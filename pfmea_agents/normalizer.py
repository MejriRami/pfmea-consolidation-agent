"""
Layer 2 — NORMALIZER agent (spec §5.2).

Translates a raw source cell (any language, possibly two languages in one
cell) to English while NEVER discarding the original wording — traceability
requires every downstream row to be able to point back to exactly what the
source said. Also corrects known typo classes (e.g. "Inetrne"/"Inetrn" for
French "Interne") as part of producing clean English, and prefers a
canonical term from rules.terminology when one applies.

Requires OPENAI_API_KEY (spec §2: Layer 2 runs on the OpenAI Agents SDK).
"""
from __future__ import annotations

from typing import Callable

from agents import Agent, Runner
from agents.usage import Usage
from pydantic import BaseModel, Field

from rules.terminology import TERMINOLOGY

DEFAULT_MODEL = "gpt-5"


class NormalizerOutput(BaseModel):
    original_wording: str = Field(description="the input text, byte-for-byte, unmodified")
    english: str = Field(description="clean English translation, typos corrected")
    canonical_term: str | None = Field(
        default=None,
        description="a term from the controlled glossary if the text matches one, else null",
    )
    confidence: float = Field(ge=0.0, le=1.0)


_GLOSSARY_LINES = "\n".join(f'  "{term}" -> "{canonical}"' for term, canonical in TERMINOLOGY.items())

INSTRUCTIONS = f"""\
You are NORMALIZER, a translation/cleanup agent for PFMEA (Process FMEA)
spreadsheet cell text. Source cells may be in French, Spanish, Chinese, or
English, and a single cell can mix two languages (e.g. Chinese characters
immediately followed by an English translation of the same phrase).

YOUR JOB:

1. `original_wording`: copy the input text EXACTLY as given. Never correct,
   translate, or trim it here — this field is the traceability record. If
   you are unsure whether a change belongs in `original_wording` or
   `english`, it belongs in `english`.

2. `english`: produce a clean, natural English rendering of the same
   content.
   - If the cell is bilingual (e.g. Chinese followed by its own English
     translation), use the intended meaning — you do not need to "invent" a
     translation when one is already embedded, just clean it up.
   - Correct typos you can identify with high confidence, especially this
     known class: "Inetrne" / "Inetrn" is a misspelling of the French word
     "Interne" (English: "Internal") and appears throughout this dataset's
     "Function of the Process Item" field. Fix it in `english`; it must
     still appear as originally misspelled in `original_wording`.
   - Do not add information that isn't in the source. Do not drop
     information either — a bilingual cell's English rendering should
     capture everything the source conveys, not just one half.

3. `canonical_term`: check whether the text matches an entry in this
   controlled glossary (case-insensitive, substring match is fine):
{_GLOSSARY_LINES}
   If it matches, return the canonical English term from the right-hand
   side above — do not paraphrase it. If nothing in the glossary applies,
   return null. Do not invent a canonical term that isn't in this list.

4. `confidence`: your calibrated probability that `english` correctly and
   completely captures the source meaning. Lower it for text you're
   uncertain how to translate (e.g. ambiguous technical jargon, garbled
   OCR-like text) rather than guessing fluently.
"""


def build_normalizer_agent(*, model: str = DEFAULT_MODEL) -> Agent:
    return Agent(
        name="NORMALIZER",
        instructions=INSTRUCTIONS,
        model=model,
        output_type=NormalizerOutput,
    )


def run_normalizer(text: str, *, model: str = DEFAULT_MODEL) -> NormalizerOutput:
    agent = build_normalizer_agent(model=model)
    result = Runner.run_sync(agent, text)
    return result.final_output


async def run_normalizer_async(
    text: str, *, model: str = DEFAULT_MODEL, on_usage: Callable[[Usage], None] | None = None,
) -> NormalizerOutput:
    """Same as run_normalizer, but awaitable — for eval scripts that need to
    fan out many rows concurrently instead of one sequential call at a time
    (gpt-5 reasoning latency makes a fully sequential 700+ row replay
    impractically slow).

    `on_usage`, if given, is called with the real measured Usage
    (input/output/reasoning tokens) from this call's context_wrapper — for
    cost tracking that doesn't rely on estimating from prompt text."""
    agent = build_normalizer_agent(model=model)
    result = await Runner.run(agent, text)
    if on_usage is not None:
        on_usage(result.context_wrapper.usage)
    return result.final_output


def is_plain_ascii(text: str) -> bool:
    """True if `text` has no non-ASCII characters at all — a cheap,
    deterministic signal that a live NORMALIZER call would be a no-op
    translation (still useful for typo-correction, but callers that want to
    skip the AI call for pure-English text can use this to decide)."""
    return text.isascii()
