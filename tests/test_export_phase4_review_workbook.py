import json
import re

import openpyxl
import pytest

from exports.export_phase4_review_workbook import (
    BASELINE_ROW_FILL,
    CLUSTER_COLUMNS,
    DECISION_OPTIONS,
    UNCLUSTERED_COLUMNS,
    build_workbook,
    compute_unclustered_rows,
    export,
)

SAMPLE_CLUSTERS = [
    {
        "operation": "OP10", "size": 3, "has_baseline": True,
        "rows": [
            {"row_id": "00.0.1", "source": "MASTER_REF", "failure_mode": "Damaged packaging",
             "failure_cause": "Bad storage", "failure_effect": "Customer claim"},
            {"row_id": "SITE_B-005", "source": "SITE_B", "failure_mode": "Torn packaging",
             "failure_cause": "Bad storage handling", "failure_effect": "Claim"},
            {"row_id": "SITE_D_PFMEA_A-010", "source": "SITE_D_PFMEA", "failure_mode": "Packaging damaged",
             "failure_cause": "Poor storage", "failure_effect": "Claim"},
        ],
    },
    {
        "operation": "OP60", "size": 2, "has_baseline": True,
        "rows": [
            {"row_id": "00.0.9", "source": "MASTER_REF", "failure_mode": "Wrong turns",
             "failure_cause": "Wrong program", "failure_effect": "Reduced inductance"},
            {"row_id": "SITE_C-020", "source": "SITE_C", "failure_mode": "Incorrect turn count",
             "failure_cause": "Wrong program setting", "failure_effect": "Low inductance"},
        ],
    },
]


def make_raw_lines_file(tmp_path, extra_rows):
    clustered = [
        {"row_id": "00.0.1", "source": "MASTER_REF", "process_step": "RM incoming inspection"},
        {"row_id": "SITE_B-005", "source": "SITE_B", "process_step": "RM incoming inspection"},
        {"row_id": "SITE_D_PFMEA_A-010", "source": "SITE_D_PFMEA", "process_step": "RM incoming inspection"},
        {"row_id": "00.0.9", "source": "MASTER_REF", "process_step": "Coil winding"},
        {"row_id": "SITE_C-020", "source": "SITE_C", "process_step": "Coil winding"},
    ]
    path = tmp_path / "raw_lines.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for r in clustered + extra_rows:
            fh.write(json.dumps(r) + "\n")
    return path


def test_compute_unclustered_rows_excludes_clustered_baseline_and_pfc(tmp_path):
    extra = [
        {"row_id": "GEMBA-099", "source": "Gemba + AI Suggestions", "process_step": "Coil winding",
         "failure_mode": "New defect", "failure_cause": "New cause", "failure_effect": "New effect"},
        {"row_id": "SITE_D_PFC_A-1", "source": "SITE_D_PFC", "process_step": "Coil winding"},
    ]
    path = make_raw_lines_file(tmp_path, extra)
    unclustered = compute_unclustered_rows(SAMPLE_CLUSTERS, path)

    row_ids = {r["row_id"] for r in unclustered}
    assert row_ids == {"GEMBA-099"}  # not the clustered rows, not the PFC row, not any baseline


def test_workbook_has_two_sheets_and_is_marked_draft():
    wb = build_workbook(SAMPLE_CLUSTERS, [])
    assert wb.sheetnames == ["Clusters for Review", "Unclustered Rows"]
    assert wb.properties.keywords == "DRAFT"
    ws1 = wb["Clusters for Review"]
    ws2 = wb["Unclustered Rows"]
    assert "DRAFT" in ws1["A2"].value
    assert "DRAFT" in ws2["A2"].value


def test_clusters_sheet_shows_largest_cluster_first_as_given():
    """build_workbook trusts the input order (the caller/export() is
    responsible for sorting largest-first) -- this just checks cluster 1's
    header matches the first entry passed in."""
    wb = build_workbook(SAMPLE_CLUSTERS, [])
    ws = wb["Clusters for Review"]
    header_texts = [c[0].value for c in ws.iter_rows(min_col=1, max_col=1)
                    if c[0].value and re.match(r"^Cluster \d", str(c[0].value))]
    assert header_texts[0].startswith("Cluster 1 of 2")
    assert "OP10" in header_texts[0]
    assert header_texts[1].startswith("Cluster 2 of 2")
    assert "OP60" in header_texts[1]


