"""
exports.export_generated_pfmea -- built for the fix where generate_pfmea's
tool output was getting reformatted into an ad hoc workbook (RPN column,
wrong layout) by the AGENT rather than exported through this project's own
AIAG-VDA template. These tests build GenerationResult objects directly (no
database needed) and assert on the actual .xlsx this module writes.
"""
from __future__ import annotations

import openpyxl
import pytest

from exports.export_final_pfmea import COLUMN_HEADERS, HEADER_ROW
from exports.export_generated_pfmea import (
    SOURCE_STATUS_COLUMN,
    SOURCE_STATUS_GAP,
    SOURCE_STATUS_HEADER,
    SOURCE_STATUS_VALIDATED,
    build_workbook,
    export,
)
from pfmea_agents.generation import (
    CONTENT_TYPE_VALIDATED,
    GenerationResult,
    GenerationSummary,
)


def _validated_row(row_id: str, operation: str, **overrides) -> dict:
    payload = {
        "row_id": row_id,
        "process_step": f"{operation} - some step",
        "failure_mode": "Wrong copper wire diameter",
        "failure_cause": "Deviation on supplier process",
        "failure_effect": "Incorrect turns, inductance out of spec",
        "severity": 8,
        "occurrence": 3,
        "detection": 3,
        "ap": "L",
        **overrides,
    }
    return {
        "id": 1,
        "product_family": "chokes",
        "operation": operation,
        "line_key": f"chokes|{operation}|{row_id}",
        "version": 1,
        "status": "approved",
        "approved_by": "engineer1",
        "approved_at": "2026-01-01T00:00:00",
        "line_payload": payload,
        "content_type": CONTENT_TYPE_VALIDATED,
    }


def _result_with_gap() -> GenerationResult:
    rows = [_validated_row("00.0.3", "OP10")]
    summary = GenerationSummary(
        product_family="chokes",
        operations_requested=["OP10", "OP80"],
        operations_included=["OP10"],
        operations_with_no_content=["OP80"],
        total_line_items=1,
    )
    return GenerationResult(rows=rows, summary=summary)


def test_workbook_has_no_rpn_column_anywhere():
    wb = build_workbook(_result_with_gap())
    ws = wb.active
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                assert "RPN" not in cell.value.upper()


def test_workbook_reuses_the_master_column_headers_verbatim():
    wb = build_workbook(_result_with_gap())
    ws = wb.active
    for col, label in COLUMN_HEADERS.items():
        assert ws[f"{col}{HEADER_ROW}"].value == label


def test_workbook_adds_a_source_status_column_after_the_master_columns():
    wb = build_workbook(_result_with_gap())
    ws = wb.active
    assert ws[f"{SOURCE_STATUS_COLUMN}{HEADER_ROW}"].value == SOURCE_STATUS_HEADER


def test_validated_row_is_flagged_validated_in_source_status():
    wb = build_workbook(_result_with_gap())
    ws = wb.active
    data_row = HEADER_ROW + 1
    assert ws[f"A{data_row}"].value == "00.0.3"
    assert ws[f"Q{data_row}"].value == "L"  # AP column, not RPN
    assert ws[f"{SOURCE_STATUS_COLUMN}{data_row}"].value == SOURCE_STATUS_VALIDATED


def test_operation_with_no_content_gets_a_clearly_flagged_gap_placeholder_row():
    wb = build_workbook(_result_with_gap())
    ws = wb.active
    gap_row = HEADER_ROW + 2  # one validated row, then the OP80 gap placeholder
    assert ws[f"{SOURCE_STATUS_COLUMN}{gap_row}"].value == SOURCE_STATUS_GAP
    assert "OP80" in ws[f"D{gap_row}"].value
    assert ws[f"I{gap_row}"].value is None  # no fabricated failure mode


def test_export_writes_a_file_and_returns_the_validated_row_count(tmp_path):
    out_path = tmp_path / "Generated_PFMEA_chokes.xlsx"
    n = export(_result_with_gap(), out_path)
    assert n == 1
    assert out_path.exists()
    wb = openpyxl.load_workbook(out_path)
    assert wb.sheetnames == ["02_FINAL_PFMEA"]


def test_no_gap_operations_means_no_extra_rows():
    rows = [_validated_row("00.0.3", "OP10")]
    summary = GenerationSummary(
        product_family="chokes",
        operations_requested=["OP10"],
        operations_included=["OP10"],
        operations_with_no_content=[],
        total_line_items=1,
    )
    result = GenerationResult(rows=rows, summary=summary)
    wb = build_workbook(result)
    ws = wb.active
    assert ws[f"A{HEADER_ROW + 2}"].value is None
