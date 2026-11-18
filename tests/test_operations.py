import json
from pathlib import Path

import pytest

from rules.operations import OPERATIONS, resolve_operation

RAW_LINES_PATH = Path(__file__).resolve().parent.parent / "ground_truth" / "raw_lines.jsonl"


def test_twenty_operations_op10_through_op200():
    codes = [op.code for op in OPERATIONS]
    assert codes == [f"OP{n}" for n in range(10, 201, 10)]


@pytest.mark.parametrize("process_step,expected_op", [
    ("RM incoming inspection", "OP10"),
    ("Incoming Inspection", "OP10"),
    ("Coil winding", "OP60"),
    ("Final visual", "OP180"),
    ("OP40 — Denoising / stripping\nVarnish removal / wire exposure", "OP40"),
])
def test_resolves_clean_cases_confidently(process_step, expected_op):
    match = resolve_operation(process_step)
    assert match.code == expected_op


def test_op_prefix_fast_path_takes_priority():
    match = resolve_operation("OP160 whatever else is written here")
    assert match.code == "OP160"


def test_unrelated_text_resolves_to_none():
    match = resolve_operation("Warehouse")
    assert match.code is None
    assert match.score == 0


def test_ambiguous_text_resolves_to_none_not_a_guess():
    """SITE_B's own real process_step: bundles stripping + tinning + winding,
    three separate operations in the canonical flow. There is no single
    honest answer -- must not silently pick one."""
    match = resolve_operation("Forming (Winding, stripping & tinning)")
    assert match.code is None
    assert len(match.ambiguous_with) > 1


def test_empty_input_resolves_to_none():
    assert resolve_operation("").code is None
    assert resolve_operation(None).code is None


@pytest.mark.parametrize("process_step,expected_op", [
    # Regression cases for the name/detail double-count bug: a token
    # appearing in both an operation's name AND its own detail (e.g.
    # OP50's "tinning") must not out-score two single, clean matches
    # elsewhere just from being counted twice against itself.
    ("10.3: tinning", "OP50"),
    ("10.2 :Winding", "OP60"),
    ("Final control", "OP180"),
    ("Packaging", "OP190"),
    ("Manual gluing", "OP110"),
    ("Drying", "OP140"),
    ("10.4 : cutting", "OP70"),
    ("10.0:Straightening", "OP30"),
])
def test_resolves_ground_truth_derived_vocabulary_gaps(process_step, expected_op):
    assert resolve_operation(process_step).code == expected_op


def test_double_counted_name_and_detail_token_does_not_break_genuine_ambiguity():
    """OP50's name AND detail both contain "tinning" ("Tinning / solder
    bath" / "...tinning length"). Before fixing the double-count, that
    let OP50 win outright on a query spanning several operations equally,
    masking a genuine ambiguity. Must still resolve to None."""
    match = resolve_operation("Forming (Winding, stripping & tinning)")
    assert match.code is None
    assert len(match.ambiguous_with) >= 2


def test_resolves_every_non_pfc_row_in_the_actual_ground_truth():
    """Not a spec requirement by itself, but the whole point of this
    module: an operation resolver that flags half of real rows as
    unresolved is useless for the Phase 4 replay, however principled its
    'never guess' behavior is in isolation. Locks in 100% coverage on the
    719-row corpus so a future change can't silently regress it."""
    rows = [json.loads(line) for line in RAW_LINES_PATH.open(encoding="utf-8")]
    non_pfc = [r for r in rows if r["source"] != "SITE_D_PFC"]

    unresolved = [
        r["process_step"] for r in non_pfc
        if resolve_operation(r["process_step"] or "").code is None
    ]
    assert not unresolved, f"{len(unresolved)}/{len(non_pfc)} unresolved, e.g. {unresolved[:5]}"