def test_baseline_row_is_shaded_and_candidate_rows_are_not():
    wb = build_workbook(SAMPLE_CLUSTERS, [])
    ws = wb["Clusters for Review"]
    # locate the row containing the MASTER_REF row_id for cluster 1
    baseline_row = next(r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == "00.0.1")
    candidate_row = next(r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == "SITE_B-005")
    assert ws.cell(row=baseline_row, column=1).fill.fgColor.rgb == BASELINE_ROW_FILL.fgColor.rgb
    assert ws.cell(row=candidate_row, column=1).fill.fgColor.rgb != BASELINE_ROW_FILL.fgColor.rgb


def test_all_cluster_rows_have_engineer_decision_and_notes_columns_blank():
    wb = build_workbook(SAMPLE_CLUSTERS, [])
    ws = wb["Clusters for Review"]
    decision_col = CLUSTER_COLUMNS.index("Engineer Decision") + 1
    notes_col = CLUSTER_COLUMNS.index("Notes") + 1
    for row_id in ("00.0.1", "SITE_B-005", "SITE_D_PFMEA_A-010", "00.0.9", "SITE_C-020"):
        r = next(r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == row_id)
        assert ws.cell(row=r, column=decision_col).value is None
        assert ws.cell(row=r, column=notes_col).value is None


def test_engineer_decision_dropdown_covers_every_cluster_data_row():
    wb = build_workbook(SAMPLE_CLUSTERS, [])
    ws = wb["Clusters for Review"]
    dvs = ws.data_validations.dataValidation
    assert len(dvs) == 1
    dv = dvs[0]
    for option in DECISION_OPTIONS:
        assert option in dv.formula1

    covered_rows = set()
    for cell_range in dv.sqref.ranges:
        for row in range(cell_range.min_row, cell_range.max_row + 1):
            covered_rows.add(row)

    for row_id in ("00.0.1", "SITE_B-005", "SITE_D_PFMEA_A-010", "00.0.9", "SITE_C-020"):
        r = next(r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == row_id)
        assert r in covered_rows, f"row {row_id} (excel row {r}) not covered by the decision dropdown"


def test_unclustered_sheet_lists_operation_and_blank_notes():
    rows = [{"row_id": "GEMBA-099", "source": "Gemba + AI Suggestions", "process_step": "Coil winding",
             "failure_mode": "New defect", "failure_cause": "New cause", "failure_effect": "New effect"}]
    wb = build_workbook([], rows)
    ws = wb["Unclustered Rows"]
    header_row = next(r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == "Row ID")
    assert [ws.cell(row=header_row, column=c).value for c in range(1, len(UNCLUSTERED_COLUMNS) + 1)] == UNCLUSTERED_COLUMNS
    data_row = header_row + 1
    assert ws.cell(row=data_row, column=1).value == "GEMBA-099"
    assert ws.cell(row=data_row, column=3).value == "OP60"  # resolved from "Coil winding"
    assert ws.cell(row=data_row, column=7).value is None  # Notes blank


def test_export_writes_real_file_and_returns_counts(tmp_path):
    clusters_path = tmp_path / "clusters.json"
    clusters_path.write_text(json.dumps(SAMPLE_CLUSTERS), encoding="utf-8")
    raw_lines_path = make_raw_lines_file(tmp_path, [
        {"row_id": "GEMBA-099", "source": "Gemba + AI Suggestions", "process_step": "Coil winding",
         "failure_mode": "New defect", "failure_cause": "New cause", "failure_effect": "New effect"},
    ])
    out_path = tmp_path / "review.xlsx"

    summary = export(out_path, clusters_path=clusters_path, raw_lines_path=raw_lines_path)

    assert summary == {"clusters": 2, "clustered_rows": 5, "unclustered_rows": 1}
    assert out_path.exists()
    reloaded = openpyxl.load_workbook(out_path)
    assert reloaded.sheetnames == ["Clusters for Review", "Unclustered Rows"]


def test_real_generated_clusters_file_produces_expected_totals():
    """Cross-check against the actual Phase 4 Path 2 output already on
    disk (exports/phase4_clusters_for_review.json) -- 54 clusters, 104
    unclustered rows, as reported when the clustering was generated."""
    from exports.export_phase4_review_workbook import DEFAULT_CLUSTERS_PATH, GROUND_TRUTH_DIR

    if not DEFAULT_CLUSTERS_PATH.exists():
        pytest.skip("phase4_clusters_for_review.json not present in this checkout")

    clusters = json.loads(DEFAULT_CLUSTERS_PATH.read_text(encoding="utf-8"))
    unclustered = compute_unclustered_rows(clusters, GROUND_TRUTH_DIR / "raw_lines.jsonl")
    assert len(clusters) == 54
    assert len(unclustered) == 104
