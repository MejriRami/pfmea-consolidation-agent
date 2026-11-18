"""
Live-database tests for db/standard_line_query.py. Skipped entirely when
DATABASE_URL isn't set -- same pattern as tests/test_db_schema.py and
tests/test_standard_line_writer.py.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — standard_line query tests need a real Postgres connection",
)

if os.environ.get("DATABASE_URL"):
    from db.connection import get_engine, get_session_factory
    from db.models import AuditLog, Base, StandardLine
    from db.standard_line_query import count_by_operation, search_standard_lines
    from db.standard_line_writer import write_consolidated_rows

TEST_ROW_PREFIX = "ZZTEST_SLQ_"
TEST_SOURCE = "STANDARD_LINE_QUERY_TEST_SRC"
TEST_ACTOR = "pytest_standard_line_query"


def make_row(row_id, *, process_step="RM incoming inspection", **overrides):
    row_id = TEST_ROW_PREFIX + row_id
    row = {
        "row_id": row_id, "source": TEST_SOURCE, "process_item": None, "process_step": process_step,
        "work_element": None, "function_item": None, "function_step": None, "function_element": None,
        "failure_mode": "Torn packaging", "failure_effect": "Customer claim", "failure_cause": "Bad storage",
        "prevention_control": None, "detection_control": None,
        "severity": 5, "occurrence": 3, "detection": 4, "ap": "L", "special_char": None,
        "op_sequence_index": None, "consolidation_tag": "TEST_TAG", "master_comparator": "TEST_COMPARATOR",
        "selected_source_rows": f"{TEST_SOURCE}:{row_id}", "justification": "test justification",
        "source_row_refs": f"{TEST_SOURCE}:{row_id}", "dedup_key": None,
    }
    row.update(overrides)
    return row


@pytest.fixture(scope="module")
def engine():
    eng = get_engine()
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="module")
def session_factory(engine):
    return get_session_factory()


@pytest.fixture
def seeded_rows(session_factory):
    def _cleanup():
        with session_factory() as session:
            rows = (
                session.query(StandardLine)
                .filter(StandardLine.line_key.like(f"%{TEST_ROW_PREFIX}%"))
                .order_by(StandardLine.version.desc())
                .all()
            )
            ids = [r.id for r in rows]
            for r in rows:
                session.delete(r)
                session.flush()
            if ids:
                session.query(AuditLog).filter(
                    AuditLog.table_name == "standard_line", AuditLog.record_id.in_(ids)
                ).delete(synchronize_session=False)
            session.commit()

    _cleanup()
    write_consolidated_rows(
        session_factory(),
        [
            make_row("PACK-1", failure_mode="Torn packaging", failure_cause="Bad storage handling",
                     process_step="RM incoming inspection"),
            make_row("WIND-1", failure_mode="Incorrect turn count", failure_cause="Wrong program setting",
                     process_step="Coil winding"),
        ],
        actor=TEST_ACTOR,
    )
    yield
    _cleanup()


def test_search_query_matches_operation_code_directly(seeded_rows, session_factory):
    """The actual reported bug: search("OP10") returned nothing even
    though 111 real OP10 rows existed, because the free-text `query` only
    checked failure-mode/cause/effect text, never the operation column
    itself. limit is generous here since the live table already has many
    real OP10 rows ordered by id ahead of these freshly-seeded ones."""
    with session_factory() as session:
        results = search_standard_lines(session, "OP10", limit=1000)
    row_ids = {r["line_payload"]["row_id"] for r in results}
    assert f"{TEST_ROW_PREFIX}PACK-1" in row_ids
    assert f"{TEST_ROW_PREFIX}WIND-1" not in row_ids


def test_search_query_matches_product_family(seeded_rows, session_factory):
    with session_factory() as session:
        results = search_standard_lines(session, "chokes", limit=1000)
    row_ids = {r["line_payload"]["row_id"] for r in results}
    assert f"{TEST_ROW_PREFIX}PACK-1" in row_ids
    assert f"{TEST_ROW_PREFIX}WIND-1" in row_ids


def test_search_matches_failure_mode_case_insensitively(seeded_rows, session_factory):
    with session_factory() as session:
        results = search_standard_lines(session, "torn PACKAGING")
    row_ids = {r["line_payload"]["row_id"] for r in results}
    assert f"{TEST_ROW_PREFIX}PACK-1" in row_ids
    assert f"{TEST_ROW_PREFIX}WIND-1" not in row_ids


def test_search_matches_failure_cause(seeded_rows, session_factory):
    with session_factory() as session:
        results = search_standard_lines(session, "wrong program")
    row_ids = {r["line_payload"]["row_id"] for r in results}
    assert f"{TEST_ROW_PREFIX}WIND-1" in row_ids
    assert f"{TEST_ROW_PREFIX}PACK-1" not in row_ids


def test_search_filters_by_operation(seeded_rows, session_factory):
    """Uses a generous limit and filters to this test's own rows before
    asserting -- the live table also holds real, pre-existing OP60 rows
    (approve_to_db.py has run against real data), so an exact-equality
    check against the whole result set would be broken by that real data,
    not by anything this test is actually verifying."""
    with session_factory() as session:
        results = search_standard_lines(session, "", operation="OP60", limit=1000)
    matching_test_rows = {r["line_payload"]["row_id"] for r in results if TEST_ROW_PREFIX in r["line_key"]}
    assert matching_test_rows == {f"{TEST_ROW_PREFIX}WIND-1"}


def test_search_filters_by_status_finds_nothing_before_approval(seeded_rows, session_factory):
    with session_factory() as session:
        results = search_standard_lines(session, "", status="approved", product_family="chokes")
    matching_test_rows = [r for r in results if TEST_ROW_PREFIX in r["line_key"]]
    assert matching_test_rows == []  # write_consolidated_rows never sets status='approved'


def test_search_respects_limit(seeded_rows, session_factory):
    with session_factory() as session:
        results = search_standard_lines(session, "", product_family="chokes", limit=1)
    assert len(results) <= 1


# --- count_by_operation -------------------------------------------------------

def test_count_by_operation_includes_seeded_rows(seeded_rows, session_factory):
    """count_by_operation aggregates across the WHOLE table by design (a
    global GROUP BY, not scoped to any one caller's rows), so this checks
    the seeded OP10/OP60 rows are reflected, not an exact total -- an
    exact count would be broken by real pre-existing data, same lesson as
    test_search_filters_by_operation above."""
    with session_factory() as session:
        counts = count_by_operation(session, product_family="chokes")
    assert counts.get("OP10", 0) >= 1  # seeded PACK-1
    assert counts.get("OP60", 0) >= 1  # seeded WIND-1


def test_count_by_operation_returns_plain_counts_not_row_content(seeded_rows, session_factory):
    with session_factory() as session:
        counts = count_by_operation(session, product_family="chokes")
    assert isinstance(counts, dict)
    assert counts, "expected at least one operation with rows"
    for operation, count in counts.items():
        assert isinstance(operation, str)
        assert isinstance(count, int)


def test_count_by_operation_is_sorted_descending(seeded_rows, session_factory):
    with session_factory() as session:
        counts = count_by_operation(session, product_family="chokes")
    values = list(counts.values())
    assert values == sorted(values, reverse=True)


def test_count_by_operation_filters_by_status(session_factory):
    with session_factory() as session:
        approved_counts = count_by_operation(session, status="approved", product_family="chokes")
        all_counts = count_by_operation(session, product_family="chokes")
    assert isinstance(approved_counts, dict)
    # approved-only is a subset of every status combined -- never more per operation
    for operation, count in approved_counts.items():
        assert count <= all_counts.get(operation, 0)
