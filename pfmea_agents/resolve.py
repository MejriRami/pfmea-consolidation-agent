"""
Locate the 5 known source files + sheets inside an arbitrary directory.

The actual customer workbooks are user-machine-specific (they live wherever
the user downloaded them, not in this repo), and even their filenames don't
match the spec's names byte-for-byte (spaces vs. underscores, double
spaces). This module matches on the `file_pattern` / `sheet_pattern` regexes
in known_layouts.py instead of a fixed path, so the same fixture works
across machines.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .grid import list_sheet_names
from .known_layouts import KNOWN_LAYOUTS, KnownLayout


@dataclass(frozen=True)
class ResolvedLayout:
    layout: KnownLayout
    path: Path
    sheet_name: str


class ResolutionError(RuntimeError):
    pass


def resolve_known_files(source_dir: str | Path) -> dict[str, ResolvedLayout]:
    """For each KnownLayout, find exactly one .xlsx under source_dir whose
    stem matches file_pattern and which has exactly one sheet matching
    sheet_pattern. Raises ResolutionError listing every ambiguity/miss up
    front rather than failing on the first one."""
    source_dir = Path(source_dir)
    # "~$foo.xlsx" is Office's transient lock file, created while foo.xlsx is
    # open elsewhere — not a real workbook, and openpyxl can't read it anyway.
    xlsx_files = sorted(f for f in source_dir.glob("*.xlsx") if not f.name.startswith("~$"))
    if not xlsx_files:
        raise ResolutionError(f"no .xlsx files found under {source_dir}")

    resolved: dict[str, ResolvedLayout] = {}
    problems: list[str] = []

    for layout in KNOWN_LAYOUTS:
        file_candidates = [f for f in xlsx_files if layout.file_pattern.search(f.stem)]
        if not file_candidates:
            problems.append(f"{layout.key}: no file matched /{layout.file_pattern.pattern}/")
            continue
        if len(file_candidates) > 1:
            problems.append(
                f"{layout.key}: multiple files matched /{layout.file_pattern.pattern}/: "
                f"{[f.name for f in file_candidates]}"
            )
            continue

        path = file_candidates[0]
        sheet_names = list_sheet_names(path)
        sheet_candidates = [s for s in sheet_names if layout.sheet_pattern.search(s)]
        if not sheet_candidates:
            problems.append(
                f"{layout.key}: no sheet in {path.name} matched "
                f"/{layout.sheet_pattern.pattern}/ (sheets: {sheet_names})"
            )
            continue
        if len(sheet_candidates) > 1:
            problems.append(
                f"{layout.key}: multiple sheets in {path.name} matched "
                f"/{layout.sheet_pattern.pattern}/: {sheet_candidates}"
            )
            continue

        resolved[layout.key] = ResolvedLayout(layout=layout, path=path, sheet_name=sheet_candidates[0])

    if problems:
        raise ResolutionError(
            f"could not resolve {len(problems)}/{len(KNOWN_LAYOUTS)} known layouts under "
            f"{source_dir}:\n  " + "\n  ".join(problems)
        )
    return resolved
