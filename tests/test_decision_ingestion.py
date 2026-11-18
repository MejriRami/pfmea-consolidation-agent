import json

import openpyxl
import pytest

from exports.export_final_pfmea import GROUND_TRUTH_DIR, load_final_lines
from exports.export_phase4_review_workbook import build_workbook as build_review_workbook
from pfmea_agents.decision_ingestion import (
    InvalidDecisionError,
    build_final_lines,
    ingest_and_export,
    read_review_workbook,
    resolve_decisions,
)

# --- a small, realistic sample cluster set, filled in as if reviewed ---------

SAMPLE_CLUSTERS = [
    {
        "operation": "OP10", "size": 4, "has_baseline": True,
        "rows": [
            {"row_id": "00.0.1", "source": "MASTER_REF", "failure_mode": "Damaged packaging",
             "failure_cause": "Bad storage", "failure_effect": "Customer claim"},
            {"row_id": "SITE_B-005", "source": "SITE_B", "failure_mode": "Torn packaging",
             "failure_cause": "Bad storage handling", "failure_effect": "Claim"},
            {"row_id": "SITE_D_PFMEA_A-010", "source": "SITE_D_PFMEA", "failure_mode": "New distinct risk",
             "failure_cause": "Different cause entirely", "failure_effect": "Different effect"},
            {"row_id": "GEMBA-001", "source": "Gemba + AI Suggestions", "failure_mode": "Ambiguous case",
             "failure_cause": "Unclear cause", "failure_effect": "Unclear effect"},
        ],
    },
]


def fill_in_decisions(wb, decisions: dict[str, str | None]):
    """decisions: row_id -> 'Merge' | 'Keep Separate' | 'Unsure' | None | 'garbage'."""
    ws = wb["Clusters for Review"]
    for row in ws.iter_rows():
        row_id_cell = row[0]
        if row_id_cell.value in decisions:
            row[5].value = decisions[row_id_cell.value]  # column F = Engineer Decision
    return wb


def make_reviewed_workbook_path(tmp_path, decisions, unclustered_notes=None):
    wb = build_review_workbook(SAMPLE_CLUSTERS, [
        {"row_id": "SITE_C-020", "source": "SITE_C", "process_step": "Coil winding",
         "failure_mode": "Standalone defect", "failure_cause": "Standalone cause",
         "failure_effect": "Standalone effect"},
    ])
    fill_in_decisions(wb, decisions)
    if unclustered_notes:
        ws = wb["Unclustered Rows"]
        for row in ws.iter_rows():
            if row[0].value == "SITE_C-020":
                row[6].value = unclustered_notes
    path = tmp_path / "reviewed.xlsx"
    wb.save(path)
    return path


# --- parsing -----------------------------------------------------------------

def test_read_review_workbook_parses_cluster_rows(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {
        "SITE_B-005": "Merge", "SITE_D_PFMEA_A-010": "Keep Separate", "GEMBA-001": "Unsure",
    })
    reviewed = read_review_workbook(path)

    by_id = {e.row_id: e for e in reviewed.cluster_rows}
    assert by_id["00.0.1"].is_baseline is True
    assert by_id["SITE_B-005"].decision == "Merge"
    assert by_id["SITE_B-005"].anchor_row_id == "00.0.1"
    assert by_id["SITE_B-005"].cluster_number == 1
    assert by_id["SITE_B-005"].operation == "OP10"
    assert by_id["SITE_D_PFMEA_A-010"].decision == "Keep Separate"
    assert by_id["GEMBA-001"].decision == "Unsure"


def test_read_review_workbook_parses_unclustered_rows(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {}, unclustered_notes="looks new to me")
    reviewed = read_review_workbook(path)

    assert len(reviewed.unclustered_rows) == 1
    entry = reviewed.unclustered_rows[0]
    assert entry.row_id == "SITE_C-020"
    assert entry.operation == "OP60"
    assert entry.notes == "looks new to me"


