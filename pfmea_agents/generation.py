"""
PFMEA generation -- a two-layer "create a new PFMEA" workflow.

LAYER 1 (proven content, read-only, no AI): list_product_families,
list_operations, generate_pfmea. ASSEMBLES the existing, approved
standard_line knowledge base into a new PFMEA row set; never invents
content. Every row carries content_type=CONTENT_TYPE_VALIDATED plus its
standard_line provenance (id/line_key/version/approved_by/approved_at).

LAYER 2 (AI suggestions, on top of Layer 1, never blended with it):
suggest_additions. Proposes failure modes/causes/controls for gaps Layer 1
can't fill -- content NOT in the knowledge base, from the model's general
PFMEA engineering knowledge, prompted with what Layer 1 already found so
it proposes gaps, not duplicates. Spec: "Present weak or missing controls
as review comments or improvement suggestions, not as automatically
validated standard content." Enforced structurally, not just by
convention: every suggestion carries content_type=CONTENT_TYPE_AI_SUGGESTION
and has NO id/line_key/version/approved_by/approved_at/status keys at all
(there is nothing to have -- it was never a standard_line row), and this
module never writes a suggestion to standard_line under any code path. The
only way a suggestion becomes real content is the existing human-only
approval path (db/standard_line_writer.py's approve_standard_line, driven
by approve_to_db.py) -- exactly like every other standard_line row.

LAYER 2 BATCH (draft_pfmea_from_ai_suggestions): suggest_additions run
across a whole operation sequence at once, for a product_family with no
(or only partial) approved content -- the case generate_pfmea has nothing,
or not enough, to assemble. Exists so a whole-document AI draft has
somewhere to go besides the agent improvising a table in chat: paired with
exports.export_generated_pfmea.export_ai_draft, it produces an actual
.xlsx file, same as generate_pfmea does for validated content, with every
row still content_type=CONTENT_TYPE_AI_SUGGESTION and the file itself
carrying an unmissable "unvalidated AI draft" banner. Does not touch or
change generate_pfmea's own validated-content path in any way.

Read-only throughout with respect to standard_line (no writes, no
audit_log entries from this module, at any layer) and built on
db/standard_line_query.py's existing functions rather than re-querying
StandardLine directly, per that module's own read-side role. Layer 2 does
make one real OpenAI Agents SDK call per suggest_additions invocation --
that's the only place in this file that talks to a network at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agents import Agent, Runner
from agents.usage import Usage
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.standard_line_query import count_by_operation, list_product_families, search_standard_lines
from rules.operations import OPERATIONS

__all__ = [
    "CONTENT_TYPE_AI_SUGGESTION",
    "CONTENT_TYPE_VALIDATED",
    "AIDraftResult",
    "AIDraftSummary",
    "GenerationResult",
    "GenerationSummary",
    "OperationAvailability",
    "SuggestedLineItem",
    "draft_pfmea_from_ai_suggestions",
    "generate_pfmea",
    "list_operations",
    "list_product_families",
    "suggest_additions",
]

APPROVED_STATUS = "approved"
CONTENT_TYPE_VALIDATED = "VALIDATED"
# Layer 2's marker -- declared here (Layer 1) so the two content types are
# an agreed contract shared by both layers, not something Layer 2 invents
# unilaterally after the fact.
CONTENT_TYPE_AI_SUGGESTION = "AI_SUGGESTION"

GENERATED_DOCUMENT_STATUS = "Draft"  # spec §7: approval is human-only; a freshly assembled PFMEA is never pre-approved

# rules.operations.OPERATIONS is already the canonical OP10..OP200
# sequence; reuse it for ordering instead of re-deriving one.
_OPERATION_SEQUENCE = {op.code: i for i, op in enumerate(OPERATIONS)}


def _operation_sort_key(operation: str | None) -> tuple[int, str]:
    """Canonical OP10..OP200 order first; anything not in that list (should
    not happen for data that passed through rules.operations, but this
    stays honest rather than crashing) sorts after, alphabetically."""
    if operation in _OPERATION_SEQUENCE:
        return (_OPERATION_SEQUENCE[operation], "")
    return (len(_OPERATION_SEQUENCE), operation or "")


@dataclass(frozen=True)
class OperationAvailability:
    operation: str
    approved_count: int


@dataclass(frozen=True)
class GenerationSummary:
    product_family: str
    operations_requested: list[str]
    operations_included: list[str]
    operations_with_no_content: list[str]
    total_line_items: int


@dataclass(frozen=True)
class GenerationResult:
    rows: list[dict]
    summary: GenerationSummary
    status: str = GENERATED_DOCUMENT_STATUS


def list_operations(session: Session, product_family: str) -> list[OperationAvailability]:
    """Operations within `product_family` that have at least one APPROVED
    standard_line row, each with how many. Built on count_by_operation
    (db/standard_line_query.py) rather than re-querying StandardLine --
    that function already does exactly this GROUP BY, just re-sorted here
    by canonical operation sequence (OP10, OP20, ...) instead of by count,
    since a "which operations can I pick from" listing reads better in
    process order than ranked by size."""
    counts = count_by_operation(session, status=APPROVED_STATUS, product_family=product_family)
    operations = sorted(counts.keys(), key=_operation_sort_key)
    return [OperationAvailability(operation=op, approved_count=counts[op]) for op in operations]


def generate_pfmea(session: Session, product_family: str, selected_operations: list[str]) -> GenerationResult:
    """Assemble the APPROVED standard_line content for each of
    `selected_operations` (within `product_family`) into one ordered PFMEA
    row set. ASSEMBLES existing proven content only -- never fabricates a
    row for an operation with none. Only status='approved' rows are ever
    included; draft/reviewed/archived rows (including anything still
    FLAGGED_UNCERTAIN_PENDING_REVIEW -- see the standard_line status
    remediation this repo already did) are excluded categorically, not
    just by default.

    Every returned row carries content_type=CONTENT_TYPE_VALIDATED plus
    its standard_line provenance (id/line_key/version/approved_by/
    approved_at), so a later AI-suggestion layer can add rows tagged
    CONTENT_TYPE_AI_SUGGESTION into the same set without the two ever
    being confused for one another.

    Rows are ordered by canonical operation sequence, then by each row's
    own op_sequence_index when the source data has one, then by id for a
    fully deterministic tie-break.
    """
    rows: list[dict] = []
    operations_included: list[str] = []
    operations_with_no_content: list[str] = []

    for operation in selected_operations:
        approved_rows = search_standard_lines(
            session, "", status=APPROVED_STATUS, product_family=product_family, operation=operation, limit=1_000_000,
        )
        if not approved_rows:
            operations_with_no_content.append(operation)
            continue
        operations_included.append(operation)
        for r in approved_rows:
            rows.append({**r, "content_type": CONTENT_TYPE_VALIDATED})

    rows.sort(key=lambda r: (
        _operation_sort_key(r["operation"]),
        (r["line_payload"] or {}).get("op_sequence_index") or 0,
        r["id"],
    ))

    summary = GenerationSummary(
        product_family=product_family,
        operations_requested=list(selected_operations),
        operations_included=operations_included,
        operations_with_no_content=operations_with_no_content,
        total_line_items=len(rows),
    )
    return GenerationResult(rows=rows, summary=summary)


# =========================== LAYER 2: AI suggestions =========================

DEFAULT_SUGGESTION_MODEL = "gpt-5"
MAX_SUGGESTIONS_PER_CALL = 5

_OPERATION_BY_CODE = {op.code: op for op in OPERATIONS}


class SuggestedLineItem(BaseModel):
    failure_mode: str = Field(description="a plausible failure mode for this operation, NOT already covered")
    failure_cause: str = Field(description="a plausible root cause of that failure mode")
    failure_effect: str = Field(description="the downstream effect of that failure, on the customer or next station")
    prevention_control: str | None = Field(
        default=None, description="a plausible prevention control, if an obvious one exists"
    )
    detection_control: str | None = Field(
        default=None, description="a plausible detection control, if an obvious one exists"
    )
    rationale: str = Field(description="1-2 sentences: why this risk matters / why it's plausible for this operation")


class _SuggestionBatch(BaseModel):
    suggestions: list[SuggestedLineItem]


_SUGGESTION_INSTRUCTIONS = """\
You are a PFMEA (Process FMEA, AIAG-VDA) process engineer reviewing one \
manufacturing operation for GAPS in its documented risk coverage.

