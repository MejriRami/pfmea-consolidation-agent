"""
Phase 4 replay benchmark (spec §5.4 / §8): "ADJUDICATOR + the replay
benchmark — Done when: 729 -> ~202, ENRICHMENT recall >= 0.95."

THIS IS THE GO/NO-GO GATE (spec §8): "If replay fails, the approach is
wrong and no amount of UI work fixes it." Runs the full pipeline (Layer 1
operation resolution + NORMALIZER + ADJUDICATOR) over every row in
ground_truth/raw_lines.jsonl and compares the resulting decision for each
row against ground_truth/decisions.jsonl.

NO MECHANICAL PRE-FILTER (see pipeline.py's module docstring for the full
history): exact dedup-key matching, embedding similarity, and
operation+theme category membership were each tried and each failed for
structural reasons. Every candidate is now compared against every
same-operation baseline row in ONE ADJUDICATOR call — no dedup-key
computation, no THEMER call, no embeddings anywhere in this script.

NOTE ON THE 729/202 VS 719/192 DISCREPANCY: the spec's own header figures
(§0, §3.1) predate Phase 0's precise extraction. PHASE_0_RESULTS.md's
actual counts — 719 raw, 192 final — are what ground_truth/ contains and
what this benchmark targets; "~202" in the done-when bar should be read as
"~192" against this ground truth.

FOUR GATES REPORTED:
  1. ENRICHMENT recall               >= 0.95
  2. Total output rows (INCLUDE_*)   185-200
  3. MASTER_REF retention            == 1.000
  4. SITE_D_PFC retention                == 0.000

COST: every candidate with at least one same-operation baseline gets
exactly one ADJUDICATOR call showing that ENTIRE baseline set (up to 21
rows for OP10 — see calibrate_dedup_cost.py for the full distribution).
Run that cost-estimation script BEFORE this one on a fresh redesign; it
prints projected token counts without spending anything beyond the
NORMALIZER calls it needs for realistic prompt sizing.

Requires OPENAI_API_KEY. Use --limit-candidates for a cheap smoke test
before committing to a full run.

Usage:
    python -m pfmea_agents.eval_replay
    python -m pfmea_agents.eval_replay --limit-candidates 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path

from agents.usage import Usage
from rules.operations import resolve_operation

from .adjudicator import (
    AdjudicatorInput,
    AdjudicatorOutput,
    BaselineRow,
    CandidateRow,
    run_adjudicator_async,
)
from .normalizer import is_plain_ascii, run_normalizer_async
from .pipeline import BASELINE_SOURCE, PFC_SOURCES, replay

GROUND_TRUTH_DIR = Path(__file__).resolve().parent.parent / "ground_truth"
GROUND_TRUTH_INCLUDE_DECISIONS = {"INCLUDE_BASELINE", "INCLUDE_ENRICHMENT"}
DEFAULT_CONCURRENCY = 12
TARGET_TOTAL_ROWS_RANGE = (185, 200)


class UsageTotals:
    """Accumulates REAL measured token usage across every NORMALIZER/
    ADJUDICATOR call in a run — not an estimate from prompt text. Safe
    under asyncio's single-threaded cooperative concurrency (no actual
    parallel mutation, just interleaved coroutines)."""

    def __init__(self) -> None:
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.reasoning_tokens = 0
        self.cached_input_tokens = 0

    def add(self, usage: Usage) -> None:
        self.requests += usage.requests
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.reasoning_tokens += usage.output_tokens_details.reasoning_tokens
        self.cached_input_tokens += getattr(usage.input_tokens_details, "cached_tokens", 0) or 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# Neither "gpt-5-nano"/"gpt-5-mini" nor plain "gpt-5" are on OpenAI's
# current official pricing page (developers.openai.com/api/docs/pricing) —
# all three were superseded by the gpt-5.4/5.5/5.6 family, but remained
# directly callable when tested. These are legacy price points from an
# unofficial aggregator (pricepertoken.com), not OpenAI's own current
# page — shown for a real cost figure, flagged as unconfirmed against the
# model actually being billed.
PRICE_PER_MILLION_TOKENS_USD: dict[str, tuple[float, float]] = {  # (input, output)
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (0.63, 5.00),
}


def _check_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. NORMALIZER/ADJUDICATOR run on OpenAI "
            "(spec section 2, Layer 2) and cannot be evaluated without it.\n"
            "Set it and re-run:\n"
            '  $env:OPENAI_API_KEY = "sk-..."   (PowerShell)\n'
            "  python -m pfmea_agents.eval_replay"
        )


def _load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _limit_candidates(raw_rows: list[dict], limit: int | None) -> list[dict]:
    if limit is None:
        return raw_rows
    baseline = [r for r in raw_rows if r["source"] == BASELINE_SOURCE]
    pfc = [r for r in raw_rows if r["source"] in PFC_SOURCES]
    other = [r for r in raw_rows if r["source"] != BASELINE_SOURCE and r["source"] not in PFC_SOURCES]
    return baseline + pfc + other[:limit]


async def _bounded_gather(coros, *, concurrency: int):
    semaphore = asyncio.Semaphore(concurrency)

    async def run(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*(run(c) for c in coros))


async def _prefetch_normalize_cache(
    texts: set[str], *, model: str, concurrency: int, usage_totals: UsageTotals,
) -> dict[str, str]:
    texts = {t for t in texts if t}

    async def one(text: str) -> tuple[str, str]:
        if is_plain_ascii(text):
            return text, text
        result = await run_normalizer_async(text, model=model, on_usage=usage_totals.add)
        return text, result.english

    pairs = await _bounded_gather((one(t) for t in texts), concurrency=concurrency)
    return dict(pairs)


def _to_baseline_row(row: dict, normalize_cache: dict[str, str]) -> BaselineRow:
    return BaselineRow(
        row_id=row["row_id"],
        process_step=row.get("process_step") or "",
        failure_mode_en=normalize_cache.get(row.get("failure_mode") or "", row.get("failure_mode") or ""),
        failure_effect_en=normalize_cache.get(row.get("failure_effect") or "", row.get("failure_effect") or ""),
        failure_cause_en=normalize_cache.get(row.get("failure_cause") or "", row.get("failure_cause") or ""),
        severity=row.get("severity"), occurrence=row.get("occurrence"), detection=row.get("detection"),
    )


def _to_candidate_row(row: dict, operation: str, normalize_cache: dict[str, str]) -> CandidateRow:
    return CandidateRow(
        row_id=row["row_id"], source=row["source"], operation=operation,
        process_step=row.get("process_step") or "",
        failure_mode_en=normalize_cache.get(row.get("failure_mode") or "", row.get("failure_mode") or ""),
        failure_effect_en=normalize_cache.get(row.get("failure_effect") or "", row.get("failure_effect") or ""),
        failure_cause_en=normalize_cache.get(row.get("failure_cause") or "", row.get("failure_cause") or ""),
        severity=row.get("severity"), occurrence=row.get("occurrence"), detection=row.get("detection"),
    )


async def _run(args: argparse.Namespace) -> int:
    raw_rows = _load_jsonl(GROUND_TRUTH_DIR / "raw_lines.jsonl")
    decisions_gt = _load_jsonl(GROUND_TRUTH_DIR / "decisions.jsonl")
    gt_by_key = {(d["source"], d["row_id"]): d for d in decisions_gt}

    rows = _limit_candidates(raw_rows, args.limit_candidates)
    non_pfc_rows = [r for r in rows if r["source"] not in PFC_SOURCES]
    baseline_rows = [r for r in non_pfc_rows if r["source"] == BASELINE_SOURCE]
    print(f"replaying {len(rows)}/{len(raw_rows)} raw rows "
          f"({'full run' if args.limit_candidates is None else f'limited to {args.limit_candidates} candidates'}), "
          f"concurrency={args.concurrency}\n")

    # --- Phase 1: NORMALIZER for every distinct text -----------------------
    texts: set[str] = set()
    for r in non_pfc_rows:
        texts.update([r.get("failure_mode") or "", r.get("failure_cause") or "", r.get("failure_effect") or ""])
    print(f"[1/2] normalizing {len(texts)} distinct text fields...")
    usage_totals = UsageTotals()
    normalize_cache = await _prefetch_normalize_cache(
        texts, model=args.model, concurrency=args.concurrency, usage_totals=usage_totals,
    )

    # --- Determine same-operation baseline groups (free, sync) -------------
    baseline_by_operation: dict[str, list[dict]] = {}
    for r in baseline_rows:
        op = resolve_operation(r.get("process_step") or "").code
        if op is not None:
            baseline_by_operation.setdefault(op, []).append(r)

    adjudication_needed: list[tuple[dict, str, list[dict]]] = []  # (candidate, operation, same_op_baselines)
    for r in non_pfc_rows:
        if r["source"] == BASELINE_SOURCE:
            continue
        op = resolve_operation(r.get("process_step") or "").code
        if op is None:
            continue
        same_op = baseline_by_operation.get(op, [])
        if same_op:
            adjudication_needed.append((r, op, same_op))

    print(f"[2/2] adjudicating {len(adjudication_needed)} candidates "
          f"(one ADJUDICATOR call each, showing every same-operation baseline row)...")

    async def adjudicate_one(candidate: dict, operation: str, matches: list[dict]) -> tuple[str, AdjudicatorOutput]:
        data = AdjudicatorInput(
            candidate_row=_to_candidate_row(candidate, operation, normalize_cache),
            matching_baseline_rows=[_to_baseline_row(m, normalize_cache) for m in matches],
        )
        result = await run_adjudicator_async(data, model=args.model, on_usage=usage_totals.add)
        return candidate["row_id"], result

    adjudicate_pairs = await _bounded_gather(
        (adjudicate_one(c, op, m) for c, op, m in adjudication_needed), concurrency=args.concurrency,
    )
    adjudicate_cache: dict[str, AdjudicatorOutput] = dict(adjudicate_pairs)

    print("prefetch complete; replaying via pipeline.replay()...\n")

    def cached_normalize(text: str) -> str:
        return normalize_cache.get(text, text)

    def cached_adjudicate(adjudicator_input: AdjudicatorInput) -> AdjudicatorOutput:
        return adjudicate_cache[adjudicator_input.candidate_row.row_id]

    results = replay(rows, normalize=cached_normalize, adjudicate=cached_adjudicate,
                      adjudicator_threshold=args.threshold)

    # --- Score against ground truth: FOUR GATES -----------------------------
    confusion: Counter[tuple[str, str]] = Counter()
    enrichment_total = 0
    enrichment_recalled = 0
    needs_review = 0
    our_include_count = 0
    master_ref_total = master_ref_included = 0
    chn_pfc_total = chn_pfc_included = 0

    for r in results:
        gt = gt_by_key.get((r.source, r.row_id))
        gt_decision = gt["decision"] if gt else "UNKNOWN_TO_GROUND_TRUTH"
        confusion[(gt_decision, r.decision)] += 1

        if r.decision == "NEEDS_OPERATION_REVIEW":
            needs_review += 1
        is_include = r.decision in GROUND_TRUTH_INCLUDE_DECISIONS
        if is_include:
            our_include_count += 1
        if gt_decision == "INCLUDE_ENRICHMENT":
            enrichment_total += 1
            if r.decision == "INCLUDE_ENRICHMENT":
                enrichment_recalled += 1
        if r.source == BASELINE_SOURCE:
            master_ref_total += 1
            if r.decision == "INCLUDE_BASELINE":
                master_ref_included += 1
        if r.source in PFC_SOURCES:
            chn_pfc_total += 1
            if is_include:
                chn_pfc_included += 1

    recall = enrichment_recalled / enrichment_total if enrichment_total else 0.0
    master_ref_retention = master_ref_included / master_ref_total if master_ref_total else 0.0
    chn_pfc_retention = chn_pfc_included / chn_pfc_total if chn_pfc_total else 0.0

    print(f"rows this pipeline couldn't resolve (NEEDS_OPERATION_REVIEW): {needs_review}\n")

    recall_pass = recall >= 0.95
    total_pass = TARGET_TOTAL_ROWS_RANGE[0] <= our_include_count <= TARGET_TOTAL_ROWS_RANGE[1]
    master_ref_pass = master_ref_retention == 1.000
    chn_pfc_pass = chn_pfc_retention == 0.000
    all_pass = recall_pass and total_pass and master_ref_pass and chn_pfc_pass

    print("=== FOUR GATES ===")
    print(f"  [{'PASS' if recall_pass else 'FAIL'}] ENRICHMENT recall: {recall:.1%} "
          f"({enrichment_recalled}/{enrichment_total}) — target >= 95%")
    print(f"  [{'PASS' if total_pass else 'FAIL'}] Total output rows (INCLUDE_*): {our_include_count} "
          f"— target {TARGET_TOTAL_ROWS_RANGE[0]}-{TARGET_TOTAL_ROWS_RANGE[1]} "
          f"(ground truth final_rows: 192, PHASE_0_RESULTS.md — not the spec's ~202)")
    print(f"  [{'PASS' if master_ref_pass else 'FAIL'}] MASTER_REF retention: {master_ref_retention:.3f} "
          f"({master_ref_included}/{master_ref_total}) — target == 1.000")
    print(f"  [{'PASS' if chn_pfc_pass else 'FAIL'}] SITE_D_PFC retention: {chn_pfc_retention:.3f} "
          f"({chn_pfc_included}/{chn_pfc_total}) — target == 0.000")
    print(f"\n=== OVERALL: {'PASS' if all_pass else 'FAIL'} ===\n")

    print("confusion matrix (ground_truth -> ours): count")
    for (gt_dec, our_dec), count in confusion.most_common(20):
        flag = "" if gt_dec == our_dec else "  <-- mismatch"
        print(f"  {gt_dec!r:>22} -> {our_dec!r:<22} {count:>4}{flag}")

    shown_counts = [r.baseline_candidates_shown for r in results if r.baseline_candidates_shown is not None]
    if shown_counts:
        print(f"\nbaseline rows shown per ADJUDICATOR call: min={min(shown_counts)} "
              f"max={max(shown_counts)} mean={sum(shown_counts)/len(shown_counts):.1f}")

    print(f"\n=== REAL MEASURED COST (model={args.model}) ===")
    print(f"  API requests: {usage_totals.requests}")
    print(f"  input tokens:  {usage_totals.input_tokens:,} "
          f"(of which cached: {usage_totals.cached_input_tokens:,})")
    print(f"  output tokens: {usage_totals.output_tokens:,} "
          f"(of which reasoning: {usage_totals.reasoning_tokens:,})")
    print(f"  total tokens:  {usage_totals.total_tokens:,}")

    price = PRICE_PER_MILLION_TOKENS_USD.get(args.model)
    if price:
        in_price, out_price = price
        cost = (usage_totals.input_tokens / 1_000_000) * in_price + (usage_totals.output_tokens / 1_000_000) * out_price
        print(f"  cost @ ${in_price}/M in, ${out_price}/M out (unofficial/legacy price point, see "
              f"PRICE_PER_MILLION_TOKENS_USD comment): ${cost:.4f}")
    else:
        print(f"  no price point on file for {args.model!r} — token counts above are the real number either way")

    return 0 if all_pass else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--limit-candidates", type=int, default=None,
                         help="cap non-baseline, non-PFC rows processed (cost control); "
                              "all baseline rows always run, since the index needs them")
    parser.add_argument("--threshold", type=float, default=0.85,
                         help="ADJUDICATOR confidence threshold for auto-include (Phase 0 decision #4)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                         help="max API calls in flight at once (lower this if you hit rate limits)")
    args = parser.parse_args(argv)

    _check_api_key()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
