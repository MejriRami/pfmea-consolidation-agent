"""
Offline Phase 2 tests — everything that can be verified WITHOUT calling the
MAPPER agent (no OPENAI_API_KEY needed): the deterministic grid renderer,
the known-layouts fixture's internal consistency, and — where the actual
source workbooks are available on this machine — that the fixture's
(header_row, column_letter) claims are correct against the real files.

The 5 known workbooks live wherever the user downloaded them, not in this
repo, so their location is resolved via $PFMEA_SOURCE_DIR or a couple of
common defaults; if none exist, the file-dependent tests are skipped
rather than failed, so this suite stays green on another machine or CI.
"""
from __future__ import annotations

import os
from pathlib import Path

import openpyxl
import pytest
from pydantic import ValidationError

from pfmea_agents.grid import render_sheet_grid
from pfmea_agents.known_layouts import KNOWN_LAYOUTS
from pfmea_agents.mapper import MANDATORY_FIELDS, MapperOutput
from pfmea_agents.resolve import ResolutionError, resolve_known_files

# Exact header text observed directly in each real workbook (verified by
# hand against the actual files — see the eval/known_layouts docstrings).
# Bare "S"/"O"/"D" entries are exact matches (SITE_B/SITE_C's RPN template labels
# those columns with a single letter, relying on a legend above); longer
# entries are substring checks against the live cell text.
EXPECTED_HEADER_TEXT = {
    "MASTER_REF": {
        "process_step": "Process Step",
        "failure_mode": "Failure Mode (FM) of the Process Step",
        "failure_effect": "Failure Effect (FE)",
        "severity": "Severity (S) of FE",
        "failure_cause": "Failure Cause (FC)",
        "occurrence": "Occurence (O) of FC",
        "detection": "Detection (D) of FC/FM",
    },
    "CUSTOMER_A_AIAG_VDA": {
        "process_step": "Process Step",
        "failure_mode": "Failure Mode (FM) of the Process Step",
        "failure_effect": "Failure Effect (FE)",
        "severity": "Severity (S) of FE",
        "failure_cause": "Failure Cause (FC)",
        "occurrence": "Occurence (O) of FC",
        "detection": "Detection (D) of FC/FM",
    },
    "SITE_B": {
        "process_step": "Operation Designation",
        "failure_mode": "Failure Type",
        "failure_effect": "Failure Effects",
        "severity": "S",
        "failure_cause": "Possible Failure Causes",
        "occurrence": "O",
        "detection": "D",
    },
    "SITE_C": {
        "process_step": "Operation Designation",
        "failure_mode": "Failure Type",
        "failure_effect": "Failure Effects",
        "severity": "S",
        "failure_cause": "Possible Failure Causes",
        "occurrence": "O",
        "detection": "D",
    },
    "SITE_D_LINE10": {
        "process_step": "Process Step",
        "failure_mode": "Failure Mode (FM) of the Process Step",
        "failure_effect": "Failure Effect (FE)",
        "severity": "Severity (S) of FE",  # must NOT match K11 "...of All FE"
        "failure_cause": "Failure Cause (FC)",
        "occurrence": "Occurrence (O) of FC",
        "detection": "Detection (D) of FC/FM",
    },
}


def _find_source_dir() -> Path | None:
    candidates = []
    if os.environ.get("PFMEA_SOURCE_DIR"):
        candidates.append(Path(os.environ["PFMEA_SOURCE_DIR"]))
    candidates.append(Path.home() / "Downloads")
    for c in candidates:
        if c.is_dir() and list(c.glob("*.xlsx")):
            return c
    return None


SOURCE_DIR = _find_source_dir()

try:
    RESOLVED = resolve_known_files(SOURCE_DIR) if SOURCE_DIR else None
except ResolutionError:
    RESOLVED = None

skip_if_no_files = pytest.mark.skipif(
    RESOLVED is None,
    reason="known source workbooks not found/resolvable (set PFMEA_SOURCE_DIR or check ~/Downloads)",
)


# ---- fixture self-consistency (no files needed) --------------------------

def test_known_layouts_cover_all_five_expected_keys():
    keys = {layout.key for layout in KNOWN_LAYOUTS}
    assert keys == {"MASTER_REF", "CUSTOMER_A_AIAG_VDA", "SITE_B", "SITE_C", "SITE_D_LINE10"}


def test_every_known_layout_has_all_mandatory_fields_mapped():
    for layout in KNOWN_LAYOUTS:
        assert set(layout.columns) == set(MANDATORY_FIELDS), layout.key


