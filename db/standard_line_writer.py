"""
Writes decision_ingestion's consolidated row output into standard_line,
with versioning and an audit_log entry on every write (spec §6/§7).

Two separate entry points, deliberately kept apart:

  - write_consolidated_rows() / write_from_review_workbook(): the
    automated path. Every row it writes lands with status='draft'. This
    is the ONLY writer the decision-ingestion pipeline ever calls.
  - approve_standard_line(): the ONLY function in this codebase allowed
    to set status='approved'. It requires an explicit human `approved_by`
    identity and is never called from the ingestion path — spec §7:
    "No MCP tool may write to standard_line with status='approved'.
    Approval is human-only, via the app."

VERSIONING: identity is `line_key` (f"{source}:{row_id}"; see
StandardLine's docstring in db/models.py for why this stands in for
spec's normalized_line_id). Writing a row whose line_key already has a
version with an identical line_payload is a no-op (repeat ingestion of an
unchanged review workbook doesn't churn version numbers). Otherwise a NEW
row is inserted with version = previous + 1, status='draft', and
supersedes = the previous row's id — the previous row is never mutated,
so its history (including any prior approval) stays intact.

OPERATION: reconstructed enrichment rows from decision_ingestion don't
carry an `operation` field (only `process_step`); baseline rows loaded
verbatim from ground_truth/final_lines.jsonl already do. Falls back to
rules.operations.resolve_operation(process_step) when missing. Since
operation is NOT NULL and rules.operations deliberately returns None
rather than guess on ambiguous text, an unresolvable operation raises
OperationResolutionError naming the row -- it needs a human/mapper fix,
not a fabricated value.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from pfmea_agents.decision_ingestion import build_final_lines, read_review_workbook, resolve_decisions
from rules.operations import resolve_operation

from .models import AuditLog, StandardLine

GROUND_TRUTH_DIR = Path(__file__).resolve().parent.parent / "ground_truth"
DEFAULT_PRODUCT_FAMILY = "chokes"  # spec: "Pilot scope: Chokes / Core Assembly"


class OperationResolutionError(ValueError):
    pass


def _line_key(source: str, row_id: str) -> str:
    return f"{source}:{row_id}"


def _resolve_operation_for(row: dict) -> str:
    operation = row.get("operation")
    if operation:
        return operation
    match = resolve_operation(row.get("process_step") or "")
    if match.code is None:
        raise OperationResolutionError(
            f"row {row.get('source')}:{row.get('row_id')}: could not resolve an operation from "
            f"process_step {row.get('process_step')!r} (ambiguous with {match.ambiguous_with}) -- "
            f"needs human/mapper disambiguation before it can become a standard_line row"
        )
    return match.code


def write_consolidated_rows(
    session: Session,
    final_lines: list[dict],
    *,
    actor: str,
    product_family: str = DEFAULT_PRODUCT_FAMILY,
    change_reason: str | None = None,
) -> dict[str, int]:
    """Writes each row in `final_lines` (the exact shape produced by
    pfmea_agents.decision_ingestion.build_final_lines) into standard_line
    as a new draft version. Never sets status='approved'."""
    inserted = 0
    unchanged = 0
    superseded = 0

    for row in final_lines:
        key = _line_key(row["source"], row["row_id"])
        operation = _resolve_operation_for(row)

        latest = (
            session.query(StandardLine)
            .filter(StandardLine.line_key == key)
            .order_by(StandardLine.version.desc())
            .first()
        )

        if latest is not None and latest.line_payload == row:
            unchanged += 1
            continue

        record = StandardLine(
            product_family=product_family,
            operation=operation,
            line_key=key,
            line_payload=row,
            version=(latest.version + 1 if latest else 1),
            status="draft",
            supersedes=latest.id if latest else None,
            change_reason=change_reason,
        )
        session.add(record)
        session.flush()  # populate record.id for the audit_log FK-by-convention record_id

        session.add(AuditLog(
            actor=actor,
            action="CREATE_NEW_VERSION" if latest is not None else "CREATE_DRAFT",
            table_name="standard_line",
            record_id=record.id,
            before=(latest.line_payload if latest is not None else None),
            after=row,
            reason=change_reason,
        ))

        inserted += 1
        if latest is not None:
            superseded += 1

    session.commit()
    return {"inserted": inserted, "unchanged": unchanged, "superseded": superseded}


def write_from_review_workbook(
    session: Session,
    review_workbook_path: Path,
    *,
    actor: str,
    ground_truth_dir: Path = GROUND_TRUTH_DIR,
    product_family: str = DEFAULT_PRODUCT_FAMILY,
    change_reason: str | None = None,
) -> dict[str, int]:
    """End-to-end: parses a completed Phase4_Cluster_Review.xlsx, resolves
    the engineer's decisions, and writes the resulting consolidated rows
    into standard_line as drafts. Rows the engineer marked Merge
    (EXCLUDE_DUPLICATE) never reach standard_line at all -- they're
    absorbed by their cluster's baseline, same as decision_ingestion's own
    exclusion_log."""
    reviewed = read_review_workbook(review_workbook_path)
    resolved = resolve_decisions(reviewed)
    final_lines, _exclusion_log = build_final_lines(resolved, ground_truth_dir=ground_truth_dir)
    return write_consolidated_rows(
        session, final_lines, actor=actor, product_family=product_family, change_reason=change_reason,
    )


def approve_standard_line(
    session: Session,
    standard_line_id: int,
    *,
    approved_by: str,
    reason: str | None = None,
) -> StandardLine:
    """The ONLY function in this codebase allowed to set
    status='approved'. Requires a named human reviewer -- never called by
    write_consolidated_rows / write_from_review_workbook or any other
    automated path."""
    if not approved_by:
        raise ValueError("approved_by is required -- approval must be attributable to a named human reviewer")

    record = session.get(StandardLine, standard_line_id)
    if record is None:
        raise ValueError(f"no standard_line row with id={standard_line_id}")

    before = {"status": record.status, "approved_by": record.approved_by, "approved_at": None}
    record.status = "approved"
    record.approved_by = approved_by
    record.approved_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()

    session.add(AuditLog(
        actor=approved_by,
        action="APPROVE",
        table_name="standard_line",
        record_id=record.id,
        before=before,
        after={"status": "approved", "approved_by": approved_by, "approved_at": record.approved_at.isoformat()},
        reason=reason,
    ))
    session.commit()
    return record
