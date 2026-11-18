"""
Offline tests for pfmea_agents/clustering.py -- the promoted Path 2
star-clustering engine. No OPENAI_API_KEY needed: `embed` is always
injected as a fake (deterministic, hand-picked vectors so similarity
relationships are known ahead of time); `normalize_english` uses its real
default, which is safe offline because every test cause string is plain
ASCII and the default short-circuits on is_plain_ascii() before ever
touching the network.
"""
from __future__ import annotations

import asyncio
import json

import openpyxl
import pytest

from pfmea_agents.clustering import calibrate_threshold, cluster_raw_rows, run_path2_clustering

# cosine_similarity([1,0], [0.9, 0.43589...]) == 0.9 exactly (both close to
# unit length); cosine_similarity([1,0], [0,1]) == 0.0 -- picked so a fixed
# threshold of 0.85 cleanly separates "close" from "far" causes below.
CAUSE_VECTORS = {
    "bad storage": [1.0, 0.0],
    "bad storage handling": [0.9, 0.43588989435406733],
    "totally unrelated issue": [0.0, 1.0],
}


async def fake_embed(texts: list[str]) -> list[list[float]]:
    return [CAUSE_VECTORS[t] for t in texts]


def make_row(row_id, source, *, process_step="RM incoming inspection", cause="bad storage", **overrides):
    row = {
        "row_id": row_id, "source": source, "process_step": process_step,
        "failure_mode": f"mode for {row_id}", "failure_cause": cause, "failure_effect": f"effect for {row_id}",
    }
    row.update(overrides)
    return row


def run(coro):
    return asyncio.run(coro)


# --- cluster_raw_rows: operation resolution & PFC exclusion ----------------

def test_pfc_rows_are_excluded_before_clustering():
    rows = [
        make_row("BASE-1", "MASTER_REF"),
        make_row("PFC-1", "SITE_D_PFC", cause="bad storage handling"),
    ]
    clusters, _ = run(cluster_raw_rows(rows, threshold=0.85, embed=fake_embed))
    all_row_ids = {r["row_id"] for c in clusters for r in c["rows"]}
    assert "PFC-1" not in all_row_ids


def test_rows_with_unresolvable_operation_are_excluded_from_clustering():
    rows = [
        make_row("BASE-1", "MASTER_REF"),
        make_row("WEIRD-1", "SITE_B", process_step="", cause="bad storage handling"),
    ]
    clusters, _ = run(cluster_raw_rows(rows, threshold=0.85, embed=fake_embed))
    all_row_ids = {r["row_id"] for c in clusters for r in c["rows"]}
    assert "WEIRD-1" not in all_row_ids


# --- star clustering behavior ------------------------------------------------

def test_candidate_above_threshold_joins_baseline_anchored_cluster():
    rows = [
        make_row("BASE-1", "MASTER_REF", cause="bad storage"),
        make_row("CLOSE-1", "SITE_B", cause="bad storage handling"),
    ]
    clusters, threshold_used = run(cluster_raw_rows(rows, threshold=0.85, embed=fake_embed))
    assert threshold_used == 0.85
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["operation"] == "OP10"
    assert cluster["has_baseline"] is True
    assert {r["row_id"] for r in cluster["rows"]} == {"BASE-1", "CLOSE-1"}


def test_candidate_below_threshold_stands_alone_not_in_any_cluster():
    rows = [
        make_row("BASE-1", "MASTER_REF", cause="bad storage"),
        make_row("FAR-1", "SITE_B", cause="totally unrelated issue"),
    ]
    clusters, _ = run(cluster_raw_rows(rows, threshold=0.85, embed=fake_embed))
    assert clusters == []  # a baseline with zero qualifying candidates isn't reported as a cluster


def test_no_candidate_candidate_edges_without_a_baseline_present():
    """Two very similar candidates in an operation with NO baseline row
    must never cluster with each other -- star clustering only anchors on
    baselines, by design (prevents the transitive chaining that made an
    earlier connected-components attempt collapse whole operations)."""
    rows = [
        make_row("CAND-1", "SITE_B", cause="bad storage"),
        make_row("CAND-2", "SITE_C", cause="bad storage handling"),
    ]
    clusters, _ = run(cluster_raw_rows(rows, threshold=0.85, embed=fake_embed))
    assert clusters == []


