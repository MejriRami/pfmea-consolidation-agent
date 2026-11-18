from pfmea_agents.ground_truth_alignment import check_alignment


def test_partitions_cover_all_192_final_rows():
    report = check_alignment()
    total = len(report.aligned) + len(report.desynced) + len(report.no_dedup_key)
    assert total == 192


def test_no_dedup_key_rows_are_the_two_documented_phase0_orphans():
    """PHASE_0_RESULTS.md documents exactly two orphan final rows: excel_row
    45 (GEMBA-004, no source_row_refs at all) and 78 (00.0.30, a reference
    that doesn't resolve). This independently re-derives the same two rows
    via a different signal (no parseable failure-mode segment), which is a
    useful cross-check that the two defects are related, not coincidental."""
    report = check_alignment()
    rows = {r["excel_row"] for r in report.no_dedup_key}
    assert rows == {45, 78}


def test_desync_defect_is_present_and_material_at_default_threshold():
    """Locks in the scope of the AW/failure_mode desync defect discovered
    while building the Phase 3 eval, so a ground_truth/ update that
    silently changes this doesn't go unnoticed. See ground_truth_alignment.py
    module docstring for the full writeup; row 60 is a directly-verified
    example (confirmed against the raw workbook, not just this repo's own
    extraction)."""
    report = check_alignment()
    desynced_rows = {r["excel_row"] for r in report.desynced}
    assert 60 in desynced_rows
    assert 63 in desynced_rows
    # Roughly a quarter of the answer key — this is not a handful of one-offs.
    assert 40 <= len(report.desynced) <= 60


def test_aligned_rows_independently_recompute_above_threshold():
    """Cross-check using a fresh Jaccard computation in the test itself,
    rather than trusting check_alignment()'s internal math unverified."""
    import re

    report = check_alignment(threshold=0.15)

    def tokens(s):
        return set(re.findall(r"[a-z0-9]+", s.lower()))

    def parse_key_fm(raw):
        parts = str(raw).split("|")
        return "|".join(parts[2:]).strip().lower()

    for r in report.aligned[:20]:
        key_fm = parse_key_fm(r["dedup_key_original"])
        a, b = tokens(key_fm), tokens(r["failure_mode"])
        overlap = len(a & b) / len(a | b) if (a and b) else 0.0
        assert overlap >= 0.15, (r["excel_row"], overlap)


def test_higher_threshold_flags_more_rows_as_desynced():
    loose = check_alignment(threshold=0.05)
    strict = check_alignment(threshold=0.5)
    assert len(strict.desynced) >= len(loose.desynced)
