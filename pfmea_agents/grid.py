"""
MAPPER input prep — deterministic, NOT AI.

Renders the top-left corner of a worksheet as a plain-text grid with cell
references (spec §5.1: "first 20 rows of a sheet, rendered as a text grid
with cell refs"). This module only reads and formats; it makes no judgment
about column meaning — that's MAPPER's job.

KNOWN SPEC DEVIATION: §5.1 says "first 20 rows." Inspecting the actual
F-ING-004-H template (FMEA-IND-SITE_B-001, FMEA-IND-SITE_C-001) shows a
severity/occurrence/detection legend block occupying rows 1-26, pushing the
real header to row 27 — outside a 20-row window entirely. DEFAULT_MAX_ROWS
is widened to 40 so every known layout's header (row 9, 9, 11, 27, 27) is
visible with data rows to spare. Pass max_rows=20 to reproduce the spec's
literal wording for comparison.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

DEFAULT_MAX_ROWS = 40
DEFAULT_MAX_COLS = 60
CELL_TEXT_TRUNCATE = 80


@dataclass(frozen=True)
class SheetGrid:
    filename: str
    sheet_name: str
    max_row_scanned: int
    text: str


def render_sheet_grid(
    path: str | Path,
    sheet_name: str,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_cols: int = DEFAULT_MAX_COLS,
) -> SheetGrid:
    """One line per non-blank row: `A1='...' | C1='...' | ...`. Blank cells
    are omitted (not padded), so a line may start at any column letter —
    the cell ref carries the position, not the line's left edge. Fully
    blank rows are dropped entirely; row numbers still identify position
    unambiguously since every populated cell carries its own ref.
    """
    path = Path(path)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]

    lines: list[str] = []
    for i, row in enumerate(
        ws.iter_rows(min_row=1, max_row=max_rows, max_col=max_cols, values_only=True), 1
    ):
        cells = []
        for j, value in enumerate(row, 1):
            if value is None:
                continue
            text = " ".join(str(value).split())
            if not text:
                continue
            if len(text) > CELL_TEXT_TRUNCATE:
                text = text[: CELL_TEXT_TRUNCATE - 3] + "..."
            cells.append(f"{get_column_letter(j)}{i}={text!r}")
        if cells:
            lines.append(" | ".join(cells))

    return SheetGrid(
        filename=path.name,
        sheet_name=sheet_name,
        max_row_scanned=max_rows,
        text="\n".join(lines),
    )


def list_sheet_names(path: str | Path) -> list[str]:
    """Sheet names verbatim, including the URL-encoded (`%20`) and
    trailing-space traps documented in spec §4 — decoding/matching those is
    the caller's job, this is just a faithful read."""
    wb = openpyxl.load_workbook(Path(path), read_only=True)
    return wb.sheetnames
