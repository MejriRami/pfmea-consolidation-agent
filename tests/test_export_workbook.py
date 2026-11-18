import json

import openpyxl
import pytest

from exports.export_final_pfmea import GROUND_TRUTH_DIR, HEADER_ROW, load_final_lines
from exports.export_workbook import (
    BLANK_PLACEHOLDER,
    COVER_SHEET_FIELDS,
    GROUPED_HEADER_ROW,
    SOURCE_RULES,
    TOP_RISKS_COUNT,
    add_ap_lookup_sheet,
    add_cover_sheet,
    add_kpi_dashboard_sheet,
    add_source_color_legend_sheet,
    add_sources_grouped_sheet,
    build_full_workbook,
    export,
)
from openpyxl import Workbook

FINAL_LINES = load_final_lines()
with (GROUND_TRUTH_DIR / "raw_lines.jsonl").open(encoding="utf-8") as _fh:
    RAW_LINES = [json.loads(line) for line in _fh]
AP_LOOKUP = json.loads((GROUND_TRUTH_DIR / "ap_lookup.json").read_text(encoding="utf-8"))
STATS = json.loads((GROUND_TRUTH_DIR / "stats.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def full_wb():
    return build_full_workbook(FINAL_LINES, RAW_LINES, AP_LOOKUP, STATS)


def test_all_six_sheets_present(full_wb):
    expected = {
        "FMEA COVER SHEET", "01_ALL_SOURCES_GROUPED", "02_FINAL_PFMEA",
        "04_SOURCE_COLOR_LEGEND", "12_PFMEA_KPI_Dashboard", "21_AP_LOOKUP",
    }
    assert expected == set(full_wb.sheetnames)


def test_no_process_flow_diagram_sheets_built(full_wb):
    forbidden = {
        "03_PFC_PROCESS_MAP", "03_PROCESS_DIAGRAM_MAP", "06_STATION_ANALYSIS",
        "07_CTQ_CTP_MATRIX", "08_QUALITY_GATES", "13_PFC_DIAGRAM", "14_PFC_CARTOGRAPHY",
    }
    assert forbidden.isdisjoint(full_wb.sheetnames)


def test_final_pfmea_sheet_unchanged_in_multisheet_context(full_wb):
    ws = full_wb["02_FINAL_PFMEA"]
    assert ws[f"A{HEADER_ROW}"].value == "N"
    row = HEADER_ROW + 1
    assert ws[f"A{row}"].value == "00.0.1"
    assert ws[f"AK{row}"].value == "Draft"
    assert ws.max_row - HEADER_ROW == 192


def test_workbook_marked_draft_in_properties(full_wb):
    assert full_wb.properties.keywords == "DRAFT"
    assert "DRAFT" in full_wb.properties.title


# --- cover sheet -----------------------------------------------------------

def test_cover_sheet_shows_draft_status():
    wb = Workbook()
    ws = add_cover_sheet(wb)
    values = [cell.value for row in ws.iter_rows() for cell in row if cell.value]
    assert any("Document Status: DRAFT" in str(v) for v in values)


def test_cover_sheet_project_fields_are_blank_placeholders_not_fabricated():
    wb = Workbook()
    ws = add_cover_sheet(wb)
    values = " ".join(str(cell.value) for row in ws.iter_rows() for cell in row if cell.value)
    for real_value in ("PLANT 1", "Customer A", "DOC-0001", "2025-05-28", "Plant 1"):
        assert real_value not in values, f"{real_value!r} looks fabricated, not a placeholder"
    assert values.count(BLANK_PLACEHOLDER) == len(COVER_SHEET_FIELDS) * 2


def test_cover_sheet_is_first_sheet(full_wb):
    assert full_wb.sheetnames[0] == "FMEA COVER SHEET"


# --- 01_ALL_SOURCES_GROUPED --------------------------------------------------

def test_sources_grouped_row_count(full_wb):
    ws = full_wb["01_ALL_SOURCES_GROUPED"]
    assert ws.max_row - GROUPED_HEADER_ROW == len(RAW_LINES) == 719


def test_sources_grouped_header_matches_master():
    wb = Workbook()
    ws = add_sources_grouped_sheet(wb, RAW_LINES[:1])
    assert ws[f"A{GROUPED_HEADER_ROW}"].value == "N"
    assert ws[f"I{GROUPED_HEADER_ROW}"].value == "2. Failure Mode (FM) of the Process Step"
    assert ws[f"AG{GROUPED_HEADER_ROW}"].value == "Source_ID"


def test_sources_grouped_mapped_final_id_never_reproduces_known_corruption(full_wb):
    """Every raw row's Mapped_Final_ID in the real master is the constant
    garbage value '184' (documented Phase 0 defect). Must be left blank,
    not copied."""
    ws = full_wb["01_ALL_SOURCES_GROUPED"]
    for row in range(GROUPED_HEADER_ROW + 1, ws.max_row + 1):
        assert ws[f"AL{row}"].value != "184"
        assert ws[f"AL{row}"].value is None


def test_sources_grouped_first_row_content(full_wb):
    ws = full_wb["01_ALL_SOURCES_GROUPED"]
    row = GROUPED_HEADER_ROW + 1
    first = RAW_LINES[0]
    assert ws[f"A{row}"].value == first["row_id"]
    assert ws[f"I{row}"].value == first["failure_mode"]
    assert ws[f"AG{row}"].value == first["source"]


# --- 04_SOURCE_COLOR_LEGEND --------------------------------------------------

def test_source_color_legend_has_actual_ground_truth_numbers():
    wb = Workbook()
    ws = add_source_color_legend_sheet(wb, STATS)
    rows = {ws.cell(row=r, column=1).value: r for r in range(2, ws.max_row + 1)}
    assert rows.keys() >= set(SOURCE_RULES) | {"UNKNOWN"}

    master_row = rows["MASTER_REF"]
    assert ws.cell(row=master_row, column=2).value == STATS["raw_by_source"]["MASTER_REF"]
    assert ws.cell(row=master_row, column=3).value == STATS["final_by_source"]["MASTER_REF"]
    assert ws.cell(row=master_row, column=4).value == STATS["retention_by_source"]["MASTER_REF"]


def test_source_color_legend_rules_match_spec_text():
    wb = Workbook()
    ws = add_source_color_legend_sheet(wb, STATS)
    rows = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=5).value for r in range(2, ws.max_row + 1)}
    assert rows["MASTER_REF"] == "Baseline. Every row preserved unconditionally."
    assert rows["SITE_D_PFC"] == "Process-flow evidence only. Never a PFMEA row."


