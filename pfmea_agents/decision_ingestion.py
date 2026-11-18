"""
Phase 4 Path 2, closing the loop: reads a COMPLETED
Phase4_Cluster_Review.xlsx (an engineer's Merge / Keep Separate / Unsure
calls, made in exports/Phase4_Cluster_Review.xlsx — see
exports/export_phase4_review_workbook.py for how that file was produced)
and turns those decisions into a final row set, which then goes through
the EXISTING, unmodified Phase 7 exporter
(exports.export_final_pfmea.build_workbook) to produce a clean PFMEA
workbook.

DECISION SEMANTICS:
  - Merge          -> the candidate's content is already represented by
                      this cluster's anchor baseline row. EXCLUDED from
                      the final row set (absorbed_by = anchor row_id),
                      logged to the exclusion log for traceability.
  - Keep Separate  -> genuine, distinct risk. INCLUDED as its own row.
  - Unsure / blank -> per instruction: "keep them separate and flag them."
                      INCLUDED as its own row (same as Keep Separate), but
                      tagged FLAGGED_UNCERTAIN_PENDING_REVIEW in
                      Consolidation_Tag (column AS) AND prefixed in the
                      justification text, so it's visible without knowing
                      to look at a specific column. Blank is treated the
                      same as Unsure, not as an error -- a real review
                      pass will always have some rows nobody got to yet,
                      and silently dropping them would be exactly the
                      "missing a real risk" failure this whole project has
                      been designed to avoid.
  - anything else  -> raises InvalidDecisionError. Never guessed.
  - Unclustered rows (no baseline was ever similar enough to compare
    against) have no decision field at all in the review workbook --
    included as ordinary enrichment, unflagged.

WHERE THE DATA COMES FROM: the review workbook only carries row_id /
source / failure_mode / cause / effect / decision / notes -- not
severity/occurrence/detection/AP, which a real PFMEA row needs. So:
  - MASTER_REF baseline rows reuse their EXISTING
    ground_truth/final_lines.jsonl record verbatim (already curated,
    unchanged by this exercise -- baseline is always preserved).
  - Newly-included candidate rows are reconstructed from
    ground_truth/raw_lines.jsonl (process_step, failure_mode/cause/effect,
    severity/occurrence/detection/ap). Fields raw_lines.jsonl doesn't carry
    (process_item, work_element, function_item/step/element, special_char,
    dedup_key, op_sequence_index) are left blank -- this pipeline doesn't
    populate them for new content yet, which is a real, documented gap,
    not a fabrication.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import openpyxl

from exports.export_final_pfmea import GROUND_TRUTH_DIR, build_workbook, load_final_lines

BASELINE_SOURCE = "MASTER_REF"
GROUPED_SHEET_NAME = "01_ALL_SOURCES_GROUPED"

VALID_DECISIONS = {"Merge", "Keep Separate", "Unsure"}
CLUSTER_HEADER_RE = re.compile(
    r"^Cluster (\d+) of (\d+)\s+—\s+Operation (\S+)\s+—\s+(\d+) rows"
    r"(?:\s+—\s+anchored on baseline (\S+))?$"
)


class InvalidDecisionError(ValueError):
    pass


@dataclass
class ClusterRowEntry:
    row_id: str
    source: str
    failure_mode: str | None
    failure_cause: str | None
    failure_effect: str | None
    decision: str | None  # raw cell value: "Merge" | "Keep Separate" | "Unsure" | None
    notes: str | None
    is_baseline: bool
    cluster_number: int
    operation: str
    anchor_row_id: str | None


@dataclass
class UnclusteredRowEntry:
    row_id: str
    source: str
    operation: str | None
    notes: str | None


@dataclass
class ReviewedWorkbook:
    cluster_rows: list[ClusterRowEntry]
    unclustered_rows: list[UnclusteredRowEntry]


@dataclass
class ResolvedRow:
    row_id: str
    source: str
    decision: Literal["INCLUDE_BASELINE", "INCLUDE_ENRICHMENT", "EXCLUDE_DUPLICATE"]
    absorbed_by: str | None = None
    flagged_uncertain: bool = False
    cluster_number: int | None = None
    anchor_row_id: str | None = None
    notes: str | None = None


def _non_empty_rows(ws, start_row: int):
    for row in ws.iter_rows(min_row=start_row, values_only=False):
        yield row


def read_review_workbook(path: Path) -> ReviewedWorkbook:
    wb = openpyxl.load_workbook(path, data_only=True)
    cluster_rows: list[ClusterRowEntry] = []

    ws = wb["Clusters for Review"]
    current_cluster: dict | None = None
    expect_column_header = False
    for row in _non_empty_rows(ws, 1):
        a_value = row[0].value
        if isinstance(a_value, str) and CLUSTER_HEADER_RE.match(a_value):
            m = CLUSTER_HEADER_RE.match(a_value)
            current_cluster = {
                "number": int(m.group(1)),
                "operation": m.group(3),
                "anchor_row_id": m.group(5),
            }
            expect_column_header = True
            continue
        if current_cluster is None:
            continue  # title/subtitle/instructions rows before the first cluster
        if expect_column_header:
            expect_column_header = False
            continue  # the "Row ID | Source | ..." header row itself
        if a_value is None:
            current_cluster = None  # blank separator row -> cluster ended
            continue

        row_id, source, failure_mode, failure_cause, failure_effect, decision, notes = (
            c.value for c in row[:7]
        )
        cluster_rows.append(ClusterRowEntry(
            row_id=row_id, source=source, failure_mode=failure_mode, failure_cause=failure_cause,
            failure_effect=failure_effect, decision=decision, notes=notes,
            is_baseline=(source == BASELINE_SOURCE),
            cluster_number=current_cluster["number"], operation=current_cluster["operation"],
            anchor_row_id=current_cluster["anchor_row_id"],
        ))

    unclustered_rows: list[UnclusteredRowEntry] = []
    ws2 = wb["Unclustered Rows"]
    header_row_idx = None
    for i, row in enumerate(_non_empty_rows(ws2, 1), 1):
        if row[0].value == "Row ID":
            header_row_idx = i
            continue
        if header_row_idx is None or row[0].value is None:
            continue
        row_id, source, operation, _mode, _cause, _effect, notes = (c.value for c in row[:7])
        unclustered_rows.append(UnclusteredRowEntry(row_id=row_id, source=source, operation=operation, notes=notes))

    return ReviewedWorkbook(cluster_rows=cluster_rows, unclustered_rows=unclustered_rows)


def resolve_decisions(reviewed: ReviewedWorkbook) -> list[ResolvedRow]:
    resolved: list[ResolvedRow] = []
    errors: list[str] = []

    for entry in reviewed.cluster_rows:
        if entry.is_baseline:
            resolved.append(ResolvedRow(row_id=entry.row_id, source=entry.source, decision="INCLUDE_BASELINE"))
            continue

        decision = (entry.decision or "").strip() or None
        if decision is not None and decision not in VALID_DECISIONS:
            errors.append(
                f"cluster {entry.cluster_number} ({entry.operation}), row {entry.row_id}: "
                f"unrecognized Engineer Decision {decision!r} (expected one of {sorted(VALID_DECISIONS)}, or blank)"
            )
            continue

        if decision == "Merge":
            if not entry.anchor_row_id:
                errors.append(
                    f"cluster {entry.cluster_number} ({entry.operation}), row {entry.row_id}: "
                    f"marked Merge but this cluster has no baseline anchor to absorb into"
                )
                continue
            resolved.append(ResolvedRow(
                row_id=entry.row_id, source=entry.source, decision="EXCLUDE_DUPLICATE",
                absorbed_by=entry.anchor_row_id, cluster_number=entry.cluster_number,
                anchor_row_id=entry.anchor_row_id, notes=entry.notes,
            ))
        else:
            # "Keep Separate" -> included, unflagged. "Unsure" or blank ->
            # included, flagged -- see module docstring for why blank is
            # NOT an error.
            resolved.append(ResolvedRow(
                row_id=entry.row_id, source=entry.source, decision="INCLUDE_ENRICHMENT",
                flagged_uncertain=(decision != "Keep Separate"),
                cluster_number=entry.cluster_number, anchor_row_id=entry.anchor_row_id, notes=entry.notes,
            ))

    for entry in reviewed.unclustered_rows:
        resolved.append(ResolvedRow(
            row_id=entry.row_id, source=entry.source, decision="INCLUDE_ENRICHMENT",
            flagged_uncertain=False, notes=entry.notes,
        ))

    if errors:
        raise InvalidDecisionError(
            f"{len(errors)} row(s) have an invalid or unresolvable decision:\n  " + "\n  ".join(errors)
        )
    return resolved


def _justification_for(row: ResolvedRow) -> str:
    if row.decision == "INCLUDE_BASELINE":
        return "Master reference row preserved as baseline."
    prefix = "[UNCERTAIN -- NEEDS FOLLOW-UP REVIEW] " if row.flagged_uncertain else ""
    origin = (
        f"cluster {row.cluster_number}, anchored on baseline {row.anchor_row_id}"
        if row.anchor_row_id else "no baseline was similar enough to group this row for review"
    )
    notes = f" Engineer notes: {row.notes}" if row.notes else ""
    return f"{prefix}Human-reviewed via Phase 4 cluster workbook ({origin}).{notes}"


def build_final_lines(
    resolved: list[ResolvedRow],
    *,
    ground_truth_dir: Path = GROUND_TRUTH_DIR,
) -> tuple[list[dict], list[dict]]:
    """Returns (final_lines_records, exclusion_log_records). The first is
    shaped exactly for exports.export_final_pfmea.build_workbook(); the
    second is a traceability record of every row that was merged away."""
    final_lines_by_key = {(r["source"], r["row_id"]): r for r in load_final_lines(ground_truth_dir / "final_lines.jsonl")}
    with (ground_truth_dir / "raw_lines.jsonl").open(encoding="utf-8") as fh:
        raw_lines_by_key = {(r["source"], r["row_id"]): r for r in (json.loads(line) for line in fh)}

    final_lines: list[dict] = []
    exclusion_log: list[dict] = []

    for row in resolved:
        key = (row.source, row.row_id)

        if row.decision == "EXCLUDE_DUPLICATE":
            exclusion_log.append({
                "row_id": row.row_id, "source": row.source, "decision": "EXCLUDE_DUPLICATE",
                "absorbed_by": row.absorbed_by, "cluster_number": row.cluster_number, "notes": row.notes,
            })
            continue

        if row.decision == "INCLUDE_BASELINE":
            if key not in final_lines_by_key:
                raise KeyError(f"baseline row {key} not found in ground_truth/final_lines.jsonl")
            final_lines.append(final_lines_by_key[key])
            continue

        # INCLUDE_ENRICHMENT: reconstruct from raw_lines.jsonl
        if key not in raw_lines_by_key:
            raise KeyError(f"candidate row {key} not found in ground_truth/raw_lines.jsonl")
        raw = raw_lines_by_key[key]
        origin_tag = (
            "FLAGGED_UNCERTAIN_PENDING_REVIEW" if row.flagged_uncertain
            else ("HUMAN_REVIEWED_ENRICHMENT_KEPT_SEPARATE" if row.anchor_row_id
                  else "NON_DUPLICATE_ENRICHMENT_UNCLUSTERED")
        )
        source_ref = f"{row.source}:{row.row_id}@{GROUPED_SHEET_NAME}!R{raw.get('excel_row')}"
        final_lines.append({
            "row_id": row.row_id,
            "row_id_column_a_untrusted": row.row_id,
            "source": row.source,
            "process_item": None, "process_step": raw.get("process_step"),
            "work_element": None,
            "function_item": None, "function_step": None, "function_element": None,
            "failure_mode": raw.get("failure_mode"), "failure_effect": raw.get("failure_effect"),
            "failure_cause": raw.get("failure_cause"),
            "prevention_control": None, "detection_control": None,
            "severity": raw.get("severity"), "occurrence": raw.get("occurrence"), "detection": raw.get("detection"),
            "ap": raw.get("ap"), "special_char": None,
            "op_sequence_index": None,
            "consolidation_tag": origin_tag,
            "master_comparator": (
                f"CLUSTER_{row.cluster_number}_ANCHOR_{row.anchor_row_id}" if row.anchor_row_id
                else "UNCLUSTERED_NO_BASELINE_MATCH"
            ),
            "selected_source_rows": source_ref,
            "justification": _justification_for(row),
            "source_row_refs": source_ref,
            "dedup_key": None,
        })

    return final_lines, exclusion_log


def ingest_and_export(
    review_workbook_path: Path,
    output_xlsx_path: Path,
    *,
    ground_truth_dir: Path = GROUND_TRUTH_DIR,
    exclusion_log_path: Path | None = None,
) -> dict[str, int]:
    reviewed = read_review_workbook(review_workbook_path)
    resolved = resolve_decisions(reviewed)
    final_lines, exclusion_log = build_final_lines(resolved, ground_truth_dir=ground_truth_dir)

    wb = build_workbook(final_lines)
    output_xlsx_path = Path(output_xlsx_path)
    output_xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_xlsx_path)

    if exclusion_log_path is not None:
        exclusion_log_path = Path(exclusion_log_path)
        exclusion_log_path.parent.mkdir(parents=True, exist_ok=True)
        exclusion_log_path.write_text(json.dumps(exclusion_log, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "total_rows": len(final_lines),
        "baseline_rows": sum(1 for r in resolved if r.decision == "INCLUDE_BASELINE"),
        "kept_separate": sum(1 for r in resolved if r.decision == "INCLUDE_ENRICHMENT" and not r.flagged_uncertain),
        "flagged_uncertain": sum(1 for r in resolved if r.decision == "INCLUDE_ENRICHMENT" and r.flagged_uncertain),
        "merged_excluded": len(exclusion_log),
    }
