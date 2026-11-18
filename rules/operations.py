"""
Layer 1 — operation resolution (spec §3.3: "op — operation ID, from
03_PROCESS_DIAGRAM_MAP (OP10...OP200)"). Deterministic keyword matching
against the canonical 20-operation process flow, extracted verbatim from
the master workbook's 03_PROCESS_DIAGRAM_MAP sheet. No AI: the spec frames
this explicitly as a lookup, and a wrong operation silently corrupts the
dedup key (rules.build_dedup_key) for every downstream decision.

WHY THIS EXISTS AS A SEPARATE MODULE: 02_FINAL_PFMEA's process_step text
already carries an "OP40 — ..." prefix (trivial to parse), but
01_ALL_SOURCES_GROUPED's process_step column — what ADJUDICATOR actually
has to work with pre-consolidation — does NOT (e.g. "RM incoming
inspection", "10.3: tinning", "Forming (Winding, stripping & tinning)").
Every non-baseline source row needs its operation resolved from free text
before a dedup key can be built at all.

SCORING: name tokens count double, detail tokens count once. The name is
the operation's actual identity ("Tinning / solder bath"); the detail is a
loose list of things to watch for at that step, and can incidentally share
a word with another operation's name (e.g. OP180's detail happens to
mention "Tinning" among five things it visually checks). Weighting by
provenance stops that incidental mention from tying with OP50, whose NAME
is literally "Tinning" — measured against the actual 719-row ground truth,
unweighted counting left ~44% of real process_step values unresolved
(mostly false ties like this one), which came down substantially once
name-matches were weighted and the concrete vocabulary gaps below were
added — both changes are traceable to specific process_step values found
in ground_truth/raw_lines.jsonl, not speculative tuning.

OPERATION_OVERRIDES covers the remaining handful of short, genuinely
ambiguous-by-keyword-alone labels (after stripping a leading step-number
like "10.2:"), resolved by cross-referencing which operation the master's
OWN clean (alignment-verified) rows assign to the corresponding theme —
see each override's comment for the specific evidence.

DELIBERATELY CONSERVATIVE otherwise: resolve_operation returns None on
remaining ties or on no signal at all rather than guessing. A source row
like SITE_B's "Forming (Winding, stripping & tinning)" genuinely bundles
three operations (OP40 stripping, OP50 tinning, OP60 winding) that the
master keeps separate — there is no honest single answer, and picking one
silently would corrupt that row's dedup key. None means "route to
human/AI disambiguation", not "give up silently."
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"of", "the", "and", "or", "a", "an", "to", "in", "on", "for", "with"}
_STEP_NUMBER_PREFIX = re.compile(r"^\s*\d+(?:\.\d+)?\s*[:.]?\s*")
_NAME_WEIGHT = 2
_DETAIL_WEIGHT = 1


@dataclass(frozen=True)
class Operation:
    code: str
    name: str
    detail: str


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOPWORDS}


def _normalize(process_step: str) -> str:
    return _STEP_NUMBER_PREFIX.sub("", process_step).strip().lower()


# Extracted verbatim from 03_PROCESS_DIAGRAM_MAP (rows 5, 7, 9) in
# PFMEA_Master_CustomerA_AIAG_VDA_2026_Pilot1_Dashboard_PFC_Updated.xlsx, with a
# small number of synonyms added for vocabulary gaps found in the real
# ground truth (each traceable to a specific raw process_step value):
#   OP30  += "straightening"/"straighten"/"feed" — final_lines.jsonl theme
#           wire_straightening_feed ("Bad Straightening of Copper Wire",
#           "Unstable wire feed / guide misalignment") is assigned OP30 in
#           the alignment-verified ground truth.
#   OP50  += "fluxing" — raw process_step "10.2 : fluxing" (9 rows).
#   OP70  += "cutting"/"cut"/"burr" — theme cutting_burr_length ("Cutted
#           leg with burr") is assigned OP70; raw process_step "10.4 :
#           cutting" (7 rows) fits the same post-tinning leg-cutting step.
#   OP110 += "gluing" — raw process_step "Manual gluing" (21 rows).
#   OP140 += "drying" — raw process_step "Drying" (13 rows); curing/heating
#           and drying are the same physical step (cure oven dwell time).
#   OP180 += "csl" — 03_PROCESS_DIAGRAM_MAP's own quality-gate section
#           names this station "VIS-STD / CSL" (QG10, "Final control").
OPERATIONS: list[Operation] = [
    Operation("OP10", "Incoming material & lot ID", "RM Reception / inspection"),
    Operation("OP20", "Weighing / quantity prep", "Packaging quantity baseline"),
    Operation("OP30", "Winding setup", "Program / tool / first-off / straightening / straighten / feed"),
    Operation("OP40", "Denoising / stripping", "Varnish removal / wire exposure"),
    Operation("OP50", "Tinning / solder bath", "Flux / fluxing / bath temp / tinning length"),
    Operation("OP60", "Coil winding", "Turns / length / wire tension"),
    Operation("OP70", "Lead forming / locking", "Bending / leg length / angle / cutting / cut / burr"),
    Operation("OP80", "Start-up OK + SPC", "Critical dimensions / number of turns"),
    Operation("OP90", "Transfer / WIP control", "Handling / segregation / traceability"),
    Operation("OP100", "Ferrite / core feeding", "Presence / orientation / damage"),
    Operation("OP110", "Glue dispensing", "Dose / nozzle / pot-life / purge / gluing"),
    Operation("OP120", "Ferrite positioning", "Assembly alignment / coverage"),
    Operation("OP130", "Auto locking / assembly", "Nucleus / coil / ferrite / locking"),
    Operation("OP140", "Heating / curing", "Temperature / speed / fan / dwell time / drying"),
    Operation("OP150", "Pull-off / push test", "Retention / force / method"),
    Operation("OP160", "LCR electrical test", "Inductance / resistance / fixture"),
    Operation("OP170", "Dimensional / angle check", "Calibre / projector / OK-NOK master"),
    Operation("OP180", "Final visual final visual standard", "Tinning / ferrite / glue / legs / aspect"),
    Operation("OP190", "Packaging / label", "Quantity / weight / barcode / FIFO"),
    Operation("OP200", "Containment & reaction", "Stop / isolate / QRQC / escalation"),
]

# Short labels that stay tied even after name/detail weighting, because both
# candidates are equally "the name" of a different operation. Resolved
# against what the alignment-verified ground truth actually assigns:
OPERATION_OVERRIDES: dict[str, str] = {
    # "winding" alone ties OP30 ("Winding setup") against OP60 ("Coil
    # winding"). Raw process_step values are numbered station labels
    # ("10.2 :Winding", "10.3: tinning", "10.4 : cutting") describing the
    # operation being PERFORMED at that station, not its setup phase, and
    # sit in the same numbered sequence as tinning (OP50) and cutting
    # (OP70) — both actual production steps. OP60 is the matching read.
    "winding": "OP60",
    # "final control" ties OP180 (matches "final") against OP90 ("WIP
    # control", matches "control"). 03_PROCESS_DIAGRAM_MAP's quality-gate
    # section literally names this gate "VIS-STD / CSL \n Final control"
    # (QG10) — it's OP180's own gate description, not OP90's.
    "final control": "OP180",
    # Bare "packaging" ties OP20 ("Packaging quantity baseline", a detail
    # mention) against OP190 ("Packaging / label", the actual name). The
    # override isn't strictly necessary post-weighting (OP190 wins on name
    # weight alone) but is kept explicit since both are plausible in
    # isolation.
    "packaging": "OP190",
}

_OP_PREFIX = re.compile(r"^\s*(OP\d+)\b")
_NAME_TOKENS = {op.code: _tokens(op.name) for op in OPERATIONS}
# Detail tokens already covered by the name are excluded here so a word
# appearing in BOTH (e.g. OP50's name "Tinning..." and its own detail
# "...tinning length") isn't credited twice toward the same operation's
# score — that double-count previously let OP50 win outright on a query
# spanning several operations ("Forming (Winding, stripping & tinning)"),
# masking a genuine ambiguity that should have resolved to None.
_DETAIL_TOKENS = {op.code: (_tokens(op.detail) - _NAME_TOKENS[op.code]) for op in OPERATIONS}
_ALL_CODES = {op.code for op in OPERATIONS}

assert set(OPERATION_OVERRIDES.values()) <= _ALL_CODES


@dataclass(frozen=True)
class OperationMatch:
    code: str | None
    score: int
    ambiguous_with: tuple[str, ...] = ()  # other operation codes tied for the top score


def resolve_operation(process_step: str) -> OperationMatch:
    """Best-effort deterministic resolution of a free-text process_step to
    one of the 20 canonical operations. Returns code=None when the text
    carries no signal (score 0) or ties between two-or-more operations
    (ambiguous_with lists them) — never picks arbitrarily."""
    if not process_step:
        return OperationMatch(code=None, score=0)

    prefix_match = _OP_PREFIX.match(process_step)
    if prefix_match:
        code = prefix_match.group(1)
        if code in _ALL_CODES:
            return OperationMatch(code=code, score=_NAME_WEIGHT * len(_NAME_TOKENS[code]))

    normalized = _normalize(process_step)
    if normalized in OPERATION_OVERRIDES:
        code = OPERATION_OVERRIDES[normalized]
        return OperationMatch(code=code, score=_NAME_WEIGHT)

    query = _tokens(process_step)
    if not query:
        return OperationMatch(code=None, score=0)

    scores = {
        op.code: (
            _NAME_WEIGHT * len(query & _NAME_TOKENS[op.code])
            + _DETAIL_WEIGHT * len(query & _DETAIL_TOKENS[op.code])
        )
        for op in OPERATIONS
    }
    best_score = max(scores.values())
    if best_score == 0:
        return OperationMatch(code=None, score=0)

    top = [code for code, score in scores.items() if score == best_score]
    if len(top) > 1:
        return OperationMatch(code=None, score=best_score, ambiguous_with=tuple(top))

    return OperationMatch(code=top[0], score=best_score)