def test_blank_decision_parses_as_none(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {"SITE_D_PFMEA_A-010": None})
    reviewed = read_review_workbook(path)
    entry = next(e for e in reviewed.cluster_rows if e.row_id == "SITE_D_PFMEA_A-010")
    assert entry.decision is None


# --- resolving decisions -----------------------------------------------------

def test_merge_resolves_to_exclude_duplicate_absorbed_by_anchor(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {"SITE_B-005": "Merge"})
    resolved = resolve_decisions(read_review_workbook(path))
    row = next(r for r in resolved if r.row_id == "SITE_B-005")
    assert row.decision == "EXCLUDE_DUPLICATE"
    assert row.absorbed_by == "00.0.1"


def test_keep_separate_resolves_to_enrichment_unflagged(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {"SITE_D_PFMEA_A-010": "Keep Separate"})
    resolved = resolve_decisions(read_review_workbook(path))
    row = next(r for r in resolved if r.row_id == "SITE_D_PFMEA_A-010")
    assert row.decision == "INCLUDE_ENRICHMENT"
    assert row.flagged_uncertain is False


def test_unsure_resolves_to_enrichment_flagged_per_spec(tmp_path):
    """Explicit requirement: 'For Unsure rows, keep them separate and flag them.'"""
    path = make_reviewed_workbook_path(tmp_path, {"GEMBA-001": "Unsure"})
    resolved = resolve_decisions(read_review_workbook(path))
    row = next(r for r in resolved if r.row_id == "GEMBA-001")
    assert row.decision == "INCLUDE_ENRICHMENT"
    assert row.flagged_uncertain is True


def test_blank_decision_treated_same_as_unsure_not_an_error(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {"GEMBA-001": None})
    resolved = resolve_decisions(read_review_workbook(path))
    row = next(r for r in resolved if r.row_id == "GEMBA-001")
    assert row.decision == "INCLUDE_ENRICHMENT"
    assert row.flagged_uncertain is True


def test_invalid_decision_value_raises_and_names_the_row(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {"SITE_D_PFMEA_A-010": "Definitely Maybe"})
    with pytest.raises(InvalidDecisionError, match="SITE_D_PFMEA_A-010"):
        resolve_decisions(read_review_workbook(path))


def test_baseline_row_always_resolves_to_include_baseline(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {})
    resolved = resolve_decisions(read_review_workbook(path))
    row = next(r for r in resolved if r.row_id == "00.0.1")
    assert row.decision == "INCLUDE_BASELINE"


def test_unclustered_rows_included_unflagged(tmp_path):
    path = make_reviewed_workbook_path(tmp_path, {})
    resolved = resolve_decisions(read_review_workbook(path))
    row = next(r for r in resolved if r.row_id == "SITE_C-020")
    assert row.decision == "INCLUDE_ENRICHMENT"
    assert row.flagged_uncertain is False


# --- building final lines (against REAL ground truth data) ------------------

REAL_FINAL_LINES = load_final_lines()
REAL_BASELINE_ROW = next(r for r in REAL_FINAL_LINES if r["source"] == "MASTER_REF")
with (GROUND_TRUTH_DIR / "raw_lines.jsonl").open(encoding="utf-8") as _fh:
    REAL_RAW_LINES = [json.loads(l) for l in _fh]
REAL_CANDIDATE_ROW = next(r for r in REAL_RAW_LINES if r["source"] not in ("MASTER_REF", "SITE_D_PFC"))


def test_baseline_row_reused_verbatim_from_final_lines():
    from pfmea_agents.decision_ingestion import ResolvedRow

    resolved = [ResolvedRow(row_id=REAL_BASELINE_ROW["row_id"], source="MASTER_REF", decision="INCLUDE_BASELINE")]
    final_lines, exclusion_log = build_final_lines(resolved)
    assert final_lines == [REAL_BASELINE_ROW]
    assert exclusion_log == []


