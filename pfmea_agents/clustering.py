"""
Phase 4, Path 2 clustering engine.

PROMOTED FROM A SCRATCH SCRIPT, SAME ALGORITHM: this is the exact star-
clustering approach used to generate the
exports/phase4_clusters_for_review.json artifact already in this repo
(and consumed, unmodified, by exports/export_phase4_review_workbook.py) --
it just never had a home in the package before now. No API calls, no
NORMALIZER/ADJUDICATOR, no schema, no algorithm here is new; this module
only gives that logic an importable, testable, MCP-wrappable shape.

No ADJUDICATOR, no DUPLICATE/ENRICHMENT call at all. For each operation,
cluster same-operation rows (baseline + candidates together, so a human
sees at a glance which cluster members are the already-known baseline
entry) by similarity of NORMALIZED ROOT CAUSE embeddings. An edge connects
two rows in the same operation when their cause-embedding cosine
similarity clears a LOOSE threshold, favoring recall over purity -- a
human filters the cluster, so over-inclusion costs review time, not
correctness.

STAR clustering, anchored on baseline rows -- NOT connected-components/
single-linkage: a first attempt at that chained transitively through
intermediate rows and collapsed entire busy operations into one 100+-row
blob, useless for a human to review. Instead: each baseline row anchors
its own cluster; a candidate joins the cluster of its SINGLE best-matching
baseline row, only if that best match clears the threshold. No candidate-
candidate edges at all, so no chaining is possible.

THRESHOLD: never fabricated. Either the caller supplies one explicitly,
or (default) it's calibrated live from ground_truth/decisions.jsonl -- the
25th percentile of the true-duplicate best-same-operation-similarity
distribution, i.e. loose enough that ~75% of true duplicates clear it.
Calibration needs an answer key; a deployment with genuinely new files and
no ground truth to check against must pass an explicit threshold.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Awaitable, Callable

from rules.operations import resolve_operation

from .embeddings import cosine_similarity, embed_texts_async
from .normalizer import is_plain_ascii, run_normalizer_async
from .pipeline import BASELINE_SOURCE, PFC_SOURCES

GROUND_TRUTH_DIR = Path(__file__).resolve().parent.parent / "ground_truth"
EMBEDDING_MODEL = "text-embedding-3-small"
CONCURRENCY = 12
CALIBRATION_PERCENTILE = 0.25

NormalizeEnglishFn = Callable[[str], Awaitable[str]]
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
ProgressFn = Callable[[float, float, str], Awaitable[None]]  # (progress, total, message)


async def _report(on_progress: ProgressFn | None, progress: float, total: float, message: str) -> None:
    if on_progress is not None:
        await on_progress(progress, total, message)


async def _default_normalize_english(text: str) -> str:
    if not text or is_plain_ascii(text):
        return text
    result = await run_normalizer_async(text)
    return result.english


def _row_view(r: dict) -> dict:
    return {
        "row_id": r["row_id"], "source": r["source"],
        "failure_mode": r.get("failure_mode"), "failure_cause": r.get("failure_cause"),
        "failure_effect": r.get("failure_effect"),
    }


def calibrate_threshold(
    clusterable_rows: list[dict],
    op_by_row: dict[str, str | None],
    vec_by_row: dict[str, list[float]],
    ground_truth_decisions: list[dict],
    *,
    percentile: float = CALIBRATION_PERCENTILE,
) -> float:
    """Pure function, no API calls: given already-computed cause vectors
    and a ground-truth decisions list (shaped like decisions.jsonl -- each
    dict needs at least source/row_id/decision), returns the `percentile`
    of the true-duplicate best-same-operation-similarity distribution."""
    gt_by_key = {(d["source"], d["row_id"]): d for d in ground_truth_decisions}
    by_op: dict[str, list[dict]] = defaultdict(list)
    for r in clusterable_rows:
        by_op[op_by_row[r["row_id"]]].append(r)

    dup_scores = []
    for _op, rows in by_op.items():
        baselines_here = [r for r in rows if r["source"] == BASELINE_SOURCE]
        if not baselines_here:
            continue
        for r in rows:
            if r["source"] == BASELINE_SOURCE:
                continue
            gt = gt_by_key.get((r["source"], r["row_id"]))
            if not gt or gt.get("decision") != "EXCLUDE_DUPLICATE":
                continue
            best = max(cosine_similarity(vec_by_row[r["row_id"]], vec_by_row[b["row_id"]]) for b in baselines_here)
            dup_scores.append(best)

    if not dup_scores:
        raise ValueError(
            "no EXCLUDE_DUPLICATE rows with a same-operation baseline found in ground_truth_decisions -- "
            "cannot calibrate a threshold from this data; pass `threshold` explicitly instead"
        )
    dup_scores.sort()
    idx = min(int(len(dup_scores) * percentile), len(dup_scores) - 1)
    return dup_scores[idx]


def _star_cluster(by_op: dict[str, list[dict]], vec_by_row: dict[str, list[float]], threshold: float) -> list[dict]:
    clusters_by_op: dict[str, list[list[dict]]] = {}
    for op, rows in by_op.items():
        baselines_here = [r for r in rows if r["source"] == BASELINE_SOURCE]
        candidates_here = [r for r in rows if r["source"] != BASELINE_SOURCE]
        cluster_members: dict[str, list[dict]] = {b["row_id"]: [b] for b in baselines_here}

        for c in candidates_here:
            if not baselines_here:
                continue
            best_baseline, best_sim = max(
                ((b, cosine_similarity(vec_by_row[c["row_id"]], vec_by_row[b["row_id"]])) for b in baselines_here),
                key=lambda pair: pair[1],
            )
            if best_sim >= threshold:
                cluster_members[best_baseline["row_id"]].append(c)

        # A cluster is only "real" once a baseline has >=1 matching candidate.
        clusters_by_op[op] = [members for members in cluster_members.values() if len(members) > 1]

    output = []
    for op, clusters in sorted(clusters_by_op.items()):
        for c in clusters:
            output.append({
                "operation": op, "size": len(c),
                "has_baseline": any(r["source"] == BASELINE_SOURCE for r in c),
                "rows": [_row_view(r) for r in c],
            })
    output.sort(key=lambda c: (-c["size"], c["operation"]))
    return output


async def cluster_raw_rows(
    raw_rows: list[dict],
    *,
    threshold: float | None = None,
    ground_truth_decisions: list[dict] | None = None,
    normalize_english: NormalizeEnglishFn | None = None,
    embed: EmbedFn | None = None,
    concurrency: int = CONCURRENCY,
    embedding_model: str = EMBEDDING_MODEL,
    on_progress: ProgressFn | None = None,
) -> tuple[list[dict], float]:
    """The full clustering pass: resolve operation, normalize + embed each
    clusterable row's failure_cause, then star-cluster by same-operation
    embedding similarity. raw_rows are dicts shaped like
    ground_truth/raw_lines.jsonl (need at least: row_id, source,
    process_step, failure_cause; failure_mode/failure_effect are carried
    through for display only).

    normalize_english/embed default to the real OpenAI-backed functions
    (pfmea_agents.normalizer.run_normalizer_async /
    pfmea_agents.embeddings.embed_texts_async) but are injectable so
    offline tests never need an API key -- same pattern as
    pfmea_agents.pipeline.replay's NormalizeFn/AdjudicateFn.

    on_progress, if given, is awaited as (step, total_steps, message) at
    each stage boundary (resolve operations -> normalize -> embed ->
    cluster). This is REPORTING only -- MCP progress notifications don't
    reset a client's response-wait timeout (the underlying JSON-RPC
    request/response wait is a single fixed `anyio.fail_after`, unaffected
    by intervening notifications; see mcp.shared.session.BaseSession.send_request).
    A client expecting to run this on hundreds of rows needs a long enough
    session timeout regardless of whether progress is reported.

    Returns (clusters, threshold_used). threshold_used echoes back
    whatever was actually applied, including a live-calibrated value when
    `threshold` wasn't given."""
    normalize_english = normalize_english or _default_normalize_english
    embed = embed or (lambda texts: embed_texts_async(texts, model=embedding_model))

    await _report(on_progress, 0, 4, f"resolving operations for {len(raw_rows)} rows")
    non_pfc = [r for r in raw_rows if r["source"] not in PFC_SOURCES]
    op_by_row = {r["row_id"]: resolve_operation(r.get("process_step") or "").code for r in non_pfc}
    clusterable = [r for r in non_pfc if op_by_row[r["row_id"]] is not None]

    await _report(on_progress, 1, 4, f"normalizing failure_cause for {len(clusterable)} clusterable rows")
    semaphore = asyncio.Semaphore(concurrency)

    async def _to_english(text: str) -> str:
        async with semaphore:
            return await normalize_english(text)

    cause_en = await asyncio.gather(*(_to_english(r.get("failure_cause") or "") for r in clusterable))
    cause_en_by_row = {r["row_id"]: en for r, en in zip(clusterable, cause_en)}

    row_ids = [r["row_id"] for r in clusterable]
    texts = [cause_en_by_row[rid].strip().lower() or "(no cause given)" for rid in row_ids]

    await _report(on_progress, 2, 4, f"embedding {len(texts)} normalized causes")
    vectors = await embed(texts)
    vec_by_row = dict(zip(row_ids, vectors))

    if threshold is None:
        if ground_truth_decisions is None:
            raise ValueError(
                "no `threshold` given and no `ground_truth_decisions` to calibrate one from -- "
                "a deployment with no ground-truth answer key must pass an explicit similarity_threshold"
            )
        threshold = calibrate_threshold(clusterable, op_by_row, vec_by_row, ground_truth_decisions)

    await _report(on_progress, 3, 4, "star-clustering by same-operation cause similarity")
    by_op: dict[str, list[dict]] = defaultdict(list)
    for r in clusterable:
        by_op[op_by_row[r["row_id"]]].append(r)

    clusters = _star_cluster(by_op, vec_by_row, threshold)
    await _report(on_progress, 4, 4, f"done -- {len(clusters)} clusters")
    return clusters, threshold


