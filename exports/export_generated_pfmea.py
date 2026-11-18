"""
Export engine for pfmea_agents.generation.generate_pfmea's output (Layer 1:
a NEW PFMEA assembled from already-approved standard_line content).

WHY THIS FILE EXISTS: before this, generate_pfmea only returned row dicts
as MCP tool JSON, and the agent formatted them into a table/workbook itself
from its own general PFMEA knowledge -- which is how an RPN column and a
layout that didn't match the AIAG-VDA master ever showed up in a "generated
PFMEA" (this project uses AP throughout, never RPN; see
pfmea_agents/generation.py and exports/export_final_pfmea.py, neither of
which has ever computed RPN). The fix is for the TOOL to produce the actual
.xlsx file, using the exact same 02_FINAL_PFMEA structure as the master
export -- same header rows, same column order, same AP-only rating columns
-- imported directly from exports.export_final_pfmea rather than
retyped, so the two exporters cannot structurally drift apart. The agent's
job becomes presenting/returning the file this module writes, never
building one of its own (see run_agent.py's INSTRUCTIONS).

ONE ADDITION beyond the master's own columns: "Source Status" (column AX,
just past the master's own A:AW). generate_pfmea's rows are a mix of
proven, already-approved content and -- per operations_with_no_content --
gaps where nothing approved exists yet for a requested operation. The
master format has no column for that distinction (it was never designed to
describe itself), so this exporter adds one rather than overloading column
AK (Validation_Status), which already carries a different, document-level
meaning (this whole freshly-assembled document is "Draft" until a human
approves it -- see GENERATED_DOCUMENT_STATUS) that a per-row content
distinction would confuse.

GAP ROWS: an operation the user asked for with zero approved standard_line
content still gets ONE placeholder row in the output -- clearly flagged via
Source Status rather than silently missing from the sheet -- so a reviewer
sees exactly which operations need engineering input, the same way this
project already flags gaps elsewhere (spec: "present weak or missing
controls as review comments ... not as automatically validated standard
content").

ALSO IN THIS FILE: build_ai_draft_workbook/export_ai_draft, the equivalent
exporter for pfmea_agents.generation.draft_pfmea_from_ai_suggestions --
same AIAG-VDA structure and Source Status column, but for a product_family
with NO validated content at all (every row content_type=AI_SUGGESTION,
S/O/D/AP columns intentionally blank, and an unmissable "unvalidated AI
draft" banner in row 7). See that function/module for why it exists.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from exports.export_final_pfmea import (
    COLUMN_HEADERS,
    HEADER_ROW,
    MASTER_COLUMN_WIDTHS,
    MASTER_ROW_HEIGHTS,
    SHEET_NAME,
    STEP_HEADERS,
    _cell_value,
    _write_planning_header,
)
from pfmea_agents.generation import (
    AIDraftResult,
    CONTENT_TYPE_AI_SUGGESTION,
    CONTENT_TYPE_VALIDATED,
    GenerationResult,
)
from rules.operations import OPERATIONS

_OPERATION_BY_CODE = {op.code: op for op in OPERATIONS}

SOURCE_STATUS_COLUMN = "AX"
SOURCE_STATUS_HEADER = "Source Status"

SOURCE_STATUS_VALIDATED = "VALIDATED - Approved Standard Content"
SOURCE_STATUS_AI_SUGGESTION = "AI SUGGESTION - Not Validated, Requires Engineer Review"
SOURCE_STATUS_GAP = "DRAFT - NO VALIDATED CONTENT (NEEDS ENGINEERING INPUT)"

_SOURCE_STATUS_BY_CONTENT_TYPE = {
    CONTENT_TYPE_VALIDATED: SOURCE_STATUS_VALIDATED,
    CONTENT_TYPE_AI_SUGGESTION: SOURCE_STATUS_AI_SUGGESTION,
}

_GAP_REMARK = (
    "No approved standard_line content exists yet for this operation. "
    "Add engineering input before finalizing this PFMEA."
)


def _operation_label(operation: str) -> str:
    op = _OPERATION_BY_CODE.get(operation)
    if op is None:
        return operation
    return f"{operation} - {op.name}"


def _gap_placeholder_record(operation: str) -> dict:
    """A record shaped like a line_payload (same field names _cell_value
    reads), with only the fields needed to make a gap visible on the sheet
    filled in -- everything else stays blank rather than fabricated."""
    return {
        "row_id": None,
        "process_step": _operation_label(operation),
        "justification": _GAP_REMARK,
    }


def _iter_export_rows(result: GenerationResult):
    """Yields (record, source_status_label) pairs in output order: the
    already-ordered validated rows first, then one gap placeholder row per
    operation in operations_with_no_content, in the order generate_pfmea
    reported them."""
    for row in result.rows:
        record = row.get("line_payload") or {}
        label = _SOURCE_STATUS_BY_CONTENT_TYPE.get(row.get("content_type"), SOURCE_STATUS_VALIDATED)
        yield record, label
    for operation in result.summary.operations_with_no_content:
        yield _gap_placeholder_record(operation), SOURCE_STATUS_GAP


def _write_headers(ws: Worksheet) -> None:
    header_font = Font(bold=True)
    header_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")

    _write_planning_header(ws)

    for ref, label in STEP_HEADERS.items():
        if ":" in ref:
            start, _ = ref.split(":")
            ws.merge_cells(ref)
            cell = ws[start]
        else:
            cell = ws[ref]
        cell.value = label
        cell.font = header_font

    for col, label in COLUMN_HEADERS.items():
        cell = ws[f"{col}{HEADER_ROW}"]
        cell.value = label
        cell.font = header_font
        cell.fill = header_fill

    status_cell = ws[f"{SOURCE_STATUS_COLUMN}{HEADER_ROW}"]
    status_cell.value = SOURCE_STATUS_HEADER
    status_cell.font = header_font
    status_cell.fill = header_fill


def build_workbook(result: GenerationResult) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    _write_headers(ws)

    excel_row = HEADER_ROW
    for record, source_status in _iter_export_rows(result):
        excel_row += 1
        for col in COLUMN_HEADERS:
            ws[f"{col}{excel_row}"] = _cell_value(record, col)
        ws[f"{SOURCE_STATUS_COLUMN}{excel_row}"] = source_status

    ws.freeze_panes = ws[f"A{HEADER_ROW + 1}"]
    ws.sheet_view.showGridLines = False

    for col_idx in range(1, len(COLUMN_HEADERS) + 1):
        letter = get_column_letter(col_idx)
        if letter in MASTER_COLUMN_WIDTHS:
            ws.column_dimensions[letter].width = MASTER_COLUMN_WIDTHS[letter]
    ws.column_dimensions[SOURCE_STATUS_COLUMN].width = 42.0

    for row_idx, height in MASTER_ROW_HEIGHTS.items():
        ws.row_dimensions[row_idx].height = height

    return wb


def export(result: GenerationResult, output_path: Path) -> int:
    wb = build_workbook(result)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return len(result.rows)


# =========== Whole-document AI draft (pfmea_agents.generation.draft_pfmea_from_ai_suggestions) ===========
# For a product_family with no (or only partial) approved standard_line
# content -- generate_pfmea has nothing, or not enough, to assemble. Same
# AIAG-VDA structure and Source Status column as build_workbook above, but
# EVERY row is content_type=AI_SUGGESTION (never blended with VALIDATED
# rows) and row 7 -- left blank in the master template, so reusing it adds
# no structural drift -- carries an unmissable banner marking the whole
# document unvalidated. No S/O/D/AP columns are ever populated here: Layer 2
# does not invent ratings (see suggest_additions), so those stay genuinely
# blank pending a real engineering exercise, not fabricated as zero or "L".

AI_DRAFT_BANNER_ROW = 7
AI_DRAFT_BANNER_RANGE = "B7:AW7"
AI_DRAFT_BANNER_TEXT = (
    "⚠ ENTIRE DOCUMENT IS AN UNVALIDATED AI DRAFT — REQUIRES ENGINEER REVIEW. "
    "Every row was proposed by AI (Source Status: AI SUGGESTION), not sourced from approved "
    "standard_line content. Severity/Occurrence/Detection/AP are intentionally left blank -- "
    "those must come from a real AIAG-VDA rating exercise by an engineer, never invented by AI. "
    "Nothing in this file has been approved."
)


def _write_ai_draft_banner(ws: Worksheet) -> None:
    ws.merge_cells(AI_DRAFT_BANNER_RANGE)
    cell = ws[f"B{AI_DRAFT_BANNER_ROW}"]
    cell.value = AI_DRAFT_BANNER_TEXT
    cell.font = Font(bold=True, size=12, color="9C0006")
    cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[AI_DRAFT_BANNER_ROW].height = 40.0


def _ai_suggestion_record(row: dict) -> dict:
    """A record shaped like a line_payload (same field names _cell_value
    reads) built from one suggest_additions row -- process_step names the
    operation (AI suggestions carry no real process/structure breakdown),
    and Remarks (AF, blank in FIELD_BY_COLUMN so safe to set directly
    afterward) carries the model's own rationale for a reviewer."""
    payload = row.get("line_payload") or {}
    return {
        "row_id": None,
        "process_step": _operation_label(row.get("operation")),
        "failure_mode": payload.get("failure_mode"),
        "failure_cause": payload.get("failure_cause"),
        "failure_effect": payload.get("failure_effect"),
        "prevention_control": payload.get("prevention_control"),
        "detection_control": payload.get("detection_control"),
    }