def test_candidate_row_reconstructed_from_raw_lines_with_rating_data():
    from pfmea_agents.decision_ingestion import ResolvedRow

    resolved = [ResolvedRow(
        row_id=REAL_CANDIDATE_ROW["row_id"], source=REAL_CANDIDATE_ROW["source"],
        decision="INCLUDE_ENRICHMENT", flagged_uncertain=False,
        cluster_number=1, anchor_row_id=REAL_BASELINE_ROW["row_id"],
    )]
    final_lines, _ = build_final_lines(resolved)
    record = final_lines[0]
    assert record["failure_mode"] == REAL_CANDIDATE_ROW["failure_mode"]
    assert record["severity"] == REAL_CANDIDATE_ROW["severity"]
    assert record["occurrence"] == REAL_CANDIDATE_ROW["occurrence"]
    assert record["detection"] == REAL_CANDIDATE_ROW["detection"]
    assert record["ap"] == REAL_CANDIDATE_ROW["ap"]
    assert record["consolidation_tag"] == "HUMAN_REVIEWED_ENRICHMENT_KEPT_SEPARATE"
    assert f"cluster 1, anchored on baseline {REAL_BASELINE_ROW['row_id']}" in record["justification"]


def test_flagged_uncertain_row_gets_visible_marker_in_tag_and_justification():
    from pfmea_agents.decision_ingestion import ResolvedRow

    resolved = [ResolvedRow(
        row_id=REAL_CANDIDATE_ROW["row_id"], source=REAL_CANDIDATE_ROW["source"],
        decision="INCLUDE_ENRICHMENT", flagged_uncertain=True,
        cluster_number=2, anchor_row_id=REAL_BASELINE_ROW["row_id"], notes="reviewer wasn't sure",
    )]
    final_lines, _ = build_final_lines(resolved)
    record = final_lines[0]
    assert record["consolidation_tag"] == "FLAGGED_UNCERTAIN_PENDING_REVIEW"
    assert "[UNCERTAIN" in record["justification"]
    assert "reviewer wasn't sure" in record["justification"]


def test_merge_produces_exclusion_log_entry_and_no_final_row():
    from pfmea_agents.decision_ingestion import ResolvedRow

    resolved = [ResolvedRow(
        row_id=REAL_CANDIDATE_ROW["row_id"], source=REAL_CANDIDATE_ROW["source"],
        decision="EXCLUDE_DUPLICATE", absorbed_by=REAL_BASELINE_ROW["row_id"],
        cluster_number=3, notes="clearly the same defect",
    )]
    final_lines, exclusion_log = build_final_lines(resolved)
    assert final_lines == []
    assert len(exclusion_log) == 1
    assert exclusion_log[0]["absorbed_by"] == REAL_BASELINE_ROW["row_id"]
    assert exclusion_log[0]["row_id"] == REAL_CANDIDATE_ROW["row_id"]


def test_unclustered_row_gets_distinct_tag_from_clustered_kept_separate():
    from pfmea_agents.decision_ingestion import ResolvedRow

    resolved = [ResolvedRow(
        row_id=REAL_CANDIDATE_ROW["row_id"], source=REAL_CANDIDATE_ROW["source"],
        decision="INCLUDE_ENRICHMENT", flagged_uncertain=False, anchor_row_id=None,
    )]
    final_lines, _ = build_final_lines(resolved)
    assert final_lines[0]["consolidation_tag"] == "NON_DUPLICATE_ENRICHMENT_UNCLUSTERED"


# --- full round trip: workbook -> resolved -> final xlsx ---------------------
# Self-contained: builds its own temp ground_truth/ so it doesn't depend on
# guessing real row_ids in the actual ground_truth files. Only the EXCLUDE_
# DUPLICATE (Merge) path skips the raw_lines.jsonl lookup entirely, so
# SITE_B-005 deliberately has no entry there -- proving that path never
# needs one.

