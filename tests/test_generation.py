"""
Live-database tests for pfmea_agents/generation.py. Skipped entirely when
DATABASE_URL isn't set -- same pattern as the other DB tests in this repo.

Uses a dedicated, fake product_family (not "chokes") for every row this
file writes, so counts/assertions are exact and fully isolated from the
real ~178 approved chokes rows already in the live table -- no need for
the "filter to my own prefix" tolerance pattern the chokes-scoped tests
use elsewhere.
"""
from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — generation tests need a real Postgres connection",
)

if os.environ.get("DATABASE_URL"):
    from db.connection import get_engine, get_session_factory
    from db.models import AuditLog, Base, StandardLine
    from db.standard_line_writer import approve_standard_line, write_consolidated_rows
    from pfmea_agents.generation import (
        CONTENT_TYPE_AI_SUGGESTION,
        CONTENT_TYPE_VALIDATED,
        GenerationResult,
        OperationAvailability,
        generate_pfmea,
        list_operations,
        list_product_families,
        suggest_additions,
    )

requires_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — suggest_additions needs a real model call",
)


def run(coro):
    return asyncio.run(coro)

TEST_PRODUCT_FAMILY = "test_family_generation"
TEST_ROW_PREFIX = "ZZTEST_GEN_"
TEST_SOURCE = "GENERATION_TEST_SRC"
TEST_ACTOR = "pytest_generation"


