import json

import pytest

from rules import APLookupError, get_ap, table_size
from rules.ap_lookup import DEFAULT_AP_LOOKUP_PATH


def _ground_truth_table():
    raw = json.loads(DEFAULT_AP_LOOKUP_PATH.read_text(encoding="utf-8"))
    table = {}
    for key, ap in raw.items():
        s, o, d = (int(x) for x in key.split("-"))
        table[(s, o, d)] = ap
    return table


def test_table_has_1000_entries():
    assert table_size() == 1000


def test_get_ap_reproduces_every_ground_truth_entry():
    """Done-when criterion: get_ap reproduces all 1000 lookup entries."""
    table = _ground_truth_table()
    assert len(table) == 1000
    mismatches = []
    for (s, o, d), expected_ap in table.items():
        actual = get_ap(s, o, d)
        if actual != expected_ap:
            mismatches.append((s, o, d, expected_ap, actual))
    assert not mismatches, f"{len(mismatches)} mismatches, e.g. {mismatches[:5]}"


def test_table_is_a_complete_cartesian_1_to_10():
    table = _ground_truth_table()
    for s in range(1, 11):
        for o in range(1, 11):
            for d in range(1, 11):
                assert (s, o, d) in table, f"missing S={s} O={o} D={d}"


def test_get_ap_never_infers_out_of_range_triplet():
    with pytest.raises(APLookupError):
        get_ap(11, 1, 1)


@pytest.mark.parametrize("s,o,d", [(1, 1, 1), (10, 10, 10), (5, 5, 5)])
def test_get_ap_returns_upper_single_letter(s, o, d):
    assert get_ap(s, o, d) in {"H", "M", "L"}


def test_ap_anchor_known_defect_documented_not_fixed():
    """PHASE_0_RESULTS.md defect #4: the table says 5-5-5 -> L, though
    AIAG-VDA convention suggests M. get_ap is a lookup only — it must
    reproduce the table as-is, not "correct" it. If this ever flips, it
    means 21_AP_LOOKUP was regenerated and the anchor check should be
    revisited (see spec §9 / PHASE_0_RESULTS.md item 4)."""
    assert get_ap(5, 5, 5) == "L"
