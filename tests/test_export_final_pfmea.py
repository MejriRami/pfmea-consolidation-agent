import json

import openpyxl
import pytest

from exports.export_final_pfmea import (
    COLUMN_HEADERS,
    DRAFT_STATUS,
    HEADER_ROW,
    MASTER_COLUMN_WIDTHS,
    MASTER_ROW_HEIGHTS,
    PLANNING_HEADER,
    build_workbook,
    export,
    load_final_lines,
)

REAL_FINAL_LINES = load_final_lines()


def make_record(**overrides):
    record = {
        "row_id": "00.0.1", "row_id_column_a_untrusted": "SITE_B-034",  # deliberately different, drift scenario
        "source": "MASTER_REF", "process_item": "RM Reception", "process_step": "RM incoming inspection",
        "work_element": "SQA", "function_item": "fi", "function_step": "fs", "function_element": "fe",
        "failure_mode": "Damaged raw material packaging", "failure_effect": "Customer claim",
        "failure_cause": "Bad storage", "prevention_control": "pc", "detection_control": "dc",
        "severity": 7, "occurrence": 3, "detection": 3, "ap": "L", "special_char": None,
        "consolidation_tag": "MASTER_REF_BASELINE_PRESERVED", "master_comparator": "BASELINE_MASTER_REFERENCE",
        "justification": "Preserved as baseline.", "source_row_refs": "MASTER_REF:00.0.1@01_ALL_SOURCES_GROUPED!R10",
        "selected_source_rows": "MASTER_REF:00.0.1@01_ALL_SOURCES_GROUPED!R10", "op_sequence_index": 1,
        "dedup_key": "OP10|packaging_material|damaged raw material packaging",
    }
    record.update(overrides)
    return record


def test_header_row_matches_master_verbatim_for_key_columns():
    wb = build_workbook([make_record()])
    ws = wb.active
    assert ws[f"A{HEADER_ROW}"].value == "N"
    assert ws[f"D{HEADER_ROW}"].value == "2. Process Step       Station No. and Name of Focus Element"
    assert ws[f"I{HEADER_ROW}"].value == "2. Failure Mode (FM) of the Process Step"
    assert ws[f"K{HEADER_ROW}"].value == "Severity (S) of FE"
    assert ws[f"AG{HEADER_ROW}"].value == "Source_Basis"
    assert ws[f"AK{HEADER_ROW}"].value == "Validation_Status"
    assert ws[f"AL{HEADER_ROW}"].value == "Source_Row_Refs"
    assert ws[f"AV{HEADER_ROW}"].value == "Source_Selection_Justification"
    assert ws[f"AW{HEADER_ROW}"].value == "Duplicate_Elimination_Key"


def test_step_header_merges_match_master():
    wb = build_workbook([make_record()])
    ws = wb.active
    merged = {str(r) for r in ws.merged_cells.ranges}
    for ref in ("C8:E8", "F8:H8", "I8:L8", "M8:S8", "T8:AE8", "AP8:AW8"):
        assert ref in merged
    assert ws["C8"].value == "STRUCTURE ANALYSIS (STEP 2)"
    assert ws["AP8"].value == "SOURCE SELECTION & CONSOLIDATION PROOF"


# --- Planning & Preparation header block (rows 1-6) -- previously missing --

def test_planning_header_block_is_present_and_matches_master():
    wb = build_workbook([make_record()])
    ws = wb.active
    assert ws["G1"].value == "PFMEA (AIAG/VDA manual 1st edition 2019)          "
    assert ws["C2"].value == "PLANNING & PREPARATION (STEP 1)"
    assert ws["C3"].value == "Company Name: "
    assert ws["E3"].value == "the company"
    assert ws["N3"].value == "PFMEA ID Number: "
    assert ws["R3"].value == "DEMO-CHOKES-PFMEA-2026"
    assert ws["N5"].value == "Confidentiality Level: "
    assert ws["R5"].value == "Confidential"


def test_planning_header_merges_match_master():
    wb = build_workbook([make_record()])
    ws = wb.active
    merged = {str(r) for r in ws.merged_cells.ranges}
    for ref in (
        "C2:F2", "C3:D3", "E3:F3", "H3:I3", "J3:M3", "N3:Q3",
        "C4:D4", "E4:F4", "H4:I4", "J4:K4", "N4:Q4", "R4:V4", "L4:M4",
        "C5:D5", "E5:F5", "H5:I5", "J5:K5", "N5:Q5", "R5:V5", "L5:M5",
        "C6:D6", "E6:F6", "H6:I6", "N6:Q6", "R6:U6",
    ):
        assert ref in merged, f"expected merge {ref} (matches master) not present"


def test_planning_header_dates_are_real_datetimes():
    wb = build_workbook([make_record()])
    ws = wb.active
    assert ws["J4"].value.isoformat().startswith("2026-05-13")
    assert ws["J5"].value.isoformat().startswith("2026-06-23")


def test_planning_header_covers_every_cell_openpyxl_touched():
    """Every ref in PLANNING_HEADER must actually land on the sheet --
    guards against a typo in the dict silently dropping a cell."""
    wb = build_workbook([make_record()])
    ws = wb.active
    for ref, (value, _merge) in PLANNING_HEADER.items():
        assert ws[ref].value == value


