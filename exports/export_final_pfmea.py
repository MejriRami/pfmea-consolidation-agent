"""
Phase 7 — XLSX export engine (spec §8: "XLSX export matching 02_FINAL_PFMEA
structure | Byte-comparable structure; Draft/Approved marking correct").

Standalone demo: reads ground_truth/final_lines.jsonl (Phase 0's 192-row
validated answer key) and writes an XLSX matching 02_FINAL_PFMEA's column
layout — same columns, same order, same AIAG-VDA 6-step header structure,
extracted VERBATIM from the master workbook's own header rows 8-9 (not
retyped from memory). Does not touch the database, rules/, or
pfmea_agents/ at all.

IDENTITY (column A): populated from final_lines.jsonl's `row_id` field,
which extract_ground_truth.py already resolved as AL-authoritative
(`ref_row_id or str(rid)`) — NEVER `row_id_column_a_untrusted`, the
master's own drifted column A value (PHASE_0_RESULTS.md defect #1: 55
final rows have a column-A id that disagrees with AG/AQ/AL; AL is
authoritative because AG/AQ/AL agree with each other on all 192 rows).
Column AL (Source_Row_Refs) is additionally populated with the FULL
provenance string, independent of column A, so identity is traceable two
ways rather than relying on the master's own defective column.

DRAFT/APPROVED (column AK, Validation_Status): every exported row is
marked "Draft". Nothing is ever marked "Approved" by this export — spec
§7 is explicit that approval is human-only, through the app; a standalone
export with no actual review step has no basis to claim any row was
approved.

ROWS 1-6 (Planning & Preparation header block): a real, previously-missing
structural piece of AIAG-VDA's 02_FINAL_PFMEA layout — the sheet title and
the Step 1 "PLANNING & PREPARATION" info block (Company Name, Subject,
PFMEA ID Number, Plant Location, Start/Revision Date, Process
Responsibility, Customer Name, Confidentiality Level, Model Year/Platform,
Cross-Functional Team) that sits above the STEP 2-6 table this export
already reproduced. Labels AND values below are extracted verbatim from
PFMEA_Master_CustomerA_AIAG_VDA_2026_Pilot1_Dashboard_PFC_Updated.xlsx's own
02_FINAL_PFMEA!A1:V6 (openpyxl dump, not retyped from memory) — same
verbatim-extraction convention as STEP_HEADERS/COLUMN_HEADERS below. The
values are this one fixed pilot's real project data (this exporter has
never run against any other project), not a fabricated guess.

OUT OF SCOPE, left blank (not part of Phase 0's ground_truth extraction —
see extract_ground_truth.py's FINAL_COLS vs. what extract_final() actually
populates): History (B), Filter Code (S), the T:AF action-tracking block
(Prevention/Detection Action, owner, dates, status, revised S/O/D/AP,
Remarks), PFC_Link (AH), QG_Link (AI), Expert_Value_Add (AJ),
Source_Color_Code (AR), ControlPlan_Link (AM), Action_Link (AN),
Review_Owner (AO). None of these are needed to demonstrate the export
format, and fabricating values for them would misrepresent what Phase 0
actually validated.
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

GROUND_TRUTH_DIR = Path(__file__).resolve().parent.parent / "ground_truth"
SHEET_NAME = "02_FINAL_PFMEA"
HEADER_ROW = 9  # data starts at HEADER_ROW + 1, matching the master exactly
STEP_HEADER_ROW = 8

# Extracted verbatim from the master's 02_FINAL_PFMEA!A1:V6 -- see module
# docstring's "ROWS 1-6" note. cell -> (value, merge_range or None).
PLANNING_HEADER: dict[str, tuple[Any, str | None]] = {
    "G1": ("PFMEA (AIAG/VDA manual 1st edition 2019)          ", None),
    "C2": ("PLANNING & PREPARATION (STEP 1)", "C2:F2"),
    "C3": ("Company Name: ", "C3:D3"),
    "E3": ("the company", "E3:F3"),
    "H3": ("Subject: ", "H3:I3"),
    "J3": ("Process PFMEA — Chokes / Core Assembly", "J3:M3"),
    "N3": ("PFMEA ID Number: ", "N3:Q3"),
    "R3": ("DEMO-CHOKES-PFMEA-2026", None),
    "C4": ("Plant Location: ", "C4:D4"),
    "E4": ("Group master (all sites)", "E4:F4"),
    "H4": ("PFMEA Start Date: ", "H4:I4"),
    "J4": (datetime.datetime(2026, 5, 13), "J4:K4"),
    "N4": ("Process Responsibility: ", "N4:Q4"),
    "R4": ("Quality Engineering", "R4:V4"),
    "C5": ("Customer Name: ", "C5:D5"),
    "E5": ("-", "E5:F5"),
    "H5": ("PFMEA Revision Date", "H5:I5"),
    "J5": (datetime.datetime(2026, 6, 23), "J5:K5"),
    "N5": ("Confidentiality Level: ", "N5:Q5"),
    "R5": ("Confidential", "R5:V5"),
    "C6": ("Model Year / Platform: ", "C6:D6"),
    "E6": ("2026 Corporate Master", "E6:F6"),
    "H6": ("Cross-Functional Team: ", "H6:I6"),
    # Blank in the master too (no value ever entered) -- merged anyway, so
    # replicated for structural fidelity even though there's nothing to show.
    "L4": (None, "L4:M4"),
    "L5": (None, "L5:M5"),
    "N6": (None, "N6:Q6"),
    "R6": (None, "R6:U6"),
}
PLANNING_DATE_CELLS = {"J4", "J5"}

# Column widths extracted verbatim from the master's 02_FINAL_PFMEA -- only
# columns where the master itself sets an explicit width; the rest are left
# at openpyxl's default rather than guessing, matching the master.
MASTER_COLUMN_WIDTHS: dict[str, float] = {
    "A": 12.0, "B": 26.0, "C": 34.0, "E": 28.0, "F": 40.0, "G": 38.0, "H": 35.0,
    "I": 34.0, "J": 40.0, "K": 10.0, "L": 36.0, "M": 40.0, "N": 12.0, "O": 40.0,
    "P": 12.0, "Q": 10.0, "R": 26.0, "S": 16.0, "T": 32.0, "V": 20.0, "W": 18.0,
    "Y": 32.0, "Z": 18.0, "AA": 10.0, "AD": 24.0, "AE": 10.0, "AF": 36.0,
    "AO": 13.0, "AP": 15.0, "AW": 13.0,
}

# Row heights extracted verbatim from the master's 02_FINAL_PFMEA rows 1-9.
MASTER_ROW_HEIGHTS: dict[int, float] = {
    1: 51.6, 2: 24.0, 3: 24.0, 4: 24.0, 5: 24.0, 6: 24.0, 7: 34.2, 8: 39.0, 9: 60.0,
}

# Extracted verbatim from PFMEA_Master_CustomerA_AIAG_VDA_2026_Pilot1_Dashboard_PFC_Updated.xlsx,
# sheet 02_FINAL_PFMEA, rows 8-9 (openpyxl dump, not retyped from memory).
STEP_HEADERS: dict[str, str] = {
    "A8": "#",
    "B8": "CONTINUOUS IMPROVEMENT",
    "C8:E8": "STRUCTURE ANALYSIS (STEP 2)",
    "F8:H8": "FUNCTION ANALYSIS (STEP 3)",
    "I8:L8": "FAILURE ANALYSIS (STEP 4)",
    "M8:S8": "RISK ANALYSIS (STEP 5)",
    "T8:AE8": "OPTIMIZATION (STEP 6)",
    "AP8:AW8": "SOURCE SELECTION & CONSOLIDATION PROOF",
}

COLUMN_HEADERS: dict[str, str] = {
    "A": "N",
    "B": "History / Change Authorization \n( as applicable)",
    "C": "1. Process Item          System, Subsystem,    Part Element or       Name of Process    ",
    "D": "2. Process Step       Station No. and Name of Focus Element",
    "E": "3. Process Work Element                    \n  4M Type",
    "F": "1. Function of the Process Item      Function of System, Subsystems, Part Element or Process ",
    "G": "2. Function of the Process Step and Product Characteristic    (Quatitative value is optional)",
    "H": "3. Function of the Process work Element and Process characteristic",
    "I": "2. Failure Mode (FM) of the Process Step",
    "J": "1. Failure Effect (FE)",
    "K": "Severity (S) of FE",
    "L": "3. Failure Cause (FC)     of the Work Element",
    "M": "Current Prevention Control (PC) of FC",
    "N": "Occurence (O) of FC",
    "O": "Current Detection Control (DC)              of FC or FM",
    "P": "Detection (D) of FC/FM",
    "Q": "PFMEA AP",
    "R": "Special Characteristics",
    "S": "Filter Code",
    "T": "Prevention Action",
    "U": "Detection Action",
    "V": "Responsible   Person's Name",
    "W": "Target      Completion     Date",
    "X": "Status",
    "Y": "Action Taken with Pointer to Evidence",
    "Z": "Completion Date",
    "AA": "Severity (S)",
    "AB": "Occurence (O)",
    "AC": "Detection (D)",
    "AD": "SpProd Char",
    "AE": "PFMEA AP",
    "AF": "Remarks",
    "AG": "Source_Basis",
    "AH": "PFC_Link",
    "AI": "QG_Link",
    "AJ": "Expert_Value_Add",
    "AK": "Validation_Status",
    "AL": "Source_Row_Refs",
    "AM": "ControlPlan_Link",
    "AN": "Action_Link",
    "AO": "Review_Owner",
    "AP": "OP_Sequence_Index",
    "AQ": "Primary_Source_Selected",
    "AR": "Source_Color_Code",
    "AS": "Consolidation_Tag",
    "AT": "Master_Reference_Comparator",
    "AU": "Selected_Source_Rows",
    "AV": "Source_Selection_Justification",
    "AW": "Duplicate_Elimination_Key",
}

DRAFT_STATUS = "Draft"

# column letter -> final_lines.jsonl field name, for the columns Phase 0
# actually extracted. Columns with no entry here are intentionally left
# blank (out of ground_truth scope, or — Validation_Status — always the
# constant DRAFT_STATUS rather than a copied field; see module docstring).
FIELD_BY_COLUMN: dict[str, str] = {
    "A": "row_id",  # AL-authoritative identity, NOT row_id_column_a_untrusted
    "C": "process_item",
    "D": "process_step",
    "E": "work_element",
    "F": "function_item",
    "G": "function_step",
    "H": "function_element",
    "I": "failure_mode",
    "J": "failure_effect",
    "K": "severity",
    "L": "failure_cause",
    "M": "prevention_control",
    "N": "occurrence",
    "O": "detection_control",
    "P": "detection",
    "Q": "ap",
    "R": "special_char",
    "AG": "source",
    "AL": "source_row_refs",
    "AP": "op_sequence_index",
    "AQ": "source",  # no separate primary_source field in ground truth; same resolved identity as AG
    "AS": "consolidation_tag",
    "AT": "master_comparator",
    "AU": "selected_source_rows",
    "AV": "justification",
    "AW": "dedup_key",
}

INTEGER_COLUMNS = {"K", "N", "P"}  # severity, occurrence, detection


def load_final_lines(path: Path = GROUND_TRUTH_DIR / "final_lines.jsonl") -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _cell_value(record: dict, column: str) -> Any:
    if column == "AK":
        return DRAFT_STATUS
    field = FIELD_BY_COLUMN.get(column)
    if field is None:
        return None
    value = record.get(field)
    if column in INTEGER_COLUMNS and value is not None:
        return int(value)
    return value


def _write_planning_header(ws: Worksheet) -> None:
    title_font = Font(bold=True, size=14)
    section_font = Font(bold=True, size=11, color="FFFFFF")
    section_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    label_font = Font(bold=True)

    for ref, (value, merge_range) in PLANNING_HEADER.items():
        if merge_range:
            ws.merge_cells(merge_range)
        cell = ws[ref]
        cell.value = value
        if ref in PLANNING_DATE_CELLS:
            cell.number_format = "yyyy-mm-dd"
        if ref == "G1":
            cell.font = title_font
        elif ref == "C2":
            cell.font = section_font
            cell.fill = section_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif isinstance(value, str) and (value.strip().endswith(":") or value.strip() == "PFMEA Revision Date"):
            cell.font = label_font


def _write_headers(ws: Worksheet) -> None:
    header_font = Font(bold=True)
    header_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    wrap = Alignment(wrap_text=True, vertical="top")

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
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for col, label in COLUMN_HEADERS.items():
        cell = ws[f"{col}{HEADER_ROW}"]
        cell.value = label
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = wrap


def build_workbook(final_lines: list[dict]) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    _write_headers(ws)

    for i, record in enumerate(final_lines):
        excel_row = HEADER_ROW + 1 + i
        for col in COLUMN_HEADERS:
            ws[f"{col}{excel_row}"] = _cell_value(record, col)

    ws.freeze_panes = ws[f"A{HEADER_ROW + 1}"]
    ws.sheet_view.showGridLines = False  # matches the master's 02_FINAL_PFMEA

    for col_idx in range(1, len(COLUMN_HEADERS) + 1):
        letter = get_column_letter(col_idx)
        if letter in MASTER_COLUMN_WIDTHS:
            ws.column_dimensions[letter].width = MASTER_COLUMN_WIDTHS[letter]
        # else: leave at openpyxl's default -- the master itself sets no
        # explicit width for this column, so guessing one would drift from it.

    for row_idx, height in MASTER_ROW_HEIGHTS.items():
        ws.row_dimensions[row_idx].height = height

    return wb


def export(
    output_path: Path,
    *,
    input_path: Path = GROUND_TRUTH_DIR / "final_lines.jsonl",
) -> int:
    final_lines = load_final_lines(input_path)
    wb = build_workbook(final_lines)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return len(final_lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="exports/02_FINAL_PFMEA_export.xlsx",
                         help="output .xlsx path")
    parser.add_argument("--input", default=str(GROUND_TRUTH_DIR / "final_lines.jsonl"),
                         help="input final_lines.jsonl path")
    args = parser.parse_args(argv)

    n = export(Path(args.out), input_path=Path(args.input))
    print(f"wrote {n} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
