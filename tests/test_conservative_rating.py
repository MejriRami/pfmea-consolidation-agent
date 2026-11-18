import pytest

from rules import Rating, get_ap, select_conservative_rating


def test_highest_severity_wins_outright():
    """Option B: the whole triplet comes from the highest-S row — never a
    per-dimension max (that would be Option A, which was rejected)."""
    low_severity_high_od = Rating(severity=5, occurrence=9, detection=9, source_id="A")
    high_severity = Rating(severity=9, occurrence=2, detection=2, source_id="B")

    result = select_conservative_rating([low_severity_high_od, high_severity])

    assert (result.severity, result.occurrence, result.detection) == (9, 2, 2)
    assert result.source_id == "B"


def test_does_not_invent_a_per_dimension_max_triplet():
    """Guards against silently reimplementing Option A."""
    a = Rating(severity=8, occurrence=2, detection=9, source_id="A")
    b = Rating(severity=6, occurrence=9, detection=2, source_id="B")

    result = select_conservative_rating([a, b])

    # Option A would produce (8, 9, 9) — a triplet nobody assessed.
    assert (result.severity, result.occurrence, result.detection) != (8, 9, 9)
    assert (result.severity, result.occurrence, result.detection) == (8, 2, 9)


def test_tiebreaks_on_highest_ap_when_severity_equal():
    # S=7: (O=1,D=1)->L, (O=4,D=7)->H — confirmed against ground_truth/ap_lookup.json
    assert get_ap(7, 1, 1) == "L"
    assert get_ap(7, 4, 7) == "H"

    low_ap = Rating(severity=7, occurrence=1, detection=1, source_id="low")
    high_ap = Rating(severity=7, occurrence=4, detection=7, source_id="high")

    result = select_conservative_rating([low_ap, high_ap])

    assert result.source_id == "high"
    assert result.ap == "H"


def test_ap_is_always_recomputed_via_lookup_not_trusted():
    only = Rating(severity=1, occurrence=1, detection=1, source_id="only")
    result = select_conservative_rating([only])
    assert result.ap == get_ap(1, 1, 1)


def test_single_candidate_returns_itself():
    r = Rating(severity=4, occurrence=4, detection=4, source_id="solo")
    result = select_conservative_rating([r])
    assert (result.severity, result.occurrence, result.detection, result.source_id) == (4, 4, 4, "solo")


def test_empty_candidates_raises():
    with pytest.raises(ValueError):
        select_conservative_rating([])


def test_stable_tiebreak_on_full_tie_prefers_first_given():
    a = Rating(severity=5, occurrence=5, detection=5, source_id="first")
    b = Rating(severity=5, occurrence=5, detection=5, source_id="second")
    result = select_conservative_rating([a, b])
    assert result.source_id == "first"