def test_explicit_threshold_required_without_ground_truth_to_calibrate_from():
    rows = [make_row("BASE-1", "MASTER_REF")]
    with pytest.raises(ValueError, match="ground_truth_decisions"):
        run(cluster_raw_rows(rows, embed=fake_embed))


# --- calibrate_threshold: pure function, no async/API involved --------------

def test_calibrate_threshold_picks_25th_percentile_of_true_duplicates():
    clusterable_rows = [
        {"row_id": "B", "source": "MASTER_REF"},
        {"row_id": "D1", "source": "SITE_B"}, {"row_id": "D2", "source": "SITE_B"},
        {"row_id": "D3", "source": "SITE_B"}, {"row_id": "D4", "source": "SITE_B"},
    ]
    op_by_row = {r["row_id"]: "OP10" for r in clusterable_rows}
    vec_by_row = {
        "B": [1.0, 0.0],
        "D1": [0.5, 0.8660254037844387],   # cos 0.5 vs B
        "D2": [0.6, 0.8],                  # cos 0.6 vs B
        "D3": [0.7, 0.7141428428542851],   # cos 0.7 vs B
        "D4": [0.8, 0.6],                  # cos 0.8 vs B
    }
    ground_truth_decisions = [
        {"source": "SITE_B", "row_id": "D1", "decision": "EXCLUDE_DUPLICATE"},
        {"source": "SITE_B", "row_id": "D2", "decision": "EXCLUDE_DUPLICATE"},
        {"source": "SITE_B", "row_id": "D3", "decision": "EXCLUDE_DUPLICATE"},
        {"source": "SITE_B", "row_id": "D4", "decision": "EXCLUDE_DUPLICATE"},
    ]
    threshold = calibrate_threshold(clusterable_rows, op_by_row, vec_by_row, ground_truth_decisions)
    assert threshold == pytest.approx(0.6)  # sorted [.5,.6,.7,.8], p25 index = int(4*.25) = 1 -> 0.6


def test_calibrate_threshold_raises_when_no_true_duplicates_present():
    clusterable_rows = [{"row_id": "B", "source": "MASTER_REF"}, {"row_id": "D1", "source": "SITE_B"}]
    op_by_row = {"B": "OP10", "D1": "OP10"}
    vec_by_row = {"B": [1.0, 0.0], "D1": [0.9, 0.1]}
    ground_truth_decisions = [{"source": "SITE_B", "row_id": "D1", "decision": "INCLUDE_ENRICHMENT"}]
    with pytest.raises(ValueError, match="no EXCLUDE_DUPLICATE"):
        calibrate_threshold(clusterable_rows, op_by_row, vec_by_row, ground_truth_decisions)


def test_cluster_raw_rows_calibrates_threshold_when_none_given_and_reports_it():
    rows = [
        make_row("BASE-1", "MASTER_REF", cause="bad storage"),
        make_row("CLOSE-1", "SITE_B", cause="bad storage handling"),
    ]
    ground_truth_decisions = [{"source": "SITE_B", "row_id": "CLOSE-1", "decision": "EXCLUDE_DUPLICATE"}]
    clusters, threshold_used = run(cluster_raw_rows(
        rows, embed=fake_embed, ground_truth_decisions=ground_truth_decisions,
    ))
    assert threshold_used == pytest.approx(0.9)  # only one true-duplicate score -> p25 == that score
    assert len(clusters) == 1


# --- run_path2_clustering: full path -> clusters.json -> review workbook ----

