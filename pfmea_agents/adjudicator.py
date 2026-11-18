"""
Layer 2 — ADJUDICATOR agent (spec §5.4). THE core agent: decides whether a
non-baseline candidate row is a DUPLICATE of any baseline (MASTER_REF) row
sharing its operation, or a genuine ENRICHMENT that should also be
included.

NO MECHANICAL PRE-FILTER (pipeline.py redesign): earlier versions of this
pipeline tried to narrow `matching_baseline_rows` down to "plausible near-
duplicates" before calling ADJUDICATOR — first an exact dedup-key string
match, then an embedding-similarity fallback, then operation+theme
category membership. All three were tested live and none reliably
separated real duplicates from real enrichments for this dataset (see
pipeline.py's module docstring for the numbers). So ADJUDICATOR is now
given the ENTIRE same-operation baseline set for every candidate and does
the actual comparison itself — it is the duplicate-detection mechanism,
not a tie-breaker for one already narrowed down by something else.

ASYMMETRY IS THE WHOLE POINT (spec §5.4 / "Asymmetry matters"): wrongly
dropping a genuine risk is a quality escape; wrongly keeping a duplicate
costs an engineer five minutes of review. Tune for ENRICHMENT recall
(target >= 0.95), not overall accuracy. This shows up here as: ties or
close calls should lean ENRICHMENT, and confidence should be honestly
lower whenever the candidate carries a risk signal (higher S, a different
special characteristic, a distinct failure cause) not clearly present in
the baseline match.

Requires OPENAI_API_KEY (spec §2: Layer 2 runs on the OpenAI Agents SDK).
"""
from __future__ import annotations

from typing import Callable, Literal

from agents import Agent, Runner
from agents.usage import Usage
from pydantic import BaseModel, Field

DEFAULT_MODEL = "gpt-5"


class CandidateRow(BaseModel):
    row_id: str
    source: str
    operation: str
    process_step: str
    failure_mode_en: str
    failure_effect_en: str
    failure_cause_en: str
    severity: int | None = None
    occurrence: int | None = None
    detection: int | None = None


class BaselineRow(BaseModel):
    row_id: str
    process_step: str
    failure_mode_en: str
    failure_effect_en: str
    failure_cause_en: str
    severity: int | None = None
    occurrence: int | None = None
    detection: int | None = None


class AdjudicatorInput(BaseModel):
    candidate_row: CandidateRow
    matching_baseline_rows: list[BaselineRow] = Field(min_length=1)


class AdjudicatorOutput(BaseModel):
    decision: Literal["DUPLICATE", "ENRICHMENT"]
    justification: str = Field(description="prose, in the style of column AV (Source_Selection_Justification)")
    absorbed_by: str | None = Field(
        default=None, description="baseline row_id that absorbed this row, required when decision=DUPLICATE"
    )
    confidence: float = Field(ge=0.0, le=1.0)


