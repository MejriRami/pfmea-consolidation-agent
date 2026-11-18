import json
from pathlib import Path

import pytest

from rules import build_dedup_key

FINAL_LINES_PATH = Path(__file__).resolve().parent.parent / "ground_truth" / "final_lines.jsonl"


def _load_final_lines():
    with FINAL_LINES_PATH.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _parse_dedup_key_original(raw):
    """Mirrors extract_ground_truth.py::parse_dedup_key exactly — splits the
    master's AW column into (op, theme, failure_mode) so we can rebuild it
    with our own build_dedup_key and check for byte-for-byte agreement."""
    if not raw or "|" not in str(raw):
        return None, None, None
    parts = str(raw).split("|")
    if len(parts) < 3:
        return None, None, None
    op = parts[0].strip()
    fm = "|".join(parts[2:]).strip().lower()
    return op, parts[1].strip(), fm


def test_build_dedup_key_reproduces_all_192_final_rows():
    """Done-when criterion: build_dedup_key reproduces all 192 final dedup
    keys from the ground truth."""
    rows = _load_final_lines()
    assert len(rows) == 192

    mismatches = []
    checked = 0
    skipped_no_key = 0
    for row in rows:
        stored_key = row["dedup_key"]
        if not stored_key:
            # Documented Phase 0 defect: row 45 (GEMBA-004) is an orphan with
            # no source_row_refs, so op/theme could never be resolved.
            skipped_no_key += 1
            continue

        op, _original_theme, failure_mode = _parse_dedup_key_original(row["dedup_key_original"])
        # The retheme step (Step 2) can have changed the theme since the AW
        # column was written; the row's current `theme` field is authoritative.
        theme = row["theme"]

        checked += 1
        rebuilt = build_dedup_key(op, theme, failure_mode)
        if rebuilt != stored_key:
            mismatches.append((row["excel_row"], stored_key, rebuilt))

    assert skipped_no_key == 1, "expected exactly the one documented orphan row (45)"
    assert checked == 191
    assert not mismatches, f"{len(mismatches)} mismatches, e.g. {mismatches[:5]}"


def test_build_dedup_key_is_pure_string_formatting():
    assert build_dedup_key("OP10", "traceability_label", "Damaged  raw   material") == (
        "OP10|traceability_label|damaged raw material"
    )


def test_build_dedup_key_collapses_whitespace_and_lowercases():
    assert build_dedup_key("OP40", "tinning_excess", "  Too Much\nCoating Tin  ") == (
        "OP40|tinning_excess|too much coating tin"
    )


def test_build_dedup_key_handles_none_failure_mode():
    assert build_dedup_key("OP10", "core_missing", None) == "OP10|core_missing|"


@pytest.mark.parametrize("row_a,row_b", [
    (("OP40", "tinning_excess", "Too much coating tin"), ("OP40", "tinning_excess", "too much coating tin")),
])
def test_build_dedup_key_is_case_insensitive_on_failure_mode(row_a, row_b):
    assert build_dedup_key(*row_a) == build_dedup_key(*row_b)
