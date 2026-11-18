import pytest
from pydantic import ValidationError

from pfmea_agents.normalizer import (
    NormalizerOutput,
    build_normalizer_agent,
    is_plain_ascii,
)


def test_is_plain_ascii():
    assert is_plain_ascii("Damaged raw material packaging")
    assert not is_plain_ascii("磁芯尺寸错判 Misjudgment of core size")
    assert not is_plain_ascii("balais usés")  # accented é


def test_output_requires_original_wording_and_english():
    with pytest.raises(ValidationError):
        NormalizerOutput(english="Damaged packaging", confidence=0.9)


def test_canonical_term_defaults_to_none():
    out = NormalizerOutput(original_wording="x", english="x", confidence=0.9)
    assert out.canonical_term is None


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        NormalizerOutput(original_wording="x", english="x", confidence=1.1)
    with pytest.raises(ValidationError):
        NormalizerOutput(original_wording="x", english="x", confidence=-0.1)


def test_valid_output_round_trips():
    out = NormalizerOutput(
        original_wording="Inetrne]: Ensure raw materials are compliant",
        english="Internal: Ensure raw materials are compliant",
        canonical_term=None,
        confidence=0.95,
    )
    assert out.original_wording != out.english  # typo preserved verbatim on one side, fixed on the other


def test_build_agent_references_the_glossary_and_typo_rule():
    agent = build_normalizer_agent()
    assert "balais" in agent.instructions
    assert "carbon brush" in agent.instructions
    assert "Inetrne" in agent.instructions
    assert agent.output_type is NormalizerOutput
