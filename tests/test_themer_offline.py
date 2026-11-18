import pytest
from pydantic import ValidationError

from rules import THEME_VOCABULARY, UNCLASSIFIED
from pfmea_agents.themer import ThemerInput, ThemerOutput, build_themer_agent
from pfmea_agents.theme_catalog import THEME_CATALOG


def test_theme_accepts_every_vocabulary_entry():
    for theme in THEME_VOCABULARY:
        out = ThemerOutput(theme=theme, confidence=0.5)
        assert out.theme == theme


def test_theme_accepts_unclassified():
    out = ThemerOutput(theme=UNCLASSIFIED, confidence=0.1)
    assert out.theme == UNCLASSIFIED


def test_theme_rejects_value_outside_closed_vocabulary():
    with pytest.raises(ValidationError):
        ThemerOutput(theme="not_a_real_theme", confidence=0.9)


def test_theme_rejects_case_variants_not_in_vocabulary():
    with pytest.raises(ValidationError):
        ThemerOutput(theme="Core_Missing", confidence=0.9)


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        ThemerOutput(theme=UNCLASSIFIED, confidence=1.5)


def test_themer_input_requires_all_four_fields():
    with pytest.raises(ValidationError):
        ThemerInput(operation="OP10", process_step="x", failure_mode_en="y")


def test_build_agent_embeds_full_catalog_and_all_themes():
    agent = build_themer_agent()
    for theme in THEME_VOCABULARY:
        assert theme in agent.instructions, f"{theme} missing from THEMER instructions"
    assert UNCLASSIFIED in agent.instructions
    assert agent.output_type is ThemerOutput


def test_catalog_examples_are_nonempty_strings_where_present():
    for theme, entry in THEME_CATALOG.items():
        assert entry.description.strip(), theme
        for example in entry.examples:
            assert example.strip(), theme
