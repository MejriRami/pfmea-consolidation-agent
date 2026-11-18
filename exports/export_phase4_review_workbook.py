"""
Turns exports/phase4_clusters_for_review.json (Phase 4, Path 2 — human-
assisted grouping, see pfmea_agents/pipeline.py's module docstring for why
automated duplicate/enrichment decisions were abandoned) into an actual
Excel workbook an engineer can review and mark up.

Pure formatting: reads the clusters JSON + ground_truth/raw_lines.jsonl,
writes an .xlsx. No API calls, no NORMALIZER/ADJUDICATOR/embeddings here —
all of that already happened when phase4_clusters_for_review.json was
generated.

Sheet 1 "Clusters for Review": all 54 clusters, largest first, each
visually separated with its own header block. Every row shows source /
failure mode / cause / effect, plus two blank columns for the engineer to
fill in: Engineer Decision (a Merge / Keep Separate / Unsure dropdown) and
Notes.

Sheet 2 "Unclustered Rows": the 104 candidate rows that landed in no
cluster (no baseline row's normalized root cause was similar enough at
the calibrated threshold — see the Path 2 write-up) — listed for a
straightforward manual scan, not because the clustering did anything
special with them.

Whole workbook marked DRAFT — this is a review aid, not an approved
decision (spec §7: approval is human-only, through the app).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from rules.operations import resolve_operation

REPO = Path(__file__).resolve().parent.parent
GROUND_TRUTH_DIR = REPO / "ground_truth"
DEFAULT_CLUSTERS_PATH = REPO / "exports" / "phase4_clusters_for_review.json"

BASELINE_SOURCE = "MASTER_REF"
PFC_SOURCES = {"SITE_D_PFC"}

TITLE_FONT = Font(bold=True, size=14)
SUBTITLE_FONT = Font(italic=True, size=10, color="555555")
DRAFT_FONT = Font(bold=True, size=12, color="C00000")
CLUSTER_HEADER_FONT = Font(bold=True, color="FFFFFF")
CLUSTER_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
CLUSTER_HEADER_FILL_ALT = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
COLUMN_HEADER_FONT = Font(bold=True)
COLUMN_HEADER_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
BASELINE_ROW_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
WRAP = Alignment(wrap_text=True, vertical="top")

DECISION_OPTIONS = ["Merge", "Keep Separate", "Unsure"]

CLUSTER_COLUMNS = ["Row ID", "Source", "Failure Mode", "Failure Cause", "Failure Effect",
                   "Engineer Decision", "Notes"]
UNCLUSTERED_COLUMNS = ["Row ID", "Source", "Operation", "Failure Mode", "Failure Cause",
                       "Failure Effect", "Notes"]


def load_clusters(path: Path = DEFAULT_CLUSTERS_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def compute_unclustered_rows(clusters: list[dict], raw_lines_path: Path = GROUND_TRUTH_DIR / "raw_lines.jsonl") -> list[dict]:
    """Every candidate row (non-baseline, non-PFC) that appears in no
    cluster's row list. Recomputed from the source data rather than stored
    separately, so this stays consistent if the clusters file changes."""
    raw_rows = [json.loads(line) for line in raw_lines_path.open(encoding="utf-8")]
    clustered_row_ids = {r["row_id"] for c in clusters for r in c["rows"]}

    unclustered = []
    for r in raw_rows:
        if r["source"] == BASELINE_SOURCE or r["source"] in PFC_SOURCES:
            continue
        if r["row_id"] in clustered_row_ids:
            continue
        unclustered.append(r)
    return unclustered


def _add_decision_dropdown(ws) -> DataValidation:
    dv = DataValidation(
        type="list", formula1=f'"{",".join(DECISION_OPTIONS)}"', allow_blank=True,
        showDropDown=False,  # showDropDown=False is the openpyxl quirk that actually SHOWS the arrow
    )
    ws.add_data_validation(dv)
    return dv


def build_clusters_sheet(wb: Workbook, clusters: list[dict]) -> None:
    ws = wb.active
    ws.title = "Clusters for Review"

    ws["A1"] = "PFMEA Phase 4 — Cluster Review Worksheet"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "DRAFT — for engineer review, not an approved decision"
    ws["A2"].font = DRAFT_FONT
    ws["A3"] = (
        "Each cluster below groups rows that MIGHT describe the same underlying risk (same operation, "
        "loosely similar root cause). Nothing here has been decided automatically — for each row, mark "
        "Engineer Decision (Merge = duplicate of another row in this cluster / Keep Separate = genuinely "
        "distinct risk / Unsure) and add Notes as needed. Rows shaded yellow are the existing baseline "
        "(MASTER_REF) entry this cluster is anchored on."
    )
    ws["A3"].font = SUBTITLE_FONT
    ws["A3"].alignment = WRAP
    ws.merge_cells("A3:G3")
    ws.row_dimensions[3].height = 45

    dv = _add_decision_dropdown(ws)

    row = 5
    for i, cluster in enumerate(clusters, 1):
        header_fill = CLUSTER_HEADER_FILL if i % 2 else CLUSTER_HEADER_FILL_ALT
        anchor_row_id = next((r["row_id"] for r in cluster["rows"] if r["source"] == BASELINE_SOURCE), None)
        header_text = (
            f"Cluster {i} of {len(clusters)}  —  Operation {cluster['operation']}  —  "
            f"{cluster['size']} rows"
            + (f"  —  anchored on baseline {anchor_row_id}" if anchor_row_id else "")
        )
        ws.cell(row=row, column=1, value=header_text)
        ws.cell(row=row, column=1).font = CLUSTER_HEADER_FONT
        ws.cell(row=row, column=1).fill = header_fill
        for col in range(2, len(CLUSTER_COLUMNS) + 1):
            ws.cell(row=row, column=col).fill = header_fill
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(CLUSTER_COLUMNS))
        row += 1

        for col_idx, label in enumerate(CLUSTER_COLUMNS, 1):
            cell = ws.cell(row=row, column=col_idx, value=label)
            cell.font = COLUMN_HEADER_FONT
            cell.fill = COLUMN_HEADER_FILL
        row += 1

        first_data_row = row
        for r in cluster["rows"]:
            is_baseline = r["source"] == BASELINE_SOURCE
            values = [r["row_id"], r["source"], r["failure_mode"], r["failure_cause"], r["failure_effect"], None, None]
            for col_idx, value in enumerate(values, 1):
                cell = ws.cell(row=row, column=col_idx, value=value)
                cell.alignment = WRAP
                if is_baseline:
                    cell.fill = BASELINE_ROW_FILL
            row += 1
        last_data_row = row - 1
        dv.add(f"F{first_data_row}:F{last_data_row}")

        row += 1  # blank separator row between clusters

    ws.freeze_panes = "A5"
    widths = [16, 22, 32, 32, 32, 16, 28]
    for col_idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def build_unclustered_sheet(wb: Workbook, unclustered_rows: list[dict]) -> None:
    ws = wb.create_sheet("Unclustered Rows")

    ws["A1"] = f"Unclustered Rows ({len(unclustered_rows)}) — Manual Scan"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "DRAFT — for engineer review, not an approved decision"
    ws["A2"].font = DRAFT_FONT
    ws["A3"] = (
        "These candidate rows didn't land in any cluster — no existing baseline row's root cause was "
        "similar enough to group them automatically. That doesn't mean they're definitely new risks, "
        "just that the automatic grouping found nothing to suggest otherwise. Scan for anything that "
        "looks familiar."
    )
    ws["A3"].font = SUBTITLE_FONT
    ws["A3"].alignment = WRAP
    ws.merge_cells("A3:G3")
    ws.row_dimensions[3].height = 45

    header_row = 5
    for col_idx, label in enumerate(UNCLUSTERED_COLUMNS, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=label)
        cell.font = COLUMN_HEADER_FONT
        cell.fill = COLUMN_HEADER_FILL

    for i, r in enumerate(unclustered_rows):
        row = header_row + 1 + i
        operation = resolve_operation(r.get("process_step") or "").code
        values = [r["row_id"], r["source"], operation, r["failure_mode"], r["failure_cause"],
                  r["failure_effect"], None]
        for col_idx, value in enumerate(values, 1):
            cell = ws.cell(row=row, column=col_idx, value=value)
            cell.alignment = WRAP

    ws.freeze_panes = f"A{header_row + 1}"
    widths = [16, 22, 12, 32, 32, 32, 28]
    for col_idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def build_workbook(clusters: list[dict], unclustered_rows: list[dict]) -> Workbook:
    wb = Workbook()
    build_clusters_sheet(wb, clusters)
    build_unclustered_sheet(wb, unclustered_rows)
    wb.properties.keywords = "DRAFT"
    wb.properties.title = "PFMEA Phase 4 Cluster Review (DRAFT)"
    return wb


def export(
    output_path: Path,
    *,
    clusters_path: Path = DEFAULT_CLUSTERS_PATH,
    raw_lines_path: Path = GROUND_TRUTH_DIR / "raw_lines.jsonl",
) -> dict[str, int]:
    clusters = load_clusters(clusters_path)
    unclustered_rows = compute_unclustered_rows(clusters, raw_lines_path)
    wb = build_workbook(clusters, unclustered_rows)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)

    return {
        "clusters": len(clusters),
        "clustered_rows": sum(c["size"] for c in clusters),
        "unclustered_rows": len(unclustered_rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="exports/Phase4_Cluster_Review.xlsx")
    parser.add_argument("--clusters", default=str(DEFAULT_CLUSTERS_PATH))
    args = parser.parse_args(argv)

    summary = export(Path(args.out), clusters_path=Path(args.clusters))
    print(f"wrote {args.out}")
    print(f"  clusters: {summary['clusters']} ({summary['clustered_rows']} rows)")
    print(f"  unclustered: {summary['unclustered_rows']} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