def build_ai_draft_workbook(result: AIDraftResult) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    _write_headers(ws)
    _write_ai_draft_banner(ws)

    excel_row = HEADER_ROW
    for row in result.rows:
        excel_row += 1
        record = _ai_suggestion_record(row)
        for col in COLUMN_HEADERS:
            ws[f"{col}{excel_row}"] = _cell_value(record, col)
        ws[f"AF{excel_row}"] = row.get("rationale")
        ws[f"{SOURCE_STATUS_COLUMN}{excel_row}"] = SOURCE_STATUS_AI_SUGGESTION

    ws.freeze_panes = ws[f"A{HEADER_ROW + 1}"]
    ws.sheet_view.showGridLines = False

    for col_idx in range(1, len(COLUMN_HEADERS) + 1):
        letter = get_column_letter(col_idx)
        if letter in MASTER_COLUMN_WIDTHS:
            ws.column_dimensions[letter].width = MASTER_COLUMN_WIDTHS[letter]
    ws.column_dimensions[SOURCE_STATUS_COLUMN].width = 42.0

    for row_idx, height in MASTER_ROW_HEIGHTS.items():
        ws.row_dimensions[row_idx].height = height

    return wb


def export_ai_draft(result: AIDraftResult, output_path: Path) -> int:
    wb = build_ai_draft_workbook(result)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return len(result.rows)
