"""
Layer 1 — schema validation for `normalized_line` records.

Mirrors the CHECK constraints and NOT NULL columns of the `normalized_line`
table (spec §6). Pure functions: no AI, no DB access. Intended to run
before a normalized row is ever written to Postgres, and in tests against
the ground truth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dedup_key import build_dedup_key
from .vocabulary import THEME_VOCABULARY, UNCLASSIFIED

VALID_AP = {"H", "M", "L"}
VALID_FOUR_M = {"Man", "Machine", "Material", "Method"}
VALID_SPECIAL_CHAR = {"CC", "SC", "KPC"}
OP_PATTERN = re.compile(r"^OP\d+$")

REQUIRED_FIELDS = (
    "operation",
    "process_step",
    "failure_mode",
    "failure_effect",
    "failure_cause",
    "theme",
    "dedup_key",
)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _check_range(name: str, value, lo: int = 1, hi: int = 10) -> list[str]:
    if value is None:
        return [f"{name} is required"]
    if isinstance(value, bool) or not isinstance(value, int):
        return [f"{name}={value!r} must be an int"]
    if not (lo <= value <= hi):
        return [f"{name}={value!r} out of range [{lo},{hi}]"]
    return []


def validate_normalized_line(row: dict) -> ValidationResult:
    """Validate a normalized_line-shaped dict against the DB constraints.

    `row` uses the same field names as the `normalized_line` table columns
    (spec §6): operation, process_step, failure_mode, failure_effect,
    failure_cause, severity, occurrence, detection, ap, four_m_type,
    special_char, theme, dedup_key, needs_rerating.
    """
    errors: list[str] = []

    for name in REQUIRED_FIELDS:
        if not row.get(name):
            errors.append(f"missing required field: {name}")

    op = row.get("operation")
    if op and not OP_PATTERN.match(op):
        errors.append(f"operation {op!r} does not match OP<digits>")

    errors += _check_range("severity", row.get("severity"))
    errors += _check_range("occurrence", row.get("occurrence"))
    errors += _check_range("detection", row.get("detection"))

    ap = row.get("ap")
    if ap is not None and ap not in VALID_AP:
        errors.append(f"ap {ap!r} not in {sorted(VALID_AP)}")

    four_m = row.get("four_m_type")
    if four_m is not None and four_m not in VALID_FOUR_M:
        errors.append(f"four_m_type {four_m!r} not in {sorted(VALID_FOUR_M)}")

    special_char = row.get("special_char")
    if special_char is not None and special_char not in VALID_SPECIAL_CHAR:
        errors.append(f"special_char {special_char!r} not in {sorted(VALID_SPECIAL_CHAR)}")

    theme = row.get("theme")
    if theme is not None and theme != UNCLASSIFIED and theme not in THEME_VOCABULARY:
        errors.append(f"theme {theme!r} not in closed vocabulary and not {UNCLASSIFIED}")

    dedup_key = row.get("dedup_key")
    if dedup_key and op and theme:
        expected = build_dedup_key(op, theme, row.get("failure_mode", ""))
        if dedup_key != expected:
            errors.append(
                f"dedup_key {dedup_key!r} does not match build_dedup_key() -> {expected!r}"
            )

    needs_rerating = bool(row.get("needs_rerating", False))
    if needs_rerating and ap is not None:
        errors.append(
            "needs_rerating=True but ap is set — RPN-only rows must never carry a fabricated AP"
        )
    if not needs_rerating and ap is None and row.get("severity") is not None:
        errors.append(
            "ap is missing for a rated row and needs_rerating is not set — "
            "every rated row must have a looked-up ap or be flagged REQUIRES_MANUAL_RERATING"
        )

    return ValidationResult(errors=errors)
