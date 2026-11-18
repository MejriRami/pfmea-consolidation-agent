"""
Layer 2 — MAPPER agent (spec §5.1).

Detects the column layout of a previously-unseen PFMEA sheet: which column
holds severity, which holds failure mode, whether the sheet rates AP or
RPN, what languages appear. Output is schema-constrained (Pydantic) and
confidence-scored. A low-confidence or absent mandatory-field mapping must
never be guessed — it's omitted and routed to the human queue (Layer 3).

Requires OPENAI_API_KEY (spec §2: Layer 2 runs on the OpenAI Agents SDK).
This module builds and calls the agent; it does not manage the key itself.
"""
from __future__ import annotations

from typing import Literal

from agents import Agent, AgentOutputSchema, Runner
from pydantic import BaseModel, Field

from .grid import SheetGrid, render_sheet_grid

DEFAULT_MODEL = "gpt-5"

# The canonical field vocabulary MAPPER assigns column letters to. This
# mirrors normalized_line (rules/schema.py) minus `operation`, `theme`, and
# `dedup_key` — those are derived downstream (an OP-number lookup against
# 03_PROCESS_DIAGRAM_MAP, the THEMER agent, and rules.build_dedup_key,
# respectively), never read directly off a source sheet.
MANDATORY_FIELDS = (
    "process_step",
    "failure_mode",
    "failure_effect",
    "failure_cause",
    "severity",
    "occurrence",
    "detection",
)
OPTIONAL_FIELDS = (
    "process_item",
    "work_element",
    "function_item",
    "function_step",
    "function_element",
    "prevention_control",
    "detection_control",
    "ap",
    "special_char",
    "four_m_type",
)
ALL_FIELDS = MANDATORY_FIELDS + OPTIONAL_FIELDS


class MapperOutput(BaseModel):
    header_row: int = Field(description="1-indexed row number containing the column headers")
    columns: dict[str, str] = Field(
        description=f"field name -> column letter. Keys must be drawn from {ALL_FIELDS}"
    )
    rating_method: Literal["AP", "RPN", "MIXED", "UNKNOWN"]
    languages: list[str] = Field(
        description="ISO 639-1 codes observed in headers or sample data, e.g. ['en'] or ['zh', 'en']"
    )
    unmapped_columns: list[str] = Field(
        default_factory=list,
        description="column letters with header text that fit no field above",
    )
    confidence: float = Field(ge=0.0, le=1.0)


INSTRUCTIONS = f"""\
You are MAPPER, a column-detection agent for PFMEA (Process FMEA) spreadsheets.

INPUT: a plain-text rendering of the top-left corner of one worksheet, one
row per line, formatted as `A1='...' | C1='...' | ...`. Blank cells are
omitted, so a line may start at any column letter — the cell reference
carries the position, not the line's left edge.

YOUR JOB — ONLY THIS:

1. Find the header row: the row whose cells are column titles ("Failure
   Mode (FM) of the Process Step", "Severity (S) of FE", ...) rather than
   data. Header rows are NOT always near the top of the window — a
   cover-sheet or rating-scale legend block can push the real header down
   20+ rows. Data rows sit below the header and repeat a consistent shape;
   use that to confirm you've found the right row, not the legend.

2. Map header columns onto this CLOSED field vocabulary. Use these keys
   only — never invent one:
     mandatory: {', '.join(MANDATORY_FIELDS)}
     optional:  {', '.join(OPTIONAL_FIELDS)}
   Any column whose header text doesn't fit one of these goes in
   `unmapped_columns` instead — e.g. an internal sequence number, an
   action-tracking column (owner/due-date/status), a duplicate "revised"
   S/O/D column, or a rollup column (see rule 3).

3. Two-severity trap: some sheets carry both a per-effect severity column
   ("Severity (S) of FE") and a rollup across all effects of a failure mode
   ("Severity (S) of All FE"). Map `severity` to the PER-EFFECT column only
   — it is scoped to one failure-effect row, like occurrence and detection
   are. Put the rollup column in `unmapped_columns`. The same pattern can
   appear for other fields; when two columns compete for one field, prefer
   whichever is scoped to the single row you're mapping, not an aggregate.

4. rating_method: "AP" if an Action Priority column is present (often
   literally labeled AP, e.g. "PFMEA AP"); "RPN" if a Risk Priority Number
   column is present (RPN, or S x O x D) and no AP column exists; "MIXED" if
   both appear; "UNKNOWN" if neither does.

5. languages: list every language you can identify, as ISO 639-1 codes, in
   header text or sample data cells. A single cell can hold more than one
   language at once (e.g. Chinese characters immediately followed by an
   English translation) — that counts as both languages present.

6. confidence: your calibrated probability that `header_row` and every
   MANDATORY field mapping are correct. If you can't confidently place a
   mandatory field, do not guess a column for it — omit that key from
   `columns` entirely and lower `confidence`. A plausible-looking wrong
   guess is worse than an honest gap, because a human only reviews what
   looks uncertain.

Never invent a field name outside the closed vocabulary above. Never
reference a column letter that has no header text anywhere in the input.
"""


def build_mapper_agent(*, model: str = DEFAULT_MODEL) -> Agent:
    return Agent(
        name="MAPPER",
        instructions=INSTRUCTIONS,
        model=model,
        # `columns` is a free-form field-name -> column-letter dict, which
        # OpenAI's strict structured-output mode rejects outright
        # ("additionalProperties should not be set for object types") since
        # strict mode requires a fixed schema, not an open-ended map.
        # Relaxed to non-strict here rather than restructuring `columns`
        # into a fixed list-of-pairs shape, since every caller (eval_mapper,
        # known_layouts comparisons, offline tests) already treats it as a
        # plain dict.
        output_type=AgentOutputSchema(MapperOutput, strict_json_schema=False),
    )


def _prompt_for_grid(grid: SheetGrid) -> str:
    return (
        f"File: {grid.filename}\n"
        f"Sheet: {grid.sheet_name}\n"
        f"Rows 1-{grid.max_row_scanned} (blank cells omitted):\n\n"
        f"{grid.text}"
    )


def run_mapper_on_grid(grid: SheetGrid, *, model: str = DEFAULT_MODEL) -> MapperOutput:
    agent = build_mapper_agent(model=model)
    result = Runner.run_sync(agent, _prompt_for_grid(grid))
    return result.final_output


def run_mapper_on_file(
    path: str,
    sheet_name: str,
    *,
    model: str = DEFAULT_MODEL,
    max_rows: int | None = None,
) -> MapperOutput:
    kwargs = {} if max_rows is None else {"max_rows": max_rows}
    grid = render_sheet_grid(path, sheet_name, **kwargs)
    return run_mapper_on_grid(grid, model=model)