def test_source_color_legend_unknown_flagged_as_defect_not_a_rule():
    wb = Workbook()
    ws = add_source_color_legend_sheet(wb, STATS)
    rows = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=5).value for r in range(2, ws.max_row + 1)}
    assert "defect" in rows["UNKNOWN"].lower()


# --- 12_PFMEA_KPI_Dashboard --------------------------------------------------

def test_kpi_dashboard_ap_distribution_sums_to_total(full_wb):
    ws = full_wb["12_PFMEA_KPI_Dashboard"]
    counts = []
    for row in ws.iter_rows(min_row=6, max_row=10, min_col=2, max_col=3, values_only=True):
        label, count = row
        if label in ("H", "M", "L", "(missing/other)"):
            counts.append(count)
    assert sum(counts) == len(FINAL_LINES)


def test_kpi_dashboard_source_breakdown_sums_to_total():
    wb = Workbook()
    ws = add_kpi_dashboard_sheet(wb, FINAL_LINES)
    values = list(ws.iter_rows(values_only=True))
    # Locate the "Source Breakdown" table and sum its Count column.
    header_idx = next(i for i, row in enumerate(values) if row and row[1] == "Source Breakdown")
    total = 0
    for row in values[header_idx + 2:]:
        if not row or row[1] is None:
            break
        total += row[2]
    assert total == len(FINAL_LINES)


def test_kpi_dashboard_computed_directly_from_final_lines_not_fabricated():
    from collections import Counter
    wb = Workbook()
    ws = add_kpi_dashboard_sheet(wb, FINAL_LINES)
    expected_ap = Counter(r.get("ap") for r in FINAL_LINES)
    values = list(ws.iter_rows(values_only=True))
    header_idx = next(i for i, row in enumerate(values) if row and row[1] == "AP Distribution")
    for row in values[header_idx + 2:header_idx + 5]:
        ap, count, _share = row[1], row[2], row[3]
        if ap in ("H", "M", "L"):
            assert count == expected_ap.get(ap, 0)


def _find_table(ws, title_prefix: str) -> list[tuple]:
    values = list(ws.iter_rows(values_only=True))
    header_idx = next(i for i, row in enumerate(values) if row and row[1] and str(row[1]).startswith(title_prefix))
    rows = []
    for row in values[header_idx + 2:]:
        if not row or row[1] is None:
            break
        rows.append(row)
    return rows


def test_ap_change_reports_zero_when_no_revised_ratings_present():
    # None of the real final_lines rows carry revised_ap/revised_severity/etc
    # (export_final_pfmea.py's own docstring: out of Phase 0's extraction
    # scope) -- this must report that honestly, not fabricate a distribution.
    wb = Workbook()
    ws = add_kpi_dashboard_sheet(wb, FINAL_LINES)
    rows = _find_table(ws, "AP Change")
    assert rows[0][1] == "Rows with a revised rating"
    assert rows[0][2] == 0


