"""
A ground-truth defect independent of, and NOT caught by, Phase 0's own
validation (PHASE_0_RESULTS.md reports "0 mismatches" and "0 disagreements"
— those checks compare AG/AQ/AL against each other and stored-AP against
looked-up-AP, never column AW's failure-mode segment against that same
row's own failure_mode column I).

DISCOVERED while building the Phase 3 THEMER eval. For roughly 50 of 192
final rows — concentrated in the OP40/OP50 stripping/winding/tinning region
(excel rows ~56-111) — column AW's dedup key (and therefore `theme`)
describes a DIFFERENT row's failure content, not the row it's attached to.
Confirmed directly against the raw workbook (not just this repo's own
extraction code), e.g.:

    row 60: I60  = "Stripping length out of specification"
            AW60 = "OP40|winding_geometry|wrong coil diameter"
            ("wrong coil diameter" is actually rows 100/101's own content)

    row 63: I63  = "Missing stripping"
            AW63 = "OP40|stripping_nok|线圈变形 coil deformation"
            ("线圈变形 Coil deformation" is actually row 103's own content)

This is NOT the same defect as the documented column-A id drift (55 rows,
already isolated and resolved by treating AL as authoritative) — AG/AQ/AL
agree with each other for these rows. It looks like the AG:AW audit-column
block was computed in a differently-sorted intermediate table and pasted
back into 02_FINAL_PFMEA without re-sorting to match the I:P content block,
for this one region. The row-to-row offset isn't constant (mixed -16/+40/
+1/-1/... deltas), so it isn't safely auto-correctable by a blind shift.

CONSEQUENCE FOR THEMER'S EVAL: a THEMER call is given {operation,
process_step, failure_mode_en, failure_cause_en} — this row's own content.
On a desynced row, comparing its output theme against the row's stored
`theme` measures agreement with the WRONG row's answer, not this row's.
A THEMER that is working correctly will appear to "fail" on exactly these
rows. Scoring them would conflate a ground-truth defect with a THEMER
quality problem. eval_themer.py excludes them from the pass/fail bar and
reports them separately instead.

This needs a Phase 0 revisit (human-verified realignment or re-extraction
of the AG:AW block from the master), not a Phase 3 fix.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

FINAL_LINES_PATH = Path(__file__).resolve().parent.parent / "ground_truth" / "final_lines.jsonl"

DEFAULT_MISALIGNMENT_THRESHOLD = 0.15  # Jaccard token overlap below this = content desync
_WORD = re.compile(r"[a-z0-9]+")


def _parse_dedup_key_original(raw: str | None) -> str | None:
    """Extract just the failure-mode segment of the raw AW value (mirrors
    extract_ground_truth.py::parse_dedup_key, third-segment half only)."""
    if not raw or "|" not in str(raw):
        return None
    parts = str(raw).split("|")
    if len(parts) < 3:
        return None
    return "|".join(parts[2:]).strip().lower()


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class AlignmentReport:
    aligned: list[dict]      # rows whose AW failure-mode segment matches their own failure_mode
    desynced: list[dict]     # rows where it doesn't (excel_row, jaccard, theme, key_fm, failure_mode)
    no_dedup_key: list[dict] # rows with no parseable dedup_key_original at all (e.g. row 45)


def check_alignment(
    rows: list[dict] | None = None,
    *,
    threshold: float = DEFAULT_MISALIGNMENT_THRESHOLD,
) -> AlignmentReport:
    if rows is None:
        with FINAL_LINES_PATH.open(encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]

    aligned, desynced, no_key = [], [], []
    for r in rows:
        key_fm = _parse_dedup_key_original(r.get("dedup_key_original"))
        fm = r.get("failure_mode")
        if not key_fm or not fm:
            no_key.append(r)
            continue
        overlap = _jaccard(_tokens(key_fm), _tokens(fm))
        if overlap < threshold:
            desynced.append({**r, "_jaccard": overlap, "_key_failure_mode": key_fm})
        else:
            aligned.append(r)

    return AlignmentReport(aligned=aligned, desynced=desynced, no_dedup_key=no_key)
