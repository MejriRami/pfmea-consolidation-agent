"""
Read-side query for standard_line -- the counterpart to
db/standard_line_writer.py's write path. Spec §7 names
`search_pfmea_lines(product_family, operation?, failure_mode?, theme?,
limit)` as an MCP tool surface; this is that lookup, scoped to what
standard_line actually stores (line_payload JSONB), not a new decision
engine or a duplicate of anything in standard_line_writer.py.

`query` matches BOTH the failure-text payload fields AND the operation /
product_family columns -- a bare code like "OP10" or a family name like
"chokes" is just as findable as failure-mode text.

count_by_operation() is the aggregate counterpart: a GROUP BY query for
"how many rows per operation" questions, returning only counts, never row
content -- see its own docstring for why that distinction matters.
"""
from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .models import StandardLine

SEARCHABLE_PAYLOAD_FIELDS = ("failure_mode", "failure_cause", "failure_effect")
SEARCHABLE_COLUMNS = ("operation", "product_family")


def _row_to_dict(record: StandardLine) -> dict:
    return {
        "id": record.id,
        "product_family": record.product_family,
        "operation": record.operation,
        "line_key": record.line_key,
        "version": record.version,
        "status": record.status,
        "approved_by": record.approved_by,
        "approved_at": record.approved_at.isoformat() if record.approved_at else None,
        "line_payload": record.line_payload,
    }


def list_product_families(session: Session) -> list[str]:
    """Distinct product_family values actually present in standard_line --
    "chokes" today, but built to return whatever's really there rather than
    a hardcoded list, so it stays correct as more families get approved
    content."""
    rows = session.query(StandardLine.product_family).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


def search_standard_lines(
    session: Session,
    query: str,
    *,
    limit: int = 20,
    status: str | None = None,
    product_family: str | None = None,
    operation: str | None = None,
) -> list[dict]:
    """Case-insensitive substring match against failure_mode/failure_cause/
    failure_effect inside line_payload, AND against the operation and
    product_family columns themselves -- so query="OP10" or query="chokes"
    finds rows by operation code / product family, not just by failure
    text. (Originally this only checked the payload text fields, which
    meant search("OP10") returned nothing even though 111 real OP10 rows
    existed -- the operation code just never appears inside a failure
    description.) `status`/`product_family`/`operation` remain separate
    EXACT-match filters for narrowing further. An empty `query` matches
    everything (subject to the other filters) -- lets a caller list e.g.
    "all approved OP60 lines" without forcing a keyword."""
    q = session.query(StandardLine)
    if status:
        q = q.filter(StandardLine.status == status)
    if product_family:
        q = q.filter(StandardLine.product_family == product_family)
    if operation:
        q = q.filter(StandardLine.operation == operation)
    if query:
        pattern = f"%{query}%"
        payload_matches = (
            StandardLine.line_payload[field].astext.ilike(pattern) for field in SEARCHABLE_PAYLOAD_FIELDS
        )
        column_matches = (getattr(StandardLine, col).ilike(pattern) for col in SEARCHABLE_COLUMNS)
        q = q.filter(or_(*payload_matches, *column_matches))
    rows = q.order_by(StandardLine.id).limit(limit).all()
    return [_row_to_dict(r) for r in rows]


def count_by_operation(
    session: Session,
    *,
    status: str | None = None,
    product_family: str | None = None,
) -> dict[str, int]:
    """Aggregate GROUP BY operation, returning just {operation: count} --
    for questions like "which operations have the most rows" that don't
    need any row content, just counts. Runs entirely in the database (one
    COUNT(*)...GROUP BY query); never loads the underlying rows, so it
    stays cheap regardless of how many rows standard_line holds (built
    after search_standard_lines's default limit=20 forced an agent to page
    through -- or, on one bad call, try to load all 496 rows at once and
    blow its context window -- just to answer an aggregate question)."""
    q = session.query(StandardLine.operation, func.count())
    if status:
        q = q.filter(StandardLine.status == status)
    if product_family:
        q = q.filter(StandardLine.product_family == product_family)
    rows = q.group_by(StandardLine.operation).order_by(func.count().desc()).all()
    return {operation: count for operation, count in rows}