def test_run_path2_clustering_writes_json_and_delegates_workbook_to_existing_exporter(tmp_path):
    raw_lines_path = tmp_path / "raw_lines.jsonl"
    rows = [
        make_row("BASE-1", "MASTER_REF", cause="bad storage"),
        make_row("CLOSE-1", "SITE_B", cause="bad storage handling"),
        make_row("FAR-1", "SITE_C", cause="totally unrelated issue", process_step="Coil winding"),
    ]
    with raw_lines_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    out_json = tmp_path / "clusters.json"
    out_xlsx = tmp_path / "Phase4_Cluster_Review.xlsx"

    summary = run(run_path2_clustering(
        [raw_lines_path],
        decisions_path=None,  # no ground truth available -- force explicit threshold
        threshold=0.85,
        output_json_path=out_json,
        output_workbook_path=out_xlsx,
        embed=fake_embed,
    ))

    assert summary["workbook_path"] == str(out_xlsx)
    assert summary["threshold_used"] == 0.85
    assert summary["clusters"] == 1
    assert summary["clustered_rows"] == 2
    assert summary["unclustered_rows"] == 1  # FAR-1

    assert out_json.exists()
    clusters = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(clusters) == 1
    assert {r["row_id"] for r in clusters[0]["rows"]} == {"BASE-1", "CLOSE-1"}

    assert out_xlsx.exists()
    wb = openpyxl.load_workbook(out_xlsx)
    assert wb.sheetnames == ["Clusters for Review", "Unclustered Rows"]


def test_run_path2_clustering_requires_threshold_when_decisions_path_missing(tmp_path):
    raw_lines_path = tmp_path / "raw_lines.jsonl"
    raw_lines_path.write_text(json.dumps(make_row("BASE-1", "MASTER_REF")) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ground_truth_decisions"):
        run(run_path2_clustering(
            [raw_lines_path], decisions_path=tmp_path / "does_not_exist.jsonl", embed=fake_embed,
        ))


# --- progress reporting -------------------------------------------------------
# Progress is reporting only: it does NOT extend an MCP client's response-wait
# timeout (see the caveat in cluster_raw_rows's docstring), so these tests just
# confirm the callback fires at each stage boundary with sane, monotonic values
# -- not that it affects timing/timeout behavior in any way.

def test_cluster_raw_rows_reports_progress_through_each_stage():
    rows = [
        make_row("BASE-1", "MASTER_REF", cause="bad storage"),
        make_row("CLOSE-1", "SITE_B", cause="bad storage handling"),
    ]
    calls = []

    async def on_progress(progress, total, message):
        calls.append((progress, total, message))

    run(cluster_raw_rows(rows, threshold=0.85, embed=fake_embed, on_progress=on_progress))

    assert len(calls) == 5  # resolve, normalize, embed, cluster, done
    progresses = [c[0] for c in calls]
    assert progresses == sorted(progresses)  # monotonically non-decreasing
    assert all(total == 4 for _, total, _ in calls)
    assert calls[0][2].startswith("resolving operations")
    assert calls[-1][2] == "done -- 1 clusters"


def test_cluster_raw_rows_without_on_progress_does_not_require_a_callback():
    rows = [make_row("BASE-1", "MASTER_REF")]
    clusters, _ = run(cluster_raw_rows(rows, threshold=0.85, embed=fake_embed))
    assert clusters == []  # just confirms no error when on_progress is omitted


def test_run_path2_clustering_reports_progress_including_file_io_steps(tmp_path):
    raw_lines_path = tmp_path / "raw_lines.jsonl"
    rows = [
        make_row("BASE-1", "MASTER_REF", cause="bad storage"),
        make_row("CLOSE-1", "SITE_B", cause="bad storage handling"),
    ]
    with raw_lines_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    calls = []

    async def on_progress(progress, total, message):
        calls.append((progress, total, message))

    run(run_path2_clustering(
        [raw_lines_path], decisions_path=None, threshold=0.85,
        output_json_path=tmp_path / "clusters.json", output_workbook_path=tmp_path / "review.xlsx",
        embed=fake_embed, on_progress=on_progress,
    ))

    assert all(total == 6 for _, total, _ in calls)
    progresses = [c[0] for c in calls]
    assert progresses == sorted(progresses)
    assert calls[0][2].startswith("reading")
    assert calls[-1] == (6, 6, "done")
