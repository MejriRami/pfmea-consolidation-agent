"""
Orchestrates the per-row consolidation algorithm end to end, wiring
Layer 1 (rules.operations.resolve_operation) and Layer 2 (NORMALIZER,
ADJUDICATOR) into the full raw-rows -> decisions replay the Phase 4
benchmark measures.

REDESIGN — no mechanical dedup pre-filter. The original design (spec §3.2)
used an exact dedup-key pre-filter (operation+theme+failure_mode string
match); a later revision added an embedding-similarity fallback. Both were
tested live and both failed for structural reasons:
  - Exact key: 1 collision across 719 rows (independently-normalized LLM
    text rarely matches byte-for-byte).
  - Embedding similarity (three field combinations tried: mode+cause,
    mode-only, effect-only): best Youden's J across all three was 0.068 —
    no real separating signal between EXCLUDE_DUPLICATE and
    INCLUDE_ENRICHMENT in this embedding space, for this dataset.
  - Operation+theme category membership: tested against ground-truth
    themes directly (zero AI noise) and still has a ~29% false-positive
    rate on genuine enrichments — theme is a coarser bucket than "the same
    specific failure," so this ceiling exists even with perfect labels.
    AI-generated themes compound it further, and the resulting errors are
    NOT concentrated in one or two bad themes — they're spread across 15+
    of the 42, meaning the disagreement is pervasive, not a couple of
    fixable outliers.

So: no pre-filter. Every non-baseline candidate is compared against EVERY
baseline row in its own operation via ONE ADJUDICATOR call — the one
component in this pipeline actually designed to make a holistic,
content-aware duplicate/enrichment judgment, rather than trying to find a
cheap mechanical proxy for it. Operation resolution is the only
mechanical step left, used purely to size the comparison set — a
candidate is never compared across operations. THEMER is no longer part
of the matching decision at all (theme was only ever needed to build the
now-abandoned dedup key).

Every AI call is injected as a plain function (NormalizeFn / AdjudicateFn),
not imported directly — the control flow (baseline short-circuit,
same-operation grouping, the confidence-threshold gate) is fully
unit-testable with fake agents, no API key required. eval_replay.py wires
in the real agents for the live benchmark.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rules.operations import resolve_operation

from .adjudicator import AdjudicatorInput, AdjudicatorOutput, BaselineRow, CandidateRow

BASELINE_SOURCE = "MASTER_REF"
PFC_SOURCES = {"SITE_D_PFC"}
DEFAULT_ADJUDICATOR_THRESHOLD = 0.85  # Phase 0 decision #4 (PHASE_0_RESULTS.md)

NormalizeFn = Callable[[str], str]  # raw text -> english text
AdjudicateFn = Callable[[AdjudicatorInput], AdjudicatorOutput]


@dataclass
class RowDecision:
    row_id: str
    source: str
    # INCLUDE_BASELINE | INCLUDE_ENRICHMENT | EXCLUDE_DUPLICATE | EXCLUDE_PFC
    # | NEEDS_OPERATION_REVIEW (this pipeline's addition — a raw process_step
    # sometimes has no honest single-operation answer, e.g. a source row
    # bundling three operations into one step description; see
    # rules.operations.resolve_operation's docstring).
    decision: str
    operation: str | None = None
    absorbed_by: str | None = None
    justification: str | None = None
    confidence: float | None = None
    # How many baseline rows ADJUDICATOR was shown for this candidate (0 if
    # the operation had no baseline at all, None if never adjudicated) —
    # kept on the decision for cost/debug visibility, not used in scoring.
    baseline_candidates_shown: int | None = None


@dataclass
class ResolvedRow:
    """A raw row after operation resolution and English normalization."""
    raw: dict
    operation: str | None
    failure_mode_en: str
    failure_cause_en: str
    failure_effect_en: str


def resolve_row(row: dict, *, normalize: NormalizeFn) -> ResolvedRow:
    failure_mode_en = normalize(row.get("failure_mode") or "")
    failure_cause_en = normalize(row.get("failure_cause") or "")
    failure_effect_en = normalize(row.get("failure_effect") or "")
    operation = resolve_operation(row.get("process_step") or "").code
    return ResolvedRow(
        raw=row, operation=operation,
        failure_mode_en=failure_mode_en, failure_cause_en=failure_cause_en, failure_effect_en=failure_effect_en,
    )


def _to_baseline_row(resolved: ResolvedRow) -> BaselineRow:
    r = resolved.raw
    return BaselineRow(
        row_id=r["row_id"],
        process_step=r.get("process_step") or "",
        failure_mode_en=resolved.failure_mode_en,
        failure_effect_en=resolved.failure_effect_en,
        failure_cause_en=resolved.failure_cause_en,
        severity=r.get("severity"), occurrence=r.get("occurrence"), detection=r.get("detection"),
    )


def _to_candidate_row(resolved: ResolvedRow) -> CandidateRow:
    r = resolved.raw
    return CandidateRow(
        row_id=r["row_id"], source=r["source"], operation=resolved.operation or "",
        process_step=r.get("process_step") or "",
        failure_mode_en=resolved.failure_mode_en,
        failure_effect_en=resolved.failure_effect_en,
        failure_cause_en=resolved.failure_cause_en,
        severity=r.get("severity"), occurrence=r.get("occurrence"), detection=r.get("detection"),
    )


def replay(
    raw_rows: list[dict],
    *,
    normalize: NormalizeFn,
    adjudicate: AdjudicateFn,
    adjudicator_threshold: float = DEFAULT_ADJUDICATOR_THRESHOLD,
) -> list[RowDecision]:
    """Baseline rows always INCLUDE; PFC rows always EXCLUDE; every other
    row is compared, in one ADJUDICATOR call, against every baseline row
    sharing its resolved operation. `raw_rows` are dicts shaped like
    ground_truth/raw_lines.jsonl records (need at least: row_id, source,
    process_step, failure_mode, failure_cause, failure_effect, and
    optionally severity/occurrence/detection)."""
    decisions: list[RowDecision] = []
    baseline_by_operation: dict[str, list[ResolvedRow]] = {}

    # Pass 1: baseline rows always INCLUDE unconditionally, but still need
    # resolving (operation + English text) so candidates have something to
    # compare against.
    for row in raw_rows:
        if row["source"] != BASELINE_SOURCE:
            continue
        resolved = resolve_row(row, normalize=normalize)
        decisions.append(RowDecision(
            row_id=row["row_id"], source=row["source"], decision="INCLUDE_BASELINE",
            operation=resolved.operation,
        ))
        if resolved.operation:
            baseline_by_operation.setdefault(resolved.operation, []).append(resolved)

    # Pass 2: everything else.
    for row in raw_rows:
        source = row["source"]
        if source == BASELINE_SOURCE:
            continue
        if source in PFC_SOURCES:
            decisions.append(RowDecision(row_id=row["row_id"], source=source, decision="EXCLUDE_PFC"))
            continue

        operation = resolve_operation(row.get("process_step") or "").code
        if operation is None:
            decisions.append(RowDecision(
                row_id=row["row_id"], source=source, decision="NEEDS_OPERATION_REVIEW",
            ))
            continue

        same_op_baselines = baseline_by_operation.get(operation, [])
        if not same_op_baselines:
            decisions.append(RowDecision(
                row_id=row["row_id"], source=source, decision="INCLUDE_ENRICHMENT",
                operation=operation,
                justification=f"NON_DUPLICATE_ENRICHMENT_FROM_{source} (no baseline rows in {operation})",
                baseline_candidates_shown=0,
            ))
            continue

        resolved = resolve_row(row, normalize=normalize)
        adjudicator_input = AdjudicatorInput(
            candidate_row=_to_candidate_row(resolved),
            matching_baseline_rows=[_to_baseline_row(b) for b in same_op_baselines],
        )
        result = adjudicate(adjudicator_input)

        if result.decision == "ENRICHMENT" and result.confidence >= adjudicator_threshold:
            decisions.append(RowDecision(
                row_id=row["row_id"], source=source, decision="INCLUDE_ENRICHMENT", operation=operation,
                justification=result.justification, confidence=result.confidence,
                baseline_candidates_shown=len(same_op_baselines),
            ))
        else:
            # A low-confidence ENRICHMENT call that never named an absorber
            # falls back to the first same-operation baseline rather than
            # leaving absorbed_by unset.
            absorbed_by = result.absorbed_by or same_op_baselines[0].raw["row_id"]
            decisions.append(RowDecision(
                row_id=row["row_id"], source=source, decision="EXCLUDE_DUPLICATE", operation=operation,
                absorbed_by=absorbed_by, justification=result.justification, confidence=result.confidence,
                baseline_candidates_shown=len(same_op_baselines),
            ))

    return decisions
