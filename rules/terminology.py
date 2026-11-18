"""
Layer 1 — controlled terminology glossary for NORMALIZER (spec §5.2).

A fixed, deterministic foreign-term -> canonical-English-term lookup.
NORMALIZER (the AI) always translates freely, but for `canonical_term` it
should prefer an entry from this glossary over inventing its own English
phrasing, so the same underlying concept gets the same canonical label
across documents/languages. Not a closed set the AI is restricted to for
`english` (free translation is still required) — only for `canonical_term`.

Seed entries are the spec's own explicit example (spec §5.2: 'Terminology
rule: FR "balais" -> "carbon brush" (phase 2, but encode now)') plus terms
actually observed repeating across the LINE10 bilingual file and the SITE_B/SITE_C
Spanish-template files during Phase 2 inspection. This is a starting
glossary, not exhaustive — extend it as new recurring terms are found.
"""
from __future__ import annotations

TERMINOLOGY: dict[str, str] = {
    # French — spec §5.2's explicit example. Brushes are out of scope for
    # this pilot (chokes only), but the mapping is encoded now per spec.
    "balais": "carbon brush",
    # Chinese — observed recurring in PFMEA_CUSTA_LINE10 bilingual cells
    # (spec §4: "one cell, two languages").
    "磁芯": "ferrite core",
    "感芯": "ferrite core",
    "感值": "inductance",
    "阻抗": "impedance",
    "磁导率": "permeability",
    "拉力": "pulling force",
    "供应商": "supplier",
    "检验员": "inspector",
    "客户": "customer",
    "卡尺": "caliper",
    "千分尺": "micrometer",
    "绕线": "winding",
    "混料": "mixed materials",
    "镀锡": "tinning",
    "剥皮": "stripping",
    # French — the typo class documented in PHASE_0_RESULTS.md item 6 /
    # spec §9.4: "Inetrne"/"Inetrn" throughout Function of the Process
    # Item is a misspelling of "Interne" (French: internal).
    "interne": "internal",
}


def lookup_canonical_term(text: str) -> str | None:
    """Case-insensitive substring lookup against the glossary. Returns the
    first matching canonical term, or None. This is a deterministic aid —
    NORMALIZER (the AI) makes the final call on `canonical_term`, but should
    not need to invent a label for anything already in this glossary."""
    lowered = text.lower()
    for term, canonical in TERMINOLOGY.items():
        if term.lower() in lowered:
            return canonical
    return None