def test_expected_header_text_fixture_covers_every_layout_and_field():
    for layout in KNOWN_LAYOUTS:
        assert layout.key in EXPECTED_HEADER_TEXT
        assert set(EXPECTED_HEADER_TEXT[layout.key]) == set(MANDATORY_FIELDS)


def test_mapper_output_schema_rejects_field_outside_closed_vocabulary():
    # columns is a plain dict[str, str] in the schema (the closed vocabulary
    # is enforced by the agent's instructions, not a Pydantic Literal, since
    # OpenAI structured outputs don't constrain dict *keys*); this test just
    # documents that omission rather than assuming a validator exists.
    out = MapperOutput(
        header_row=9,
        columns={"failure_mode": "I", "not_a_real_field": "Z"},
        rating_method="AP",
        languages=["en"],
        confidence=0.9,
    )
    assert out.columns["not_a_real_field"] == "Z"  # accepted at the schema layer


def test_mapper_output_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        MapperOutput(
            header_row=9, columns={}, rating_method="AP", languages=["en"], confidence=1.5,
        )


# ---- grid renderer against the real workbooks -----------------------------

@skip_if_no_files
def test_default_window_reaches_every_known_header_row():
    """The core regression this suite guards: spec §5.1 says MAPPER sees
    'the first 20 rows.' SITE_B/SITE_C's real header is at row 27 — a
    naive 20-row window would never show it to the agent at all, guaranteeing
    failure regardless of model quality."""
    for key, resolved in RESOLVED.items():
        grid = render_sheet_grid(resolved.path, resolved.sheet_name)
        assert resolved.layout.header_row <= grid.max_row_scanned, (
            f"{key}: header_row {resolved.layout.header_row} is outside the "
            f"{grid.max_row_scanned}-row window MAPPER would see"
        )


@skip_if_no_files
def test_literal_spec_20_row_window_fails_for_dya_and_nic():
    """Documents the spec defect precisely: with the literal wording ("first
    20 rows"), the SITE_B/SITE_C header is provably invisible to MAPPER."""
    for key in ("SITE_B", "SITE_C"):
        resolved = RESOLVED[key]
        assert resolved.layout.header_row > 20, f"{key}: expected the defect to still be present"


@skip_if_no_files
@pytest.mark.parametrize("key", ["MASTER_REF", "CUSTOMER_A_AIAG_VDA", "SITE_B", "SITE_C", "SITE_D_LINE10"])
def test_grid_renders_real_header_text_at_expected_cells(key):
    resolved = RESOLVED[key]
    grid = render_sheet_grid(resolved.path, resolved.sheet_name)
    header_row = resolved.layout.header_row

    for field_name, column_letter in resolved.layout.columns.items():
        expected_text = EXPECTED_HEADER_TEXT[key][field_name]
        cell_prefix = f"{column_letter}{header_row}="
        matching_lines = [line for line in grid.text.splitlines() if cell_prefix in line]
        assert matching_lines, f"{key}.{field_name}: no cell {cell_prefix} in rendered grid"
        line = matching_lines[0]
        # extract the quoted value following the `=`
        value = line.split(cell_prefix, 1)[1]
        if len(expected_text) <= 2:
            assert value.startswith(f"'{expected_text}'"), (
                f"{key}.{field_name}: expected exact {expected_text!r}, got {value!r}"
            )
        else:
            assert expected_text in value, (
                f"{key}.{field_name}: expected {expected_text!r} in {value!r}"
            )


@skip_if_no_files
def test_esp10_severity_rollup_trap_is_present_and_distinct():
    """Confirms the two-severity trap (spec §4) is real in the actual file:
    K11 is the rollup, M11 is the per-effect column MAPPER must pick."""
    resolved = RESOLVED["SITE_D_LINE10"]
    wb = openpyxl.load_workbook(resolved.path, data_only=True, read_only=True)
    ws = wb[resolved.sheet_name]
    k11 = str(ws["K11"].value)
    m11 = str(ws["M11"].value)
    assert "All FE" in k11
    assert "All FE" not in m11
    assert k11 != m11
    assert resolved.layout.columns["severity"] == "M"


@skip_if_no_files
def test_all_five_known_files_resolve_unambiguously():
    assert set(RESOLVED) == {"MASTER_REF", "CUSTOMER_A_AIAG_VDA", "SITE_B", "SITE_C", "SITE_D_LINE10"}
