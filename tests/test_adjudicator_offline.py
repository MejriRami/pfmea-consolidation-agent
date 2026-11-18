import pytest
from pydantic import ValidationError

from pfmea_agents.adjudicator import (
    AdjudicatorInput,
    AdjudicatorOutput,
    BaselineRow,
    CandidateRow,
    build_adjudicator_agent,
)


def _candidate(**overrides):
    defaults = dict(
        row_id="SITE_B-030", source="SITE_B", operation="OP50",
        process_step="Forming", failure_mode_en="Missing coil turns",
        failure_effect_en="Reduced inductance", failure_cause_en="Wrong program",
        severity=7, occurrence=3, detection=3,
    )
    defaults.update(overrides)
    return CandidateRow(**defaults)


def _baseline(**overrides):
    defaults = dict(
        row_id="00.0.31", process_step="Coil winding",
        failure_mode_en="Missing coil turns", failure_effect_en="Reduced inductance",
        failure_cause_en="Wrong program", severity=7, occurrence=3, detection=3,
    )
    defaults.update(overrides)
    return BaselineRow(**defaults)


def test_input_requires_at_least_one_matching_baseline_row():
    with pytest.raises(ValidationError):
        AdjudicatorInput(candidate_row=_candidate(), matching_baseline_rows=[])


def test_valid_input_round_trips():
    data = AdjudicatorInput(candidate_row=_candidate(), matching_baseline_rows=[_baseline()])
    assert data.matching_baseline_rows[0].row_id == "00.0.31"


def test_output_decision_restricted_to_duplicate_or_enrichment():
    with pytest.raises(ValidationError):
        AdjudicatorOutput(decision="MAYBE", justification="x", confidence=0.9)


def test_output_allows_null_absorbed_by_for_enrichment():
    out = AdjudicatorOutput(decision="ENRICHMENT", justification="new risk", confidence=0.9)
    assert out.absorbed_by is None


def test_output_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        AdjudicatorOutput(decision="DUPLICATE", justification="x", absorbed_by="00.0.31", confidence=1.5)


def test_build_agent_encodes_the_recall_asymmetry():
    agent = build_adjudicator_agent()
    assert "recall" in agent.instructions.lower() or "asymmetry" in agent.instructions.lower()
    assert "quality escape" in agent.instructions.lower()
    assert agent.output_type is AdjudicatorOutput
