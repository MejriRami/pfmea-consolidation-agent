"""
Layer 1 — dedup key construction.

Format (column AW of 02_FINAL_PFMEA, observed verbatim):

    OP10|traceability_label|damaged raw material packaging
    └─┬─┘ └───────┬───────┘ └──────────────┬─────────────┘
      op    semantic theme    normalized failure mode (lowercased)

This MUST match extract_ground_truth.py::build_dedup_key byte-for-byte —
that function is what produced the ground truth this engine is measured
against. Do not "improve" the normalization here without re-running Phase 0.
"""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def build_dedup_key(op: str, theme: str, failure_mode: str) -> str:
    """Canonical key construction: OP|theme|normalized_failure_mode."""
    fm = _WHITESPACE.sub(" ", (failure_mode or "")).strip().lower()
    return f"{op}|{theme}|{fm}"
