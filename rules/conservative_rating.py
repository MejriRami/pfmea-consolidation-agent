"""
Layer 1 — conservative rating selection.

Spec §3.5, decision #1 in PHASE_0_RESULTS.md: **Option B — worst-line wins.**

When multiple source rows collapse onto one final PFMEA row (baseline +
enrichment candidates matched to it), the surviving S/O/D triplet is not
built by taking max(S), max(O), max(D) independently (Option A) — that can
invent a triplet no plant ever actually assessed. Instead, take the *entire*
S/O/D from whichever source row has the highest severity, tiebreaking on
the highest AP, so the winning row stays internally coherent as a real
assessment. AP for the winner is then always recomputed via `get_ap` — a
row's own stored AP (if any) is never trusted, since AI/RPN sources are not
guaranteed to have derived it correctly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .ap_lookup import get_ap

_AP_RANK = {"L": 0, "M": 1, "H": 2}


@dataclass(frozen=True)
class Rating:
    """One candidate S/O/D assessment competing to win a final row."""
    severity: int
    occurrence: int
    detection: int
    source_id: str | None = None


@dataclass(frozen=True)
class ConservativeRating:
    """The winning triplet, with AP recomputed by lookup (never inferred)."""
    severity: int
    occurrence: int
    detection: int
    ap: str
    source_id: str | None


def select_conservative_rating(ratings: Sequence[Rating]) -> ConservativeRating:
    """Option B: worst-line wins. Highest severity; ties broken by highest AP.

    Remaining ties (equal severity, equal AP) resolve to the first such
    rating in `ratings`, so callers should pass candidates in a stable,
    deterministic order (e.g. by source_row_ref).
    """
    if not ratings:
        raise ValueError("select_conservative_rating: no candidate ratings given")

    def sort_key(r: Rating) -> tuple[int, int]:
        ap = get_ap(r.severity, r.occurrence, r.detection)
        return (r.severity, _AP_RANK[ap])

    winner = max(ratings, key=sort_key)
    ap = get_ap(winner.severity, winner.occurrence, winner.detection)
    return ConservativeRating(
        severity=winner.severity,
        occurrence=winner.occurrence,
        detection=winner.detection,
        ap=ap,
        source_id=winner.source_id,
    )