SYNTHETIC_FINAL_LINE = {
    "row_id": "00.0.1", "source": "MASTER_REF", "process_item": "RM Reception",
    "process_step": "RM incoming inspection", "work_element": "SQA",
    "function_item": "fi", "function_step": "fs", "function_element": "fe",
    "failure_mode": "Damaged packaging", "failure_effect": "Customer claim",
    "failure_cause": "Bad storage", "prevention_control": "pc", "detection_control": "dc",
    "severity": 7, "occurrence": 3, "detection": 3, "ap": "L", "special_char": None,
    "consolidation_tag": "MASTER_REF_BASELINE_PRESERVED", "master_comparator": "BASELINE_MASTER_REFERENCE",
    "justification": "Preserved as baseline.", "source_row_refs": "MASTER_REF:00.0.1@01_ALL_SOURCES_GROUPED!R10",
    "selected_source_rows": "MASTER_REF:00.0.1@01_ALL_SOURCES_GROUPED!R10", "op_sequence_index": 1,
    "dedup_key": "OP10|packaging_material|damaged packaging",
}

SYNTHETIC_RAW_LINES = [
    {"excel_row": 50, "row_id": "SITE_D_PFMEA_A-010", "source": "SITE_D_PFMEA", "process_step": "RM incoming inspection",
     "failure_mode": "New distinct risk", "failure_cause": "Different cause entirely",
     "failure_effect": "Different effect", "severity": 8, "occurrence": 2, "detection": 4, "ap": "M"},
    {"excel_row": 60, "row_id": "GEMBA-001", "source": "Gemba + AI Suggestions", "process_step": "RM incoming inspection",
     "failure_mode": "Ambiguous case", "failure_cause": "Unclear cause", "failure_effect": "Unclear effect",
     "severity": 5, "occurrence": 4, "detection": 5, "ap": "L"},
    {"excel_row": 70, "row_id": "SITE_C-020", "source": "SITE_C", "process_step": "Coil winding",
     "failure_mode": "Standalone defect", "failure_cause": "Standalone cause",
     "failure_effect": "Standalone effect", "severity": 6, "occurrence": 3, "detection": 3, "ap": "L"},
]


def make_synthetic_ground_truth_dir(tmp_path):
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "final_lines.jsonl").write_text(json.dumps(SYNTHETIC_FINAL_LINE) + "\n", encoding="utf-8")
    with (gt_dir / "raw_lines.jsonl").open("w", encoding="utf-8") as fh:
        for r in SYNTHETIC_RAW_LINES:
            fh.write(json.dumps(r) + "\n")
    return gt_dir


def test_ingest_and_export_full_round_trip(tmp_path):
    review_path = make_reviewed_workbook_path(tmp_path, {
        "SITE_B-005": "Merge", "SITE_D_PFMEA_A-010": "Keep Separate", "GEMBA-001": "Unsure",
    })
    gt_dir = make_synthetic_ground_truth_dir(tmp_path)
    out_path = tmp_path / "final_pfmea.xlsx"
    log_path = tmp_path / "exclusion_log.json"

    summary = ingest_and_export(review_path, out_path, ground_truth_dir=gt_dir, exclusion_log_path=log_path)

    assert summary["merged_excluded"] == 1
    assert summary["kept_separate"] == 2  # SITE_D_PFMEA_A-010 (Keep Separate) + SITE_C-020 (unclustered, unflagged)
    assert summary["flagged_uncertain"] == 1  # GEMBA-001
    # baseline_rows: the sample's own 00.0.1 -- NOT the full 91-row ground
    # truth baseline set, since this test only resolves the 4 sample rows.
    assert summary["baseline_rows"] == 1
    assert summary["total_rows"] == 4  # baseline + 2 kept-separate + 1 flagged-uncertain (merge excluded)

    assert out_path.exists()
    wb = openpyxl.load_workbook(out_path)
    assert wb.sheetnames == ["02_FINAL_PFMEA"]

    assert log_path.exists()
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log[0]["row_id"] == "SITE_B-005"
    assert log[0]["absorbed_by"] == "00.0.1"