async def run_path2_clustering(
    raw_lines_paths: list[Path] | None = None,
    *,
    decisions_path: Path | None = GROUND_TRUTH_DIR / "decisions.jsonl",
    threshold: float | None = None,
    output_json_path: Path | None = None,
    output_workbook_path: Path | None = None,
    normalize_english: NormalizeEnglishFn | None = None,
    embed: EmbedFn | None = None,
    on_progress: ProgressFn | None = None,
) -> dict:
    """End to end: read raw_lines-shaped JSONL file(s) (default:
    ground_truth/raw_lines.jsonl), cluster them, write the clusters JSON,
    then build the actual review workbook via the EXISTING, unmodified
    Phase 4 exporter (exports.export_phase4_review_workbook.export) --
    the xlsx-writing logic is not touched or duplicated here.

    on_progress is forwarded to cluster_raw_rows (see its docstring for
    the caveat: progress is reporting only, it does not extend a client's
    response-wait timeout), plus reports its own two extra steps around
    reading the input file(s) and writing the review workbook."""
    from exports.export_phase4_review_workbook import DEFAULT_CLUSTERS_PATH
    from exports.export_phase4_review_workbook import export as export_review_workbook

    output_json_path = Path(output_json_path or DEFAULT_CLUSTERS_PATH)
    output_workbook_path = Path(output_workbook_path or output_json_path.parent / "Phase4_Cluster_Review.xlsx")

    paths = [Path(p) for p in (raw_lines_paths or [GROUND_TRUTH_DIR / "raw_lines.jsonl"])]
    await _report(on_progress, 0, 6, f"reading {len(paths)} raw_lines file(s)")
    raw_rows: list[dict] = []
    for p in paths:
        with p.open(encoding="utf-8") as fh:
            raw_rows.extend(json.loads(line) for line in fh)

    ground_truth_decisions = None
    if threshold is None and decisions_path is not None and Path(decisions_path).exists():
        with Path(decisions_path).open(encoding="utf-8") as fh:
            ground_truth_decisions = [json.loads(line) for line in fh]

    # cluster_raw_rows's own 5 stages (0-4) are re-scaled into steps 1-5 of
    # this function's 6, so a client watching only run_path2_clustering's
    # progress still sees smooth, monotonically increasing steps.
    async def _inner_progress(step: float, total: float, message: str) -> None:
        await _report(on_progress, 1 + (step / total) * 4, 6, message)

    clusters, threshold_used = await cluster_raw_rows(
        raw_rows, threshold=threshold, ground_truth_decisions=ground_truth_decisions,
        normalize_english=normalize_english, embed=embed,
        on_progress=_inner_progress if on_progress is not None else None,
    )

    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(clusters, indent=2, ensure_ascii=False), encoding="utf-8")

    # export_review_workbook() re-derives unclustered rows from a raw_lines
    # file path; when multiple input files were given, write their union to
    # a combined file rather than reimplementing that computation here.
    combined_raw_lines_path = output_json_path.with_name(output_json_path.stem + "_combined_raw_lines.jsonl")
    with combined_raw_lines_path.open("w", encoding="utf-8") as fh:
        for r in raw_rows:
            fh.write(json.dumps(r) + "\n")

    await _report(on_progress, 5, 6, "writing review workbook")
    summary = export_review_workbook(
        output_workbook_path, clusters_path=output_json_path, raw_lines_path=combined_raw_lines_path,
    )
    await _report(on_progress, 6, 6, "done")

    return {
        "workbook_path": str(output_workbook_path),
        "clusters_json_path": str(output_json_path),
        "threshold_used": threshold_used,
        **summary,
    }