def test_ap_change_computes_real_deltas_when_revised_ap_present():
    rows_in = [
        {"row_id": "R1", "operation": "OP10", "ap": "H", "revised_ap": "L"},   # improved
        {"row_id": "R2", "operation": "OP10", "ap": "L", "revised_ap": "H"},   # worsened
        {"row_id": "R3", "operation": "OP10", "ap": "M", "revised_ap": "M"},   # unchanged
        {"row_id": "R4", "operation": "OP10", "ap": "H"},                      # no revised data at all
    ]
    wb = Workbook()
    ws = add_kpi_dashboard_sheet(wb, rows_in)
    rows = _find_table(ws, "AP Change")
    as_dict = {r[1]: r[2] for r in rows}
    assert as_dict["Rows with a revised rating"] == 3
    assert as_dict["Improved (lower AP)"] == 1
    assert as_dict["Unchanged"] == 1
    assert as_dict["Worsened (higher AP)"] == 1
    assert as_dict["H -> L"] == 1
    assert as_dict["L -> H"] == 1


def test_ap_change_derives_revised_ap_from_revised_sod_via_lookup_table():
    # S=2,O=1,D=1 -> L per the real AP lookup table; starting AP is H, so
    # this must count as improved even though revised_ap itself was never given.
    from rules.ap_lookup import get_ap
    derived = get_ap(2, 1, 1)
    rows_in = [{
        "row_id": "R1", "operation": "OP10", "ap": "H",
        "revised_severity": 2, "revised_occurrence": 1, "revised_detection": 1,
    }]
    wb = Workbook()
    ws = add_kpi_dashboard_sheet(wb, rows_in)
    rows = _find_table(ws, "AP Change")
    as_dict = {r[1]: r[2] for r in rows}
    assert as_dict["Rows with a revised rating"] == 1
    assert as_dict[f"H -> {derived}"] == 1


def test_top_risks_ranked_by_ap_then_severity_occurrence_detection():
    rows_in = [
        {"row_id": "LOW", "operation": "OP10", "failure_mode": "low risk", "ap": "L", "severity": 9, "occurrence": 9, "detection": 9},
        {"row_id": "HIGH-1", "operation": "OP10", "failure_mode": "high risk a", "ap": "H", "severity": 5, "occurrence": 5, "detection": 5},
        {"row_id": "HIGH-2", "operation": "OP10", "failure_mode": "high risk b", "ap": "H", "severity": 9, "occurrence": 1, "detection": 1},
        {"row_id": "NO-AP", "operation": "OP10", "failure_mode": "unrated", "ap": None, "severity": 10, "occurrence": 10, "detection": 10},
    ]
    wb = Workbook()
    ws = add_kpi_dashboard_sheet(wb, rows_in)
    rows = _find_table(ws, "Top ")
    row_ids = [r[1] for r in rows]
    # Both H rows outrank the L row regardless of S/O/D; among the two H
    # rows, higher severity (9 > 5) breaks the tie; the unrated row is excluded.
    assert row_ids == ["HIGH-2", "HIGH-1", "LOW"]


def test_top_risks_limited_to_top_n_count():
    rows_in = [
        {"row_id": f"R{i}", "operation": "OP10", "failure_mode": "x", "ap": "H", "severity": i, "occurrence": 1, "detection": 1}
        for i in range(TOP_RISKS_COUNT + 10)
    ]
    wb = Workbook()
    ws = add_kpi_dashboard_sheet(wb, rows_in)
    rows = _find_table(ws, "Top ")
    assert len(rows) == TOP_RISKS_COUNT
    # Highest severity values (most recently generated ids) should win.
    assert rows[0][1] == f"R{TOP_RISKS_COUNT + 9}"


# --- 21_AP_LOOKUP -----------------------------------------------------------

def test_ap_lookup_sheet_has_all_1000_entries(full_wb):
    ws = full_wb["21_AP_LOOKUP"]
    assert ws.max_row - 1 == 1000 == len(AP_LOOKUP)


def test_ap_lookup_sheet_values_match_ground_truth_exactly(full_wb):
    ws = full_wb["21_AP_LOOKUP"]
    for row in ws.iter_rows(min_row=2, values_only=True):
        s, o, d, ap, key, method = row
        assert AP_LOOKUP[key] == ap
        assert key == f"{s}-{o}-{d}"
        assert method == "AIAG/VDA AP rule mapped to H/M/L"


def test_ap_lookup_sheet_header():
    wb = Workbook()
    ws = add_ap_lookup_sheet(wb, {"1-1-1": "L"})
    assert [ws.cell(row=1, column=c).value for c in range(1, 7)] == [
        "Severity", "Occurrence", "Detection", "AP", "Key", "Method",
    ]


# --- full export -------------------------------------------------------------

def test_export_writes_real_multisheet_file(tmp_path):
    out_path = tmp_path / "full_export.xlsx"
    summary = export(out_path)
    assert summary["sheets"] == 6
    assert summary["final_pfmea_rows"] == 192
    assert summary["grouped_rows"] == 719
    assert summary["ap_lookup_rows"] == 1000
    assert out_path.exists()

    reloaded = openpyxl.load_workbook(out_path)
    assert set(reloaded.sheetnames) == {
        "FMEA COVER SHEET", "01_ALL_SOURCES_GROUPED", "02_FINAL_PFMEA",
        "04_SOURCE_COLOR_LEGEND", "12_PFMEA_KPI_Dashboard", "21_AP_LOOKUP",
    }