INSTRUCTIONS = """\
You are ADJUDICATOR, the core decision agent in a PFMEA consolidation
pipeline. You are called for every candidate row that has at least one
baseline (MASTER_REF) row in the SAME OPERATION — that's the only
pre-filtering that happens before you. You are shown the FULL set of
baseline rows at that operation, not a pre-selected shortlist of "likely"
matches. Some of what you're shown will be obviously unrelated to the
candidate; scanning past those to find (or rule out) a real match is your
job, not a sign the input was mis-filtered.

YOUR JOB: decide whether the candidate's failure is ALREADY COVERED by ANY
of the baseline rows you were shown — safe to drop, its risk is already
captured — or whether it's a genuine ENRICHMENT that adds real,
non-redundant risk information and should be kept as its own row. Check
the candidate against every baseline row given, not just the first one or
the most superficially similar one — the right match (if any) might not be
the one that shares the most words with the candidate.

THE ASYMMETRY YOU MUST OPTIMIZE FOR: wrongly calling a real risk a
DUPLICATE is a quality escape — a genuine failure mode silently vanishes
from the PFMEA. Wrongly calling a duplicate an ENRICHMENT costs a reviewing
engineer a few minutes, nothing more. These are not equally bad mistakes.
When you are genuinely unsure, prefer ENRICHMENT over DUPLICATE.

Signals that argue for ENRICHMENT (keep):
- No baseline row describes the same underlying failure — a shared
  operation alone means nothing; most of the baseline set will legitimately
  describe different failures at that same operation.
- The candidate's severity is meaningfully higher than the closest
  baseline's — it's describing a worse consequence, not the same one.
- The failure CAUSE is materially different (a distinct 4M — Man/Machine/
  Material/Method — root cause), even if the failure mode wording is
  similar to some baseline row. Two rows with the same wording but
  different causes (e.g. a program error vs. a machine misadjustment vs.
  worn tooling) legitimately coexist per AIAG-VDA practice — this is not
  the same as being duplicate.
- The candidate carries a special characteristic, a distinct detection
  method, or shop-floor evidence (e.g. from a Gemba observation) not
  reflected in any baseline row at all.

Signals that argue for DUPLICATE (drop, absorbed by the specific baseline
row that covers it):
- Same failure mode, same underlying cause family as ONE SPECIFIC baseline
  row, no materially different risk information — that baseline row
  already captures this candidate's content, just phrased differently or
  in a different language.
- The candidate is strictly less specific/informative than a specific
  baseline row (e.g. a rollup where that baseline already has the
  per-effect detail).

`justification`: write it in the style of a PFMEA reviewer's note — plain
prose stating what you compared and why (e.g. "Same failure mode and cause
family as baseline 00.0.12; no additional risk information — absorbed."
or "No baseline row in this operation describes this failure — genuine
enrichment."). This is read by humans reviewing the decision, not just
logged.

`absorbed_by`: the row_id of the ONE specific baseline row that absorbed
this candidate, REQUIRED when decision=DUPLICATE (name the actual matching
row, not just "one of them"), and must be null when decision=ENRICHMENT.

`confidence`: your calibrated probability that this decision is correct.
Because ENRICHMENT recall matters more than overall accuracy, do not let a
low confidence push you toward DUPLICATE by default — an uncertain call
should be ENRICHMENT with modest confidence, so a human reviews it, rather
than DUPLICATE with modest confidence, which discards it silently.
"""


def build_adjudicator_agent(*, model: str = DEFAULT_MODEL) -> Agent:
    return Agent(
        name="ADJUDICATOR",
        instructions=INSTRUCTIONS,
        model=model,
        output_type=AdjudicatorOutput,
    )


def _prompt_for_input(data: AdjudicatorInput) -> str:
    lines = ["CANDIDATE ROW:", data.candidate_row.model_dump_json(indent=2), ""]
    lines.append(f"MATCHING BASELINE ROW(S) ({len(data.matching_baseline_rows)}):")
    for b in data.matching_baseline_rows:
        lines.append(b.model_dump_json(indent=2))
    return "\n".join(lines)


def run_adjudicator(data: AdjudicatorInput, *, model: str = DEFAULT_MODEL) -> AdjudicatorOutput:
    agent = build_adjudicator_agent(model=model)
    result = Runner.run_sync(agent, _prompt_for_input(data))
    return result.final_output


async def run_adjudicator_async(
    data: AdjudicatorInput, *, model: str = DEFAULT_MODEL, on_usage: Callable[[Usage], None] | None = None,
) -> AdjudicatorOutput:
    """Awaitable counterpart to run_adjudicator — see
    normalizer.run_normalizer_async for why eval scripts need this instead
    of one sequential call at a time.

    `on_usage`, if given, is called with the real measured Usage from this
    call's context_wrapper — for cost tracking that doesn't rely on
    estimating from prompt text."""
    agent = build_adjudicator_agent(model=model)
    result = await Runner.run(agent, _prompt_for_input(data))
    if on_usage is not None:
        on_usage(result.context_wrapper.usage)
    return result.final_output
