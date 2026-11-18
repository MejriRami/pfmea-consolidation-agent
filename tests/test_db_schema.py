"""
Live-database tests for Phase 5 (spec §6). Skipped entirely when
DATABASE_URL isn't set, so the rest of the offline suite stays green on
any machine/CI without a Postgres server configured — same pattern as the
OPENAI_API_KEY-gated tests elsewhere in this repo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from db.connection import get_engine, get_session_factory
from db.loaders import load_ap_lookup, load_raw_lines, load_source_files
from db.models import ApLookup, AuditLog, RawLine, SourceFile, StandardLine

GROUND_TRUTH_DIR = Path(__file__).resolve().parent.parent / "ground_truth"

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — Phase 5 DB tests need a real Postgres connection",
)


@pytest.fixture(scope="module")
def engine():
    return get_engine()


@pytest.fixture(scope="module")
def session_factory():
    return get_session_factory()


def test_all_five_tables_exist(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"source_file", "raw_line", "ap_lookup", "audit_log", "standard_line"} <= tables


def test_ap_lookup_has_exactly_1000_rows(session_factory):
    with session_factory() as session:
        count = session.query(ApLookup).count()
    assert count == 1000


def test_raw_line_has_exactly_719_rows(session_factory):
    with session_factory() as session:
        count = session.query(RawLine).count()
    assert count == 719


def test_ap_lookup_values_match_ground_truth_exactly(session_factory):
    ap_lookup_json = json.loads((GROUND_TRUTH_DIR / "ap_lookup.json").read_text(encoding="utf-8"))
    with session_factory() as session:
        rows = session.query(ApLookup).all()
        db_lookup = {f"{r.severity}-{r.occurrence}-{r.detection}": r.ap for r in rows}
    assert db_lookup == ap_lookup_json


def test_source_file_row_counts_match_actual_raw_line_distribution(session_factory):
    with session_factory() as session:
        source_files = session.query(SourceFile).all()
        total_row_count_column = sum(sf.row_count for sf in source_files)
        actual_raw_line_count = session.query(RawLine).count()
    assert total_row_count_column == actual_raw_line_count == 719


def test_raw_line_source_file_fk_is_always_populated(session_factory):
    with session_factory() as session:
        orphans = session.execute(
            text("SELECT COUNT(*) FROM raw_line r LEFT JOIN source_file s ON r.source_file_id = s.id "
                 "WHERE s.id IS NULL")
        ).scalar()
    assert orphans == 0


def test_raw_line_source_row_ref_and_payload_are_populated(session_factory):
    with session_factory() as session:
        first = session.query(RawLine).order_by(RawLine.id).first()
    assert first.source_row_ref == "01_ALL_SOURCES_GROUPED!R10"
    assert first.source_row_id == "00.0.1"
    assert first.raw_payload["failure_mode"] == "Damaged raw material packaging"


def test_raw_line_unique_constraint_on_source_file_and_row_ref(session_factory):
    with session_factory() as session:
        dup_count = session.execute(text(
            "SELECT COUNT(*) FROM ("
            "  SELECT source_file_id, source_row_ref, COUNT(*) c FROM raw_line "
            "  GROUP BY source_file_id, source_row_ref HAVING COUNT(*) > 1"
            ") dups"
        )).scalar()
    assert dup_count == 0


def test_standard_line_is_empty_until_a_review_workbook_is_ingested(session_factory):
    with session_factory() as session:
        count = session.query(StandardLine).count()
    assert count == 0  # real table now, but nothing populates it here -- see db/standard_line_writer.py


def test_audit_log_table_exists_and_is_queryable(session_factory):
    with session_factory() as session:
        count = session.query(AuditLog).count()
    assert count >= 0  # just confirms the table is real and queryable


def test_reloading_ap_lookup_is_idempotent(session_factory):
    """Loaders clear their own table before reloading -- re-running must
    not duplicate rows."""
    with session_factory() as session:
        n = load_ap_lookup(session)
        count_after = session.query(ApLookup).count()
    assert n == 1000
    assert count_after == 1000


def test_reloading_raw_lines_is_idempotent(session_factory):
    with session_factory() as session:
        source_file_ids = load_source_files(
            session, [json.loads(l) for l in (GROUND_TRUTH_DIR / "raw_lines.jsonl").open(encoding="utf-8")]
        )
        n = load_raw_lines(session, source_file_ids=source_file_ids)
        count_after = session.query(RawLine).count()
    assert n == 719
    assert count_after == 719
