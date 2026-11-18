"""
Tests the redesigned orchestration logic (pfmea_agents.pipeline.replay):
no mechanical dedup pre-filter, every candidate compared against every
same-operation baseline row via a single ADJUDICATOR call. Uses fake
NORMALIZER/ADJUDICATOR functions — no OPENAI_API_KEY needed. This is
exactly the control flow the Phase 4 go/no-go gate depends on being
correct: the baseline short-circuit, same-operation grouping (never
cross-operation), and the confidence-threshold include/exclude gate.
"""
from __future__ import annotations

import pytest

from pfmea_agents.adjudicator import AdjudicatorOutput
from pfmea_agents.pipeline import replay


def make_row(row_id, source, process_step, failure_mode, failure_cause="cause", failure_effect="effect",
             severity=7, occurrence=3, detection=3):
    return {
        "row_id": row_id, "source": source, "process_step": process_step,
        "failure_mode": failure_mode, "failure_cause": failure_cause, "failure_effect": failure_effect,
        "severity": severity, "occurrence": occurrence, "detection": detection,
    }


def identity_normalize(text: str) -> str:
    return text


def fixed_adjudication(decision: str, confidence: float, absorbed_by: str | None = None,
                        justification: str = "test"):
    def adjudicate_fn(adjudicator_input):
        return AdjudicatorOutput(
            decision=decision, justification=justification, absorbed_by=absorbed_by, confidence=confidence,
        )
    return adjudicate_fn


def never_called(*_args, **_kwargs):
    raise AssertionError("should not have been called")


def counting(fn):
    calls = []

    def wrapper(*args, **kwargs):
        calls.append((args, kwargs))
        return fn(*args, **kwargs)

    wrapper.calls = calls
    return wrapper


def test_master_ref_always_included_regardless_of_adjudicator():
    rows = [make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging")]
    results = replay(rows, normalize=identity_normalize, adjudicate=never_called)
    assert results[0].decision == "INCLUDE_BASELINE"


def test_pfc_source_always_excluded_and_never_calls_any_agent():
    rows = [make_row("SITE_D_PFC_A-1", "SITE_D_PFC", "RM incoming inspection", "some PFC content")]
    results = replay(rows, normalize=never_called, adjudicate=never_called)
    assert results[0].decision == "EXCLUDE_PFC"


def test_no_baseline_in_same_operation_included_without_adjudicating():
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),  # OP10
        make_row("GEMBA-001", "Gemba + AI Suggestions", "Coil winding", "Loose Ferrite"),  # OP60, no baseline there
    ]
    results = replay(rows, normalize=identity_normalize, adjudicate=never_called)
    gemba = next(r for r in results if r.row_id == "GEMBA-001")
    assert gemba.decision == "INCLUDE_ENRICHMENT"
    assert gemba.baseline_candidates_shown == 0


def test_unresolvable_operation_routes_to_needs_operation_review_without_adjudicating():
    rows = [make_row("SITE_B-999", "SITE_B", "Warehouse", "Missing material on production line")]
    results = replay(rows, normalize=identity_normalize, adjudicate=never_called)
    assert results[0].decision == "NEEDS_OPERATION_REVIEW"


def test_adjudicator_is_shown_every_baseline_row_in_the_operation():
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),
        make_row("00.0.2", "MASTER_REF", "RM incoming inspection", "Wrong copper wire diameter"),
        make_row("00.0.3", "MASTER_REF", "RM incoming inspection", "Missing traceability label"),
        make_row("SITE_B-005", "SITE_B", "RM incoming inspection", "Contaminated packaging material"),
    ]
    adjudicate = counting(fixed_adjudication("ENRICHMENT", confidence=0.9))
    replay(rows, normalize=identity_normalize, adjudicate=adjudicate)

    assert len(adjudicate.calls) == 1
    (adjudicator_input,), _kwargs = adjudicate.calls[0]
    assert len(adjudicator_input.matching_baseline_rows) == 3
    assert {b.row_id for b in adjudicator_input.matching_baseline_rows} == {"00.0.1", "00.0.2", "00.0.3"}


