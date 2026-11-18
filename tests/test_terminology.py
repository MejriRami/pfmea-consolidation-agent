from rules.terminology import TERMINOLOGY, lookup_canonical_term


def test_spec_example_fr_balais_to_carbon_brush():
    """spec §5.2's explicit example: FR "balais" -> "carbon brush"."""
    assert TERMINOLOGY["balais"] == "carbon brush"
    assert lookup_canonical_term("remplacer les balais usés") == "carbon brush"


def test_lookup_is_case_insensitive():
    assert lookup_canonical_term("BALAIS") == "carbon brush"
    assert lookup_canonical_term("Interne : no compliant product") == "internal"


def test_lookup_returns_none_when_nothing_matches():
    assert lookup_canonical_term("this text matches nothing in the glossary") is None


def test_chinese_terms_observed_in_esp10_file_are_present():
    for term in ("磁芯", "感值", "阻抗", "磁导率"):
        assert term in TERMINOLOGY


def test_all_glossary_values_are_lowercase_english_phrases():
    for canonical in TERMINOLOGY.values():
        assert canonical == canonical.lower(), f"{canonical!r} should be lowercase for consistency"
