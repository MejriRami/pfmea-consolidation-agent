"""
Layer 2 — THEMER agent (spec §5.3).

Assigns one theme from the CLOSED vocabulary (rules.THEME_VOCABULARY, 42
themes frozen in Phase 0) to a normalized row, based on operation, process
step, and English failure mode/cause. Never invents a theme — no
confident match means UNCLASSIFIED, routed to the human queue.

Requires OPENAI_API_KEY (spec §2: Layer 2 runs on the OpenAI Agents SDK).
"""
from __future__ import annotations

from typing import Literal

from agents import Agent, Runner
from pydantic import BaseModel, Field

from rules import THEME_VOCABULARY, UNCLASSIFIED

from .theme_catalog import THEME_CATALOG, UNCLASSIFIED_NOTE

DEFAULT_MODEL = "gpt-5"

_ALLOWED_THEMES = THEME_VOCABULARY | {UNCLASSIFIED}
# A Literal, not a validated str: this puts the closed vocabulary directly
# into the JSON schema sent to the model (OpenAI structured outputs enforce
# enums at the API level), so an out-of-vocabulary theme can't come back as
# a "successful" result to then reject — it's excluded at the source.
ThemeLiteral = Literal[tuple(sorted(_ALLOWED_THEMES))]


class ThemerInput(BaseModel):
    operation: str
    process_step: str
    failure_mode_en: str
    failure_cause_en: str


class ThemerOutput(BaseModel):
    theme: ThemeLiteral
    confidence: float = Field(ge=0.0, le=1.0)


def _catalog_block() -> str:
    lines = []
    for theme in sorted(THEME_CATALOG):
        entry = THEME_CATALOG[theme]
        examples = "; ".join(repr(e) for e in entry.examples) if entry.examples else "(no verified example)"
        lines.append(f"- {theme}: {entry.description}\n    examples: {examples}")
    return "\n".join(lines)


INSTRUCTIONS = f"""\
You are THEMER, a classification agent for PFMEA (Process FMEA) failure
rows. You assign exactly one theme from a CLOSED vocabulary, describing
the underlying failure category — never invent a theme outside this list.

INPUT: operation (e.g. "OP40"), process_step, failure_mode_en,
failure_cause_en — all already translated to English by an upstream
NORMALIZER step.

CLOSED VOCABULARY (theme: description, example failure modes):
{_catalog_block()}

RULES:

1. Classify primarily on failure_mode_en — what actually went wrong. Most
   themes are fully determined by it, and a difference in 4M cause category
   (Man/Machine/Material/Method) alone is normally NOT a reason to split
   otherwise-identical failure modes into different themes.

2. EXCEPTION — read failure_cause_en whenever a theme's own description
   above says it is distinguished by cause (several glue/core-force themes
   do: glue_missing vs. glue_process vs. glue_pressure vs.
   core_ferrite_process can all describe the exact same failure-mode
   wording, e.g. "Ferrite core pull out force under specifications" —
   ONLY the cause tells them apart). When a theme's description names a
   specific cause signal to look for, that signal wins over a same-wording
   match to a different theme's example.

3. {UNCLASSIFIED_NOTE}

4. `confidence`: your calibrated probability that the assigned theme is
   correct. A forced, low-confidence match into the nearest-sounding theme
   is worse than an honest UNCLASSIFIED — the latter gets human review,
   the former may not.

Output ONLY a theme from the vocabulary above, or the literal string
"{UNCLASSIFIED}". Never output any other value.
"""


def build_themer_agent(*, model: str = DEFAULT_MODEL) -> Agent:
    return Agent(
        name="THEMER",
        instructions=INSTRUCTIONS,
        model=model,
        output_type=ThemerOutput,
    )


def _prompt_for_input(row: ThemerInput) -> str:
    return (
        f"operation: {row.operation}\n"
        f"process_step: {row.process_step}\n"
        f"failure_mode_en: {row.failure_mode_en}\n"
        f"failure_cause_en: {row.failure_cause_en}"
    )


def run_themer(row: ThemerInput, *, model: str = DEFAULT_MODEL) -> ThemerOutput:
    agent = build_themer_agent(model=model)
    result = Runner.run_sync(agent, _prompt_for_input(row))
    return result.final_output


async def run_themer_async(row: ThemerInput, *, model: str = DEFAULT_MODEL) -> ThemerOutput:
    """Awaitable counterpart to run_themer — see normalizer.run_normalizer_async
    for why eval scripts need this instead of one sequential call at a time."""
    agent = build_themer_agent(model=model)
    result = await Runner.run(agent, _prompt_for_input(row))
    return result.final_output