def test_never_compares_across_different_operations():
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),  # OP10
        make_row("00.0.2", "MASTER_REF", "Coil winding", "Wrong number of turns"),         # OP60
        make_row("SITE_B-005", "SITE_B", "RM incoming inspection", "Torn packaging"),    # OP10 candidate
    ]
    adjudicate = counting(fixed_adjudication("ENRICHMENT", confidence=0.9))
    replay(rows, normalize=identity_normalize, adjudicate=adjudicate)

    (adjudicator_input,), _kwargs = adjudicate.calls[0]
    assert [b.row_id for b in adjudicator_input.matching_baseline_rows] == ["00.0.1"]  # not 00.0.2 (OP60)


def test_duplicate_decision_excludes_and_records_absorbed_by():
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),
        make_row("SITE_B-005", "SITE_B", "RM incoming inspection", "Damaged raw material packaging"),
    ]
    results = replay(
        rows, normalize=identity_normalize,
        adjudicate=fixed_adjudication("DUPLICATE", confidence=0.95, absorbed_by="00.0.1"),
    )
    candidate = next(r for r in results if r.row_id == "SITE_B-005")
    assert candidate.decision == "EXCLUDE_DUPLICATE"
    assert candidate.absorbed_by == "00.0.1"
    assert candidate.baseline_candidates_shown == 1


def test_enrichment_above_threshold_included():
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),
        make_row("SITE_B-005", "SITE_B", "RM incoming inspection", "Wrong copper wire diameter"),
    ]
    results = replay(
        rows, normalize=identity_normalize,
        adjudicate=fixed_adjudication("ENRICHMENT", confidence=0.90),
    )
    candidate = next(r for r in results if r.row_id == "SITE_B-005")
    assert candidate.decision == "INCLUDE_ENRICHMENT"
    assert candidate.confidence == 0.90


def test_enrichment_below_threshold_is_excluded_not_included():
    """A low-confidence ENRICHMENT call must still exclude — confidence is
    what gates inclusion, not the decision label alone."""
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),
        make_row("SITE_B-005", "SITE_B", "RM incoming inspection", "Wrong copper wire diameter"),
    ]
    results = replay(
        rows, normalize=identity_normalize,
        adjudicate=fixed_adjudication("ENRICHMENT", confidence=0.50),
        adjudicator_threshold=0.85,
    )
    candidate = next(r for r in results if r.row_id == "SITE_B-005")
    assert candidate.decision == "EXCLUDE_DUPLICATE"


def test_low_confidence_enrichment_with_no_absorbed_by_falls_back_to_first_baseline():
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),
        make_row("SITE_B-005", "SITE_B", "RM incoming inspection", "Wrong copper wire diameter"),
    ]
    results = replay(
        rows, normalize=identity_normalize,
        adjudicate=fixed_adjudication("ENRICHMENT", confidence=0.10, absorbed_by=None),
        adjudicator_threshold=0.85,
    )
    candidate = next(r for r in results if r.row_id == "SITE_B-005")
    assert candidate.decision == "EXCLUDE_DUPLICATE"
    assert candidate.absorbed_by == "00.0.1"


def test_threshold_boundary_is_inclusive():
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),
        make_row("SITE_B-005", "SITE_B", "RM incoming inspection", "Wrong copper wire diameter"),
    ]
    results = replay(
        rows, normalize=identity_normalize,
        adjudicate=fixed_adjudication("ENRICHMENT", confidence=0.85),
        adjudicator_threshold=0.85,
    )
    candidate = next(r for r in results if r.row_id == "SITE_B-005")
    assert candidate.decision == "INCLUDE_ENRICHMENT"


def test_multiple_candidates_each_get_their_own_adjudicator_call():
    rows = [
        make_row("00.0.1", "MASTER_REF", "RM incoming inspection", "Damaged packaging"),
        make_row("SITE_B-005", "SITE_B", "RM incoming inspection", "Damaged packaging"),
        make_row("SITE_C-005", "SITE_C", "RM incoming inspection", "Damaged packaging"),
    ]
    adjudicate = counting(fixed_adjudication("DUPLICATE", confidence=0.9, absorbed_by="00.0.1"))
    results = replay(rows, normalize=identity_normalize, adjudicate=adjudicate)

    assert len(adjudicate.calls) == 2  # one per candidate, not batched
    excluded = [r for r in results if r.decision == "EXCLUDE_DUPLICATE"]
    assert len(excluded) == 2