def make_row(row_id, *, process_step="RM incoming inspection", op_sequence_index=None, **overrides):
    row_id = TEST_ROW_PREFIX + row_id
    row = {
        "row_id": row_id, "source": TEST_SOURCE, "process_item": None, "process_step": process_step,
        "work_element": None, "function_item": None, "function_step": None, "function_element": None,
        "failure_mode": f"failure mode for {row_id}", "failure_effect": "effect", "failure_cause": "cause",
        "prevention_control": None, "detection_control": None,
        "severity": 5, "occurrence": 3, "detection": 4, "ap": "L", "special_char": None,
        "op_sequence_index": op_sequence_index, "consolidation_tag": "TEST_TAG", "master_comparator": "TEST_COMPARATOR",
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


def _cleanup(session_factory):
    with session_factory() as session:
        rows = (
            session.query(StandardLine)
            .filter(StandardLine.product_family == TEST_PRODUCT_FAMILY)
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


@pytest.fixture
def seeded(session_factory):
    """OP10: 2 approved rows + 1 draft row (same operation, mixed status).
    OP60: 1 approved row. No rows at all for OP150 -- an operation with
    zero content."""
    _cleanup(session_factory)

    rows = [
        make_row("OP10-A", process_step="RM incoming inspection", op_sequence_index=2,
                 failure_mode="OP10 approved A"),
        make_row("OP10-B", process_step="RM incoming inspection", op_sequence_index=1,
                 failure_mode="OP10 approved B"),
        make_row("OP10-C-DRAFT", process_step="RM incoming inspection", failure_mode="OP10 draft, never approved"),
        make_row("OP60-A", process_step="Coil winding", failure_mode="OP60 approved A"),
    ]
    write_consolidated_rows(session_factory(), rows, actor=TEST_ACTOR, product_family=TEST_PRODUCT_FAMILY)

    with session_factory() as session:
        to_approve = (
            session.query(StandardLine)
            .filter(StandardLine.product_family == TEST_PRODUCT_FAMILY)
            .filter(StandardLine.line_key.in_([
                f"{TEST_SOURCE}:{TEST_ROW_PREFIX}OP10-A",
                f"{TEST_SOURCE}:{TEST_ROW_PREFIX}OP10-B",
                f"{TEST_SOURCE}:{TEST_ROW_PREFIX}OP60-A",
            ]))
            .all()
        )
        assert len(to_approve) == 3
        for record in to_approve:
            approve_standard_line(session_factory(), record.id, approved_by=TEST_ACTOR)

    yield
    _cleanup(session_factory)


# --- list_product_families ----------------------------------------------

def test_list_product_families_includes_the_seeded_family(seeded, session_factory):
    with session_factory() as session:
        families = list_product_families(session)
    assert TEST_PRODUCT_FAMILY in families


def test_list_product_families_returns_sorted_distinct_list(session_factory):
    with session_factory() as session:
        families = list_product_families(session)
    assert families == sorted(set(families))


# --- list_operations -------------------------------------------------------

def test_list_operations_only_includes_operations_with_approved_content(seeded, session_factory):
    with session_factory() as session:
        ops = list_operations(session, TEST_PRODUCT_FAMILY)
    assert ops == [
        OperationAvailability(operation="OP10", approved_count=2),
        OperationAvailability(operation="OP60", approved_count=1),
    ]


def test_list_operations_excludes_draft_rows_from_the_count(seeded, session_factory):
    """OP10 has 3 total rows (2 approved + 1 draft) but the count must only
    reflect the 2 approved ones."""
    with session_factory() as session:
        ops = list_operations(session, TEST_PRODUCT_FAMILY)
    op10 = next(o for o in ops if o.operation == "OP10")
    assert op10.approved_count == 2


def test_list_operations_returns_canonical_sequence_order(seeded, session_factory):
    with session_factory() as session:
        ops = list_operations(session, TEST_PRODUCT_FAMILY)
    assert [o.operation for o in ops] == ["OP10", "OP60"]  # OP10 before OP60, not count-sorted


def test_list_operations_empty_for_unknown_product_family(session_factory):
    with session_factory() as session:
        ops = list_operations(session, "no_such_product_family_at_all")
    assert ops == []


# --- generate_pfmea ----------------------------------------------------------

def test_generate_pfmea_assembles_only_approved_rows(seeded, session_factory):
    with session_factory() as session:
        result = generate_pfmea(session, TEST_PRODUCT_FAMILY, ["OP10", "OP60"])

    assert isinstance(result, GenerationResult)
    assert result.status == "Draft"
    assert result.summary.total_line_items == 3
    assert result.summary.operations_included == ["OP10", "OP60"]
    assert result.summary.operations_with_no_content == []

    failure_modes = {r["line_payload"]["failure_mode"] for r in result.rows}
    assert failure_modes == {"OP10 approved A", "OP10 approved B", "OP60 approved A"}
    assert "OP10 draft, never approved" not in failure_modes  # the draft row must never appear


def test_generate_pfmea_marks_every_row_validated_with_provenance(seeded, session_factory):
    with session_factory() as session:
        result = generate_pfmea(session, TEST_PRODUCT_FAMILY, ["OP10"])

    assert len(result.rows) == 2
    for row in result.rows:
        assert row["content_type"] == CONTENT_TYPE_VALIDATED
        assert row["status"] == "approved"
        assert row["id"] is not None
        assert row["line_key"].startswith(TEST_SOURCE)
        assert row["version"] == 1
        assert row["approved_by"] == TEST_ACTOR
        assert row["approved_at"] is not None


def test_generate_pfmea_orders_rows_by_operation_then_op_sequence_index(seeded, session_factory):
    with session_factory() as session:
        result = generate_pfmea(session, TEST_PRODUCT_FAMILY, ["OP60", "OP10"])  # requested out of order

    # OP10 rows must come before OP60 rows regardless of request order, and
    # within OP10, op_sequence_index=1 (row B) before op_sequence_index=2 (row A).
    modes = [r["line_payload"]["failure_mode"] for r in result.rows]
    assert modes == ["OP10 approved B", "OP10 approved A", "OP60 approved A"]


def test_generate_pfmea_reports_operation_with_no_approved_content(seeded, session_factory):
    with session_factory() as session:
        result = generate_pfmea(session, TEST_PRODUCT_FAMILY, ["OP150"])

    assert result.rows == []
    assert result.summary.total_line_items == 0
    assert result.summary.operations_included == []
    assert result.summary.operations_with_no_content == ["OP150"]


def test_generate_pfmea_handles_a_mix_of_available_and_unavailable_operations(seeded, session_factory):
    with session_factory() as session:
        result = generate_pfmea(session, TEST_PRODUCT_FAMILY, ["OP10", "OP150"])

    assert result.summary.operations_included == ["OP10"]
    assert result.summary.operations_with_no_content == ["OP150"]
    assert result.summary.operations_requested == ["OP10", "OP150"]
    assert result.summary.total_line_items == 2
    assert all(r["operation"] == "OP10" for r in result.rows)


def test_generate_pfmea_with_no_selected_operations_returns_empty_result(seeded, session_factory):
    with session_factory() as session:
        result = generate_pfmea(session, TEST_PRODUCT_FAMILY, [])
    assert result.rows == []
    assert result.summary.total_line_items == 0
    assert result.summary.operations_included == []
    assert result.summary.operations_with_no_content == []


# --- suggest_additions (Layer 2: AI suggestions) --------------------------------
# Real model calls -- also gated behind OPENAI_API_KEY, on top of this
# file's module-level DATABASE_URL gate.

@requires_openai
def test_suggest_additions_structural_contract_and_no_verbatim_duplicates(seeded, session_factory):
    """Every suggestion: marked AI_SUGGESTION, has a rationale, is scoped
    to the requested operation/family, and carries NONE of a VALIDATED
    row's provenance keys -- there is nothing to carry, it was never a
    standard_line row. Also checks the model didn't just echo back an
    existing failure mode verbatim (best-effort content check; exact
    wording overlap is the one thing we can assert deterministically)."""
    with session_factory() as session:
        existing = generate_pfmea(session, TEST_PRODUCT_FAMILY, ["OP10"])
    existing_modes = {r["line_payload"]["failure_mode"] for r in existing.rows}
    assert existing_modes  # sanity: the fixture actually seeded OP10 content

    suggestions = run(suggest_additions(TEST_PRODUCT_FAMILY, "OP10", existing.rows))

    assert isinstance(suggestions, list)
    for s in suggestions:
        assert s["content_type"] == CONTENT_TYPE_AI_SUGGESTION
        assert s["operation"] == "OP10"
        assert s["product_family"] == TEST_PRODUCT_FAMILY
        assert s["rationale"].strip()
        assert s["line_payload"]["failure_mode"].strip()
        assert s["line_payload"]["failure_mode"] not in existing_modes
        for forbidden_key in ("id", "line_key", "version", "status", "approved_by", "approved_at"):
            assert forbidden_key not in s, f"suggestion carries provenance key {forbidden_key!r} it must not have"


@requires_openai
def test_suggest_additions_never_writes_to_standard_line(seeded, session_factory):
    with session_factory() as session:
        existing = generate_pfmea(session, TEST_PRODUCT_FAMILY, ["OP10"])
        count_before = session.query(StandardLine).count()

    run(suggest_additions(TEST_PRODUCT_FAMILY, "OP10", existing.rows))

    with session_factory() as session:
        count_after = session.query(StandardLine).count()
    assert count_after == count_before


@requires_openai
def test_suggest_additions_works_for_an_operation_with_no_existing_content():
    """OP150 has zero rows in this file's fixtures (and isn't seeded at
    all here) -- suggest_additions must still work with an empty
    existing_validated_rows list rather than requiring non-empty input."""
    suggestions = run(suggest_additions(TEST_PRODUCT_FAMILY, "OP150", []))
    assert isinstance(suggestions, list)
    for s in suggestions:
        assert s["content_type"] == CONTENT_TYPE_AI_SUGGESTION
        assert s["operation"] == "OP150"