You will be given the operation and a list of failure modes ALREADY \
documented and approved for it. Your job is to propose ADDITIONAL, \
genuinely distinct failure modes an experienced process engineer would \
flag as plausible risks for this specific operation -- ones that are \
missing from the already-covered list.

RULES:
1. Never repeat or lightly reword an already-covered failure mode. If you \
cannot think of anything genuinely new, return fewer suggestions (even \
zero) rather than padding with near-duplicates.
2. Every suggestion must be specific to the given operation and plausible \
for real manufacturing (winding, tinning, gluing, testing, packaging, \
etc. -- whatever the operation actually is), not generic boilerplate that \
could apply to any process step.
3. Every suggestion needs a short rationale explaining why an engineer \
should care about it -- what goes wrong for the customer or the next \
station if this failure occurs.
4. Do not invent Severity/Occurrence/Detection ratings or an AP value --
those come from a deterministic lookup table this system already owns \
(rules.ap_lookup), not from you. Only propose failure_mode, failure_cause, \
failure_effect, and (if genuinely obvious) prevention_control/ \
detection_control.
5. These are SUGGESTIONS for a human to review, not decisions -- it is \
fine, and expected, to be conservative rather than exhaustive.
"""


def _build_suggestion_agent(*, model: str = DEFAULT_SUGGESTION_MODEL) -> Agent:
    return Agent(
        name="PFMEA_SUGGESTION",
        instructions=_SUGGESTION_INSTRUCTIONS,
        model=model,
        output_type=_SuggestionBatch,
    )


def _operation_description(operation: str) -> str:
    op = _OPERATION_BY_CODE.get(operation)
    if op is None:
        return operation
    return f"{operation} ({op.name} -- {op.detail})"


def _existing_coverage_block(existing_validated_rows: list[dict]) -> str:
    lines = []
    for row in existing_validated_rows:
        payload = row.get("line_payload") or {}
        failure_mode = payload.get("failure_mode")
        if not failure_mode:
            continue
        failure_cause = payload.get("failure_cause")
        line = f"- {failure_mode}"
        if failure_cause:
            line += f" (cause: {failure_cause})"
        lines.append(line)
    if not lines:
        return "(none -- no approved content exists for this operation yet)"
    return "\n".join(lines)


async def suggest_additions(
    product_family: str,
    operation: str,
    existing_validated_rows: list[dict],
    *,
    model: str = DEFAULT_SUGGESTION_MODEL,
    on_usage: Callable[[Usage], None] | None = None,
) -> list[dict]:
    """LAYER 2. Proposes up to MAX_SUGGESTIONS_PER_CALL new PFMEA line-items
    for `operation` that are NOT already represented in
    `existing_validated_rows` -- content from the model's general PFMEA
    engineering knowledge, not from any plant source file or the
    standard_line table.

    `existing_validated_rows` is supplied by the caller (the same row
    shape generate_pfmea/search_standard_lines return) rather than queried
    here, so this function itself needs no database session -- the
    database boundary stays entirely in Layer 1 and the MCP tool wrapper,
    keeping this call pure with respect to standard_line.

    Every returned dict is structurally distinct from a Layer 1 VALIDATED
    row -- it carries content_type=CONTENT_TYPE_AI_SUGGESTION and a
    `rationale`, and has NO id/line_key/version/status/approved_by/
    approved_at keys at all (there is nothing to have; it was never a
    standard_line row). This function never writes anything to
    standard_line under any circumstance -- suggestions are transient,
    presented for a human to review. The ONLY way one becomes real content
    is the existing human-only approval path (approve_to_db.py ->
    db.standard_line_writer.approve_standard_line), entered exactly like
    any other new row, never automatically from here.

    Args:
        product_family: e.g. "chokes" -- context for the prompt only, not
            used to query anything (the caller already scoped
            existing_validated_rows to this family).
        operation: the operation code, e.g. "OP60". Looked up against
            rules.operations for its name/detail to give the model real
            context instead of a bare code.
        existing_validated_rows: Layer 1 VALIDATED rows already assembled
            for this operation (e.g. from generate_pfmea or
            search_standard_lines) -- used only to tell the model what NOT
            to repeat.
        model: OpenAI model for the suggestion agent.
        on_usage: optional callback receiving the real measured Usage from
            this call, for cost tracking (same pattern as
            pfmea_agents.normalizer.run_normalizer_async).

    Returns: a list of dicts, each
        {content_type, product_family, operation, line_payload:
        {failure_mode, failure_cause, failure_effect, prevention_control,
        detection_control}, rationale}.
    """
    prompt = (
        f"Product family: {product_family}\n"
        f"Operation: {_operation_description(operation)}\n\n"
        f"Failure modes ALREADY covered by approved content (do NOT repeat these, even reworded):\n"
        f"{_existing_coverage_block(existing_validated_rows)}\n\n"
        f"Propose up to {MAX_SUGGESTIONS_PER_CALL} additional, genuinely distinct failure modes."
    )

    agent = _build_suggestion_agent(model=model)
    result = await Runner.run(agent, prompt)
    if on_usage is not None:
        on_usage(result.context_wrapper.usage)
    batch: _SuggestionBatch = result.final_output

    return [
        {
            "content_type": CONTENT_TYPE_AI_SUGGESTION,
            "product_family": product_family,
            "operation": operation,
            "line_payload": {
                "failure_mode": item.failure_mode,
                "failure_cause": item.failure_cause,
                "failure_effect": item.failure_effect,
                "prevention_control": item.prevention_control,
                "detection_control": item.detection_control,
            },
            "rationale": item.rationale,
        }
        for item in batch.suggestions
    ]


# =================== LAYER 2 BATCH: whole-document AI draft ===================
# generate_pfmea (Layer 1) only ever produces a file when there's approved
# content to assemble -- for a brand-new product_family with zero approved
# rows, list_operations returns nothing and there's nothing for generate_pfmea
# to write, so the deliverable used to fall back to whatever table the AGENT
# improvised from suggest_additions' JSON in chat -- the same
# build-your-own-workbook anti-pattern generate_pfmea's own file-writing was
# built to eliminate, just reached from the empty-content side instead of the
# has-content side. draft_pfmea_from_ai_suggestions closes that gap: it's
# suggest_additions run across a whole operation sequence instead of one
# operation at a time, so the caller (the MCP tool wrapper) can hand the
# combined result straight to exports.export_generated_pfmea.export_ai_draft
# in one call instead of stitching per-operation suggestions into a table by
# hand. Still Layer 2 throughout: every row stays content_type=
# CONTENT_TYPE_AI_SUGGESTION, still never invents S/O/D/AP, still never
# writes to standard_line. generate_pfmea's own validated-content path is
# completely untouched by this -- a separate function, a separate MCP tool.


@dataclass(frozen=True)
class AIDraftSummary:
    product_family: str
    operations_requested: list[str]
    operations_with_suggestions: list[str]
    operations_with_no_suggestions: list[str]
    total_line_items: int


@dataclass(frozen=True)
class AIDraftResult:
    rows: list[dict]
    summary: AIDraftSummary
    status: str = GENERATED_DOCUMENT_STATUS


async def draft_pfmea_from_ai_suggestions(
    product_family: str,
    selected_operations: list[str],
    existing_validated_rows_by_operation: dict[str, list[dict]] | None = None,
    *,
    model: str = DEFAULT_SUGGESTION_MODEL,
    on_usage: Callable[[Usage], None] | None = None,
) -> AIDraftResult:
    """Runs suggest_additions across every operation in `selected_operations`
    and assembles the combined result into one ordered set -- for a product
    with no (or only partial) approved standard_line content, where
    generate_pfmea has nothing, or not enough, to assemble on its own.

    `existing_validated_rows_by_operation` lets a caller that DOES have some
    approved content for a subset of operations pass it in (same row shape
    as generate_pfmea/search_standard_lines return) so suggest_additions is
    still told what NOT to repeat for those -- omit or leave an operation's
    entry empty when there's genuinely nothing approved yet, which is the
    expected case this function exists for. This function itself needs no
    database session, same as suggest_additions -- the database boundary
    stays in the MCP tool wrapper.

    Every returned row is structurally identical to a single suggest_additions
    call's output (content_type=CONTENT_TYPE_AI_SUGGESTION, a `rationale`,
    no id/line_key/version/status/approved_by/approved_at) -- never blended
    with VALIDATED rows, never written to standard_line under any
    circumstance. Rows are ordered by canonical operation sequence; within
    one operation, in whatever order the model returned them.

    Args:
        product_family: e.g. "brushes" -- a family with no approved content yet.
        selected_operations: operation codes to draft, in any order.
        existing_validated_rows_by_operation: optional {operation: rows}
            for operations that DO have some approved content already.
        model: OpenAI model for the suggestion agent.
        on_usage: optional callback receiving each call's measured Usage,
            for cost tracking -- called once per operation.
    """
    existing_validated_rows_by_operation = existing_validated_rows_by_operation or {}
    rows: list[dict] = []
    operations_with_suggestions: list[str] = []
    operations_with_no_suggestions: list[str] = []

    for operation in selected_operations:
        existing = existing_validated_rows_by_operation.get(operation, [])
        suggestions = await suggest_additions(
            product_family, operation, existing, model=model, on_usage=on_usage,
        )
        if suggestions:
            operations_with_suggestions.append(operation)
            rows.extend(suggestions)
        else:
            operations_with_no_suggestions.append(operation)

    rows.sort(key=lambda r: _operation_sort_key(r["operation"]))

    summary = AIDraftSummary(
        product_family=product_family,
        operations_requested=list(selected_operations),
        operations_with_suggestions=operations_with_suggestions,
        operations_with_no_suggestions=operations_with_no_suggestions,
        total_line_items=len(rows),
    )
    return AIDraftResult(rows=rows, summary=summary)