def test_gridlines_hidden_and_data_start_unchanged():
    """Confirms the new header block didn't shift where data rows start --
    HEADER_ROW/freeze_panes stay exactly as before this fix."""
    wb = build_workbook([make_record()])
    ws = wb.active
    assert ws.sheet_view.showGridLines is False
    assert str(ws.freeze_panes) == f"A{HEADER_ROW + 1}"
    assert ws[f"A{HEADER_ROW + 1}"].value == "00.0.1"  # first data row unmoved


def test_column_widths_match_master_where_master_sets_one():
    wb = build_workbook([make_record()])
    ws = wb.active
    for letter, width in MASTER_COLUMN_WIDTHS.items():
        assert ws.column_dimensions[letter].width == width


def test_row_heights_match_master_for_header_rows():
    wb = build_workbook([make_record()])
    ws = wb.active
    for row_idx, height in MASTER_ROW_HEIGHTS.items():
        assert ws.row_dimensions[row_idx].height == height


def test_column_a_uses_al_authoritative_row_id_not_untrusted_column_a():
    """The whole point of the identity instruction: never reproduce the
    master's own column-A drift defect (PHASE_0_RESULTS.md defect #1)."""
    record = make_record(row_id="00.0.1", row_id_column_a_untrusted="SITE_B-034")
    wb = build_workbook([record])
    ws = wb.active
    assert ws[f"A{HEADER_ROW + 1}"].value == "00.0.1"
    assert ws[f"A{HEADER_ROW + 1}"].value != "SITE_B-034"


def test_source_row_refs_populated_in_column_al_independent_of_column_a():
    record = make_record(source_row_refs="MASTER_REF:00.0.1@01_ALL_SOURCES_GROUPED!R10; SUPPORT_SC:X")
    wb = build_workbook([record])
    ws = wb.active
    assert ws[f"AL{HEADER_ROW + 1}"].value == "MASTER_REF:00.0.1@01_ALL_SOURCES_GROUPED!R10; SUPPORT_SC:X"


@pytest.mark.parametrize("record_kwargs", [{}, {"source": "SITE_D_PFMEA"}, {"ap": "H"}])
def test_every_row_marked_draft_never_approved(record_kwargs):
    wb = build_workbook([make_record(**record_kwargs)])
    ws = wb.active
    assert ws[f"AK{HEADER_ROW + 1}"].value == DRAFT_STATUS
    assert ws[f"AK{HEADER_ROW + 1}"].value != "Approved"


def test_severity_occurrence_detection_are_integers():
    wb = build_workbook([make_record(severity=7, occurrence=3, detection=3)])
    ws = wb.active
    row = HEADER_ROW + 1
    assert ws[f"K{row}"].value == 7 and isinstance(ws[f"K{row}"].value, int)
    assert ws[f"N{row}"].value == 3 and isinstance(ws[f"N{row}"].value, int)
    assert ws[f"P{row}"].value == 3 and isinstance(ws[f"P{row}"].value, int)


@pytest.mark.parametrize("column", ["B", "S", "T", "U", "AH", "AI", "AJ", "AR", "AM", "AN", "AO"])
def test_out_of_scope_columns_are_blank(column):
    wb = build_workbook([make_record()])
    ws = wb.active
    assert ws[f"{column}{HEADER_ROW + 1}"].value is None


def test_all_49_columns_have_a_header():
    wb = build_workbook([make_record()])
    ws = wb.active
    assert len(COLUMN_HEADERS) == 49
    for col in COLUMN_HEADERS:
        assert ws[f"{col}{HEADER_ROW}"].value, col


def test_dedup_key_and_justification_carried_through():
    record = make_record(
        dedup_key="OP10|packaging_material|damaged raw material packaging",
        justification="Master reference row preserved as baseline.",
    )
    wb = build_workbook([record])
    ws = wb.active
    row = HEADER_ROW + 1
    assert ws[f"AW{row}"].value == "OP10|packaging_material|damaged raw material packaging"
    assert ws[f"AV{row}"].value == "Master reference row preserved as baseline."


def test_export_writes_real_file_and_returns_row_count(tmp_path):
    out_path = tmp_path / "out.xlsx"
    n = export(out_path)
    assert n == len(REAL_FINAL_LINES)
    assert out_path.exists()

    reloaded = openpyxl.load_workbook(out_path)
    ws = reloaded[reloaded.sheetnames[0]]
    assert ws.title == "02_FINAL_PFMEA"


def test_real_ground_truth_has_192_rows():
    assert len(REAL_FINAL_LINES) == 192


def test_real_ground_truth_row_10_matches_expected_content():
    """Spot-check against ground_truth/final_lines.jsonl's actual first
    record (excel_row 10 in the master, row_id 00.0.1)."""
    wb = build_workbook(REAL_FINAL_LINES)
    ws = wb.active
    first = REAL_FINAL_LINES[0]
    assert first["row_id"] == "00.0.1"
    row = HEADER_ROW + 1
    assert ws[f"A{row}"].value == "00.0.1"
    assert ws[f"I{row}"].value == "Damaged raw material packaging"
    assert ws[f"AK{row}"].value == "Draft"


def test_real_ground_truth_full_export_has_no_untrusted_column_a_leak():
    """None of the 55 id-drift-affected rows should leak
    row_id_column_a_untrusted into column A anywhere in a full export."""
    wb = build_workbook(REAL_FINAL_LINES)
    ws = wb.active
    for i, record in enumerate(REAL_FINAL_LINES):
        row = HEADER_ROW + 1 + i
        if record.get("id_drift_detected"):
            assert ws[f"A{row}"].value == record["row_id"]
            assert ws[f"A{row}"].value != record["row_id_column_a_untrusted"]
