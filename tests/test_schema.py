import json
from pathlib import Path

import pytest

from rules import THEME_VOCABULARY, UNCLASSIFIED, build_dedup_key, validate_normalized_line

FINAL_LINES_PATH = Path(__file__).resolve().parent.parent / "ground_truth" / "final_lines.jsonl"


def _valid_row(**overrides):
    row = {
        "operation": "OP10",
        "process_step": "RM incoming inspection",
        "failure_mode": "Damaged raw material packaging",
        "failure_effect": "Customer claim",
        "failure_cause": "Supplier bad storage",
        "severity": 7,
        "occurrence": 3,
        "detection": 3,
        "ap": "L",
        "four_m_type": None,
        "special_char": None,
        "theme": "packaging_material",
    }
    row["dedup_key"] = build_dedup_key(row["operation"], row["theme"], row["failure_mode"])
    row.update(overrides)
    return row


def test_valid_row_passes():
    result = validate_normalized_line(_valid_row())
    assert result.ok, result.errors


def test_missing_required_field_fails():
    row = _valid_row()
    del row["failure_cause"]
    result = validate_normalized_line(row)
    assert not result.ok
    assert any("failure_cause" in e for e in result.errors)


def test_operation_must_match_op_pattern():
    row = _valid_row(operation="10")
    result = validate_normalized_line(row)
    assert not result.ok
    assert any("operation" in e for e in result.errors)


@pytest.mark.parametrize("field", ["severity", "occurrence", "detection"])
def test_sod_out_of_range_fails(field):
    row = _valid_row(**{field: 11})
    result = validate_normalized_line(row)
    assert not result.ok
    assert any(field in e for e in result.errors)


@pytest.mark.parametrize("field", ["severity", "occurrence", "detection"])
def test_sod_missing_fails(field):
    row = _valid_row(**{field: None})
    result = validate_normalized_line(row)
    assert not result.ok
    assert any(field in e for e in result.errors)


def test_invalid_ap_fails():
    row = _valid_row(ap="X")
    result = validate_normalized_line(row)
    assert not result.ok
    assert any("ap" in e for e in result.errors)


def test_invalid_four_m_type_fails():
    row = _valid_row(four_m_type="Money")
    result = validate_normalized_line(row)
    assert not result.ok
    assert any("four_m_type" in e for e in result.errors)


@pytest.mark.parametrize("value", ["Man", "Machine", "Material", "Method", None])
def test_valid_four_m_type_passes(value):
    row = _valid_row(four_m_type=value)
    result = validate_normalized_line(row)
    assert result.ok, result.errors


def test_invalid_special_char_fails():
    row = _valid_row(special_char="ZZ")
    result = validate_normalized_line(row)
    assert not result.ok


@pytest.mark.parametrize("value", ["CC", "SC", "KPC", None])
def test_valid_special_char_passes(value):
    row = _valid_row(special_char=value)
    result = validate_normalized_line(row)
    assert result.ok, result.errors


def test_theme_outside_closed_vocabulary_fails():
    row = _valid_row(theme="totally_made_up_theme")
    row["dedup_key"] = build_dedup_key(row["operation"], row["theme"], row["failure_mode"])
    result = validate_normalized_line(row)
    assert not result.ok
    assert any("theme" in e for e in result.errors)


def test_unclassified_theme_is_allowed():
    row = _valid_row(theme=UNCLASSIFIED)
    row["dedup_key"] = build_dedup_key(row["operation"], row["theme"], row["failure_mode"])
    result = validate_normalized_line(row)
    assert result.ok, result.errors


def test_dedup_key_mismatch_fails():
    row = _valid_row()
    row["dedup_key"] = "OP10|packaging_material|something else entirely"
    result = validate_normalized_line(row)
    assert not result.ok
    assert any("dedup_key" in e for e in result.errors)


def test_rpn_only_row_must_flag_needs_rerating_and_have_no_ap():
    row = _valid_row(ap=None, needs_rerating=True)
    result = validate_normalized_line(row)
    assert result.ok, result.errors


def test_needs_rerating_with_ap_set_fails():
    """An RPN-only source must never have a fabricated AP — spec §4:
    'You cannot back-convert RPN -> AP... Never fabricate.'"""
    row = _valid_row(ap="L", needs_rerating=True)
    result = validate_normalized_line(row)
    assert not result.ok
    assert any("needs_rerating" in e for e in result.errors)


def test_rated_row_missing_ap_without_flag_fails():
    row = _valid_row(ap=None, needs_rerating=False)
    result = validate_normalized_line(row)
    assert not result.ok
    assert any("ap is missing" in e for e in result.errors)


def test_all_192_ground_truth_rows_have_themes_within_vocabulary_or_unclassified():
    """Sanity check tying the validator's theme rule back to the actual
    Phase 0 answer key (allows the one known UNCLASSIFIED row, #100)."""
    with FINAL_LINES_PATH.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]

    bad = [
        r["excel_row"] for r in rows
        if r["theme"] is not None
        and r["theme"] != UNCLASSIFIED
        and r["theme"] not in THEME_VOCABULARY
    ]
    assert not bad, f"themes outside frozen vocabulary at rows: {bad}"
