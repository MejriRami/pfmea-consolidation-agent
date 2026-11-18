"""
Phase 7 — multi-sheet workbook export. Builds on export_final_pfmea.py
(02_FINAL_PFMEA, UNCHANGED — imported and reused, not reimplemented) and
adds five more sheets, every one sourced from data already in this repo:

  FMEA COVER SHEET      — standard AIAG-VDA labels; project-specific
                          values (customer, part number, dates, team) are
                          left as clearly-marked blank placeholders, since
                          this repo has no ground-truth source for them.
  01_ALL_SOURCES_GROUPED — ground_truth/raw_lines.jsonl (719 rows)
  02_FINAL_PFMEA         — unchanged, from export_final_pfmea.build_workbook
  04_SOURCE_COLOR_LEGEND — the six sources' inclusion rules, quoted from
                          PFMEA_Agent_Implementation_Spec.md §3.1, with
                          actual retention numbers from
                          ground_truth/stats.json (not the spec's
                          pre-extraction ~729/202 estimates)
  12_PFMEA_KPI_Dashboard — AP distribution, per-operation counts, source
                          breakdown, AP change (initial vs. revised, where
                          a row carries revised_ap or revised_severity/
                          occurrence/detection -- none currently do, so
                          this section honestly reports zero rather than
                          fabricating a distribution), and a Top Risks
                          table (highest-AP rows, tie-broken by S/O/D) --
                          all computed directly from the 192 rows in
                          ground_truth/final_lines.jsonl
  21_AP_LOOKUP           — ground_truth/ap_lookup.json (1000 entries)

Explicitly NOT built (a separate feature): 03_PFC_PROCESS_MAP,
03_PROCESS_DIAGRAM_MAP, 06_STATION_ANALYSIS, 07_CTQ_CTP_MATRIX,
08_QUALITY_GATES, 13_PFC_DIAGRAM, 14_PFC_CARTOGRAPHY, or any other
process-flow-diagram sheet from the master.

The whole workbook is marked DRAFT: the cover sheet carries an explicit
"Document Status: DRAFT" field, and the workbook's own document
properties (keywords) are set to DRAFT — in addition to 02_FINAL_PFMEA's
existing per-row Validation_Status="Draft" column. Nothing in this
workbook is ever marked Approved; spec §7 makes approval human-only
through the app, and no review has actually happened here.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from rules.ap_lookup import APLookupError, get_ap
from rules.operations import OPERATIONS, resolve_operation

from .export_final_pfmea import GROUND_TRUTH_DIR, build_workbook, load_final_lines

DRAFT_STATUS = "Draft"
TOP_RISKS_COUNT = 15

BOLD = Font(bold=True)
HEADER_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
WRAP = Alignment(wrap_text=True, vertical="top")


# ---------------------------------------------------------------------------
# 21_AP_LOOKUP
# ---------------------------------------------------------------------------

AP_LOOKUP_HEADERS = ["Severity", "Occurrence", "Detection", "AP", "Key", "Method"]
# Verbatim constant text from the master's own 21_AP_LOOKUP!F column (every
# row repeats this same string) — observed directly, not invented.
AP_LOOKUP_METHOD_TEXT = "AIAG/VDA AP rule mapped to H/M/L"


def add_ap_lookup_sheet(wb: Workbook, ap_lookup: dict[str, str]) -> Worksheet:
    ws = wb.create_sheet("21_AP_LOOKUP")
    for col_idx, label in enumerate(AP_LOOKUP_HEADERS, 1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font = BOLD
        cell.fill = HEADER_FILL

    # ap_lookup.json isn't guaranteed insertion-ordered by S/O/D numerically
    # (it's whatever order extract_ground_truth.py wrote), so sort for a
    # readable sheet — this is presentation only, the data is unchanged.
    def sort_key(item):
        s, o, d = (int(x) for x in item[0].split("-"))
        return (s, o, d)

    for row_idx, (key, ap) in enumerate(sorted(ap_lookup.items(), key=sort_key), 2):
        s, o, d = (int(x) for x in key.split("-"))
        ws.cell(row=row_idx, column=1, value=s)
        ws.cell(row=row_idx, column=2, value=o)
        ws.cell(row=row_idx, column=3, value=d)
        ws.cell(row=row_idx, column=4, value=ap)
        ws.cell(row=row_idx, column=5, value=key)
        ws.cell(row=row_idx, column=6, value=AP_LOOKUP_METHOD_TEXT)

    ws.freeze_panes = "A2"
    for col_idx in range(1, len(AP_LOOKUP_HEADERS) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 14
    ws.column_dimensions["F"].width = 40
    return ws


# ---------------------------------------------------------------------------
# 01_ALL_SOURCES_GROUPED
# ---------------------------------------------------------------------------

GROUPED_HEADER_ROW = 9  # matches extract_ground_truth.py's GROUPED_HEADER_ROW

# Header text extracted verbatim from the master's own
# 01_ALL_SOURCES_GROUPED!A9:AO9 (openpyxl dump, not retyped from memory).
GROUPED_COLUMN_HEADERS: dict[str, str] = {
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
    "AG": "Source_ID",
    "AH": "Source_File",
    "AI": "Source_Sheet",
    "AJ": "Original_Row",
    "AK": "Source_Color",
    "AL": "Mapped_Final_ID",
    "AM": "Mapping_Note",
    "AN": "Site",
    "AO": "Customer",
}

# column letter -> raw_lines.jsonl field. Everything else is left blank:
# not extracted by Phase 0 (process_item, work_element, function_*,
# prevention/detection_control, special_char, action-tracking T:AF,
# Source_File/Sheet/Original_Row/Site/Customer), or deliberately never
# reproduced — Mapped_Final_ID (AL) is a documented Phase 0 defect: every
# one of the 720 rows carries the identical stray value "184" (a
# column-shift artifact, see PHASE_0_RESULTS.md / spec §9 item 2), so
# reproducing it would fabricate a false mapping, not preserve a real one.
GROUPED_FIELD_BY_COLUMN: dict[str, str] = {
    "A": "row_id",
    "D": "process_step",
    "I": "failure_mode",
    "J": "failure_effect",
    "K": "severity",
    "L": "failure_cause",
    "N": "occurrence",
    "P": "detection",
    "Q": "ap",
    "AG": "source",
}
GROUPED_INTEGER_COLUMNS = {"K", "N", "P"}


def _grouped_cell_value(record: dict, column: str):
    field = GROUPED_FIELD_BY_COLUMN.get(column)
    if field is None:
        return None
    value = record.get(field)
    if column in GROUPED_INTEGER_COLUMNS and value is not None:
        return int(value)
    return value


def add_sources_grouped_sheet(wb: Workbook, raw_lines: list[dict]) -> Worksheet:
    ws = wb.create_sheet("01_ALL_SOURCES_GROUPED")
    for col, label in GROUPED_COLUMN_HEADERS.items():
        cell = ws[f"{col}{GROUPED_HEADER_ROW}"]
        cell.value = label
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.alignment = WRAP

    for i, record in enumerate(raw_lines):
        row = GROUPED_HEADER_ROW + 1 + i
        for col in GROUPED_COLUMN_HEADERS:
            ws[f"{col}{row}"] = _grouped_cell_value(record, col)

    ws.freeze_panes = ws[f"A{GROUPED_HEADER_ROW + 1}"]
    for col_idx in range(1, len(GROUPED_COLUMN_HEADERS) + 1):
        letter = get_column_letter(col_idx)
        ws.column_dimensions[letter].width = 8 if letter in ("A", "K", "N", "P", "Q") else 16
    return ws


# ---------------------------------------------------------------------------
# 04_SOURCE_COLOR_LEGEND
# ---------------------------------------------------------------------------

# Rule text quoted verbatim from PFMEA_Agent_Implementation_Spec.md §3.1's
# "Rule" column (that table's own retention numbers are the spec's
# pre-extraction estimates, ~729/202; the counts below are instead the
# actual, verified figures from ground_truth/stats.json).
SOURCE_RULES: dict[str, str] = {
    "MASTER_REF": "Baseline. Every row preserved unconditionally.",
    "SITE_D_PFMEA": "Admitted only if non-duplicate vs. baseline.",
    "Gemba + AI Suggestions": "Admitted when shopfloor evidence adds a real risk.",
    "SITE_B": "Admitted when risk/failure-cause differs.",
    "SITE_C": "Same as SITE_B.",
    "SITE_D_PFC": "Process-flow evidence only. Never a PFMEA row.",
}

SOURCE_LEGEND_HEADERS = ["Source_ID", "Raw_Rows", "Final_Rows", "Retention", "Rule"]


def add_source_color_legend_sheet(wb: Workbook, stats: dict) -> Worksheet:
    ws = wb.create_sheet("04_SOURCE_COLOR_LEGEND")
    for col_idx, label in enumerate(SOURCE_LEGEND_HEADERS, 1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font = BOLD
        cell.fill = HEADER_FILL

    raw_by_source = stats["raw_by_source"]
    final_by_source = stats["final_by_source"]
    retention_by_source = stats["retention_by_source"]

    row_idx = 2
    for source, rule in SOURCE_RULES.items():
        ws.cell(row=row_idx, column=1, value=source)
        ws.cell(row=row_idx, column=2, value=raw_by_source.get(source))
        ws.cell(row=row_idx, column=3, value=final_by_source.get(source, 0))
        ws.cell(row=row_idx, column=4, value=retention_by_source.get(source))
        ws.cell(row=row_idx, column=5, value=rule)
        row_idx += 1

    # UNKNOWN isn't a real source with a rule -- it's the documented orphan
    # row defect (excel_row 45, GEMBA-004, no source_row_refs at all; see
    # PHASE_0_RESULTS.md). Included for honesty (it IS in stats.json), not
    # presented as a sixth inclusion rule.
    if "UNKNOWN" in final_by_source:
        ws.cell(row=row_idx, column=1, value="UNKNOWN")
        ws.cell(row=row_idx, column=2, value=None)
        ws.cell(row=row_idx, column=3, value=final_by_source["UNKNOWN"])
        ws.cell(row=row_idx, column=4, value=None)
        ws.cell(row=row_idx, column=5,
                value="Not a source, not a rule -- documented Phase 0 defect: an orphan final "
                      "row with no source_row_refs at all (excel_row 45, GEMBA-004). "
                      "See PHASE_0_RESULTS.md.")
        ws.cell(row=row_idx, column=5).alignment = WRAP

    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 60
    return ws


# ---------------------------------------------------------------------------
# FMEA COVER SHEET
# ---------------------------------------------------------------------------

BLANK_PLACEHOLDER = "[TO BE COMPLETED]"

# Standard AIAG-VDA PFMEA planning/cover-sheet labels (spec §5.4 / the
# master's own PLANNING & PREPARATION STEP 1 block) -- structural, not
# project-specific, so reproducing the LABELS is legitimate. Every VALUE
# is a placeholder: this repo has no ground-truth source for the actual
# customer, part number, dates, or team for a specific program.
COVER_SHEET_FIELDS = [
    ("Company Name:", "Plant Location:"),
    ("Subject:", "PFMEA Start Date:"),
    ("Customer Name:", "PFMEA Revision Date:"),
    ("Model Year / Platform:", "Cross-Functional Team:"),
    ("PFMEA ID Number:", "Process Responsibility:"),
    ("Part Number:", "Confidentiality Level:"),
]


def add_cover_sheet(wb: Workbook) -> Worksheet:
    ws = wb.create_sheet("FMEA COVER SHEET", 0)  # first sheet
    ws["B2"] = "PFMEA (AIAG/VDA manual 1st edition 2019) -- PLANNING & PREPARATION (STEP 1)"
    ws["B2"].font = Font(bold=True, size=14)

    ws["B4"] = "Document Status: DRAFT"
    ws["B4"].font = Font(bold=True, color="C00000", size=12)

    row = 6
    for left_label, right_label in COVER_SHEET_FIELDS:
        ws.cell(row=row, column=2, value=left_label).font = BOLD
        ws.cell(row=row, column=3, value=BLANK_PLACEHOLDER)
        ws.cell(row=row, column=6, value=right_label).font = BOLD
        ws.cell(row=row, column=7, value=BLANK_PLACEHOLDER)
        row += 1

    note_row = row + 2
    ws.cell(row=note_row, column=2,
            value="All fields above are standard AIAG-VDA cover-sheet labels. Values are "
                  "placeholders -- this export has no ground-truth source for a specific "
                  "program's customer, part number, dates, or team.")
    ws.cell(row=note_row, column=2).alignment = WRAP
    ws.merge_cells(start_row=note_row, start_column=2, end_row=note_row, end_column=8)

    for col, width in [("A", 3), ("B", 22), ("C", 18), ("D", 3), ("E", 3), ("F", 22), ("G", 18)]:
        ws.column_dimensions[col].width = width
    return ws


# ---------------------------------------------------------------------------
# 12_PFMEA_KPI_Dashboard
# ---------------------------------------------------------------------------

def add_kpi_dashboard_sheet(wb: Workbook, final_lines: list[dict]) -> Worksheet:
    ws = wb.create_sheet("12_PFMEA_KPI_Dashboard")
    ws["B2"] = "PFMEA KPI Dashboard"
    ws["B2"].font = Font(bold=True, size=14)
    ws["B3"] = f"Computed directly from the {len(final_lines)} rows in ground_truth/final_lines.jsonl"
    ws["B3"].font = Font(italic=True)

    total = len(final_lines)

    def write_table(title: str, start_row: int, headers: list[str], rows: list[tuple]) -> int:
        ws.cell(row=start_row, column=2, value=title).font = Font(bold=True, size=12)
        header_row = start_row + 1
        for col_idx, label in enumerate(headers, 2):
            cell = ws.cell(row=header_row, column=col_idx, value=label)
            cell.font = BOLD
            cell.fill = HEADER_FILL
        for i, row_values in enumerate(rows):
            for col_idx, value in enumerate(row_values, 2):
                ws.cell(row=header_row + 1 + i, column=col_idx, value=value)
        return header_row + 1 + len(rows) + 2  # next free row, with a blank line gap

    # --- AP distribution -----------------------------------------------------
    ap_counts = Counter(r.get("ap") for r in final_lines)
    ap_rows = [
        (ap or "(none)", ap_counts.get(ap, 0), round(ap_counts.get(ap, 0) / total, 3) if total else 0)
        for ap in ("H", "M", "L")
    ]
    unclassified_ap = sum(c for ap, c in ap_counts.items() if ap not in ("H", "M", "L"))
    if unclassified_ap:
        ap_rows.append(("(missing/other)", unclassified_ap, round(unclassified_ap / total, 3) if total else 0))
    next_row = write_table("AP Distribution", 5, ["AP", "Count", "Share"], ap_rows)

    # --- counts per operation --------------------------------------------------
    op_counts = Counter(r.get("operation") for r in final_lines)
    canonical_order = [op.code for op in OPERATIONS]
    op_rows = [(op, op_counts.get(op, 0)) for op in canonical_order if op_counts.get(op)]
    other_ops = sorted(set(op_counts) - set(canonical_order) - {None})
    op_rows += [(op, op_counts[op]) for op in other_ops]
    if op_counts.get(None):
        op_rows.append(("(none)", op_counts[None]))
    next_row = write_table("Rows per Operation", next_row, ["Operation", "Count"], op_rows)

    # --- source breakdown --------------------------------------------------
    source_counts = Counter(r.get("source") for r in final_lines)
    source_rows = [
        (source, count, round(count / total, 3) if total else 0)
        for source, count in sorted(source_counts.items(), key=lambda kv: -kv[1])
    ]
    next_row = write_table("Source Breakdown", next_row, ["Source", "Count", "Share"], source_rows)

    # --- AP change: initial vs revised -----------------------------------
    # Revised S/O/D/AP (the master's T:AF action-tracking block) has never
    # been extracted into final_lines (see export_final_pfmea.py's own
    # docstring, "OUT OF SCOPE") -- so this reads whatever's actually on
    # each row (revised_ap directly, or revised_severity/occurrence/
    # detection to derive it via the same AP lookup table) rather than
    # assuming it exists. With none currently populated, this section
    # honestly reports zero rather than fabricating a distribution.
    revised_rows = []
    for r in final_lines:
        revised_ap = r.get("revised_ap")
        if not revised_ap:
            rs, ro, rd = r.get("revised_severity"), r.get("revised_occurrence"), r.get("revised_detection")
            if rs is not None and ro is not None and rd is not None:
                try:
                    revised_ap = get_ap(int(rs), int(ro), int(rd))
                except (APLookupError, TypeError, ValueError):
                    revised_ap = None
        if revised_ap and r.get("ap"):
            revised_rows.append((r, revised_ap))

    ap_rank = {"L": 1, "M": 2, "H": 3}
    improved = worsened = unchanged = 0
    transitions: Counter = Counter()
    for r, revised_ap in revised_rows:
        initial_ap = r.get("ap")
        transitions[(initial_ap, revised_ap)] += 1
        i_rank, r_rank = ap_rank.get(initial_ap), ap_rank.get(revised_ap)
        if i_rank is None or r_rank is None or i_rank == r_rank:
            unchanged += 1
        elif r_rank < i_rank:
            improved += 1
        else:
            worsened += 1

    ap_change_rows = [
        ("Rows with a revised rating", len(revised_rows), f"of {total} total"),
        ("Improved (lower AP)", improved, ""),
        ("Unchanged", unchanged, ""),
        ("Worsened (higher AP)", worsened, ""),
    ]
    for (initial_ap, revised_ap), count in sorted(transitions.items(), key=lambda kv: -kv[1]):
        ap_change_rows.append((f"{initial_ap} -> {revised_ap}", count, ""))
    next_row = write_table("AP Change: Initial vs Revised", next_row, ["", "Count", "Note"], ap_change_rows)

    # --- top risks: highest-AP items ---------------------------------------
    def risk_sort_key(r: dict) -> tuple:
        rank = ap_rank.get(r.get("ap"), 0)
        sev = r.get("severity") or 0
        occ = r.get("occurrence") or 0
        det = r.get("detection") or 0
        return (-rank, -sev, -occ, -det)

    ranked = sorted((r for r in final_lines if r.get("ap") in ap_rank), key=risk_sort_key)
    top_risks = ranked[:TOP_RISKS_COUNT]
    top_risk_rows = [
        (
            r.get("row_id"), r.get("operation"), (r.get("failure_mode") or "")[:80],
            r.get("severity"), r.get("occurrence"), r.get("detection"), r.get("ap"),
        )
        for r in top_risks
    ]
    write_table(
        f"Top {len(top_risk_rows)} Risks (highest AP)", next_row,
        ["Row ID", "Operation", "Failure Mode", "S", "O", "D", "AP"], top_risk_rows,
    )

    for col, width in [("A", 3), ("B", 26), ("C", 40), ("D", 10), ("E", 10), ("F", 10), ("G", 10)]:
        ws.column_dimensions[col].width = width
    return ws


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_full_workbook(
    final_lines: list[dict], raw_lines: list[dict], ap_lookup: dict[str, str], stats: dict,
) -> Workbook:
    wb = build_workbook(final_lines)  # 02_FINAL_PFMEA, unchanged
    add_cover_sheet(wb)  # inserted at index 0, so it becomes the first sheet
    add_sources_grouped_sheet(wb, raw_lines)
    add_source_color_legend_sheet(wb, stats)
    add_kpi_dashboard_sheet(wb, final_lines)
    add_ap_lookup_sheet(wb, ap_lookup)

    wb.properties.keywords = "DRAFT"
    wb.properties.title = "PFMEA Export (DRAFT)"
    return wb


def export(output_path: Path, *, ground_truth_dir: Path = GROUND_TRUTH_DIR) -> dict[str, int]:
    final_lines = load_final_lines(ground_truth_dir / "final_lines.jsonl")
    with (ground_truth_dir / "raw_lines.jsonl").open(encoding="utf-8") as fh:
        raw_lines = [json.loads(line) for line in fh]
    ap_lookup = json.loads((ground_truth_dir / "ap_lookup.json").read_text(encoding="utf-8"))
    stats = json.loads((ground_truth_dir / "stats.json").read_text(encoding="utf-8"))

    wb = build_full_workbook(final_lines, raw_lines, ap_lookup, stats)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)

    return {
        "sheets": len(wb.sheetnames),
        "final_pfmea_rows": len(final_lines),
        "grouped_rows": len(raw_lines),
        "ap_lookup_rows": len(ap_lookup),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="exports/PFMEA_Export.xlsx", help="output .xlsx path")
    args = parser.parse_args(argv)

    summary = export(Path(args.out))
    print(f"wrote {summary['sheets']} sheets to {args.out}")
    print(f"  02_FINAL_PFMEA: {summary['final_pfmea_rows']} rows")
    print(f"  01_ALL_SOURCES_GROUPED: {summary['grouped_rows']} rows")
    print(f"  21_AP_LOOKUP: {summary['ap_lookup_rows']} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
