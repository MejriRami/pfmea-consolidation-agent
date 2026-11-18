"""
Live-database tests for db/standard_line_writer.py. Skipped entirely when
DATABASE_URL isn't set -- same pattern as tests/test_db_schema.py, so the
offline suite stays green without a Postgres server configured.

All test rows use row_ids under TEST_ROW_PREFIX so cleanup can find (and
only touch) rows this file created, never real data -- regardless of
which `source` a given row uses (the baseline-detection path needs an
actual "MASTER_REF" source, so filtering by source alone wouldn't catch
every row this file writes).
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — standard_line writer tests need a real Postgres connection",
)

if os.environ.get("DATABASE_URL"):
    from exports.export_phase4_review_workbook import build_workbook as build_review_workbook
    from db.connection import get_engine, get_session_factory
    from db.models import AuditLog, Base, StandardLine
    from db.standard_line_writer import (
        OperationResolutionError,
        approve_standard_line,
        write_consolidated_rows,
        write_from_review_workbook,
    )

TEST_ROW_PREFIX = "ZZTEST_SLW_"
TEST_SOURCE = "STANDARD_LINE_WRITER_TEST_SRC"
TEST_ACTOR = "pytest_standard_line_writer"


def make_row(row_id, *, source=TEST_SOURCE, process_step="RM incoming inspection", **overrides):
    row_id = TEST_ROW_PREFIX + row_id
    row = {
        "row_id": row_id, "source": source, "process_item": None, "process_step": process_step,
        "work_element": None, "function_item": None, "function_step": None, "function_element": None,
        "failure_mode": "Test failure mode", "failure_effect": "Test effect", "failure_cause": "Test cause",
        "prevention_control": None, "detection_control": None,
        "severity": 5, "occurrence": 3, "detection": 4, "ap": "L", "special_char": None,
        "op_sequence_index": None, "consolidation_tag": "TEST_TAG", "master_comparator": "TEST_COMPARATOR",
        "selected_source_rows": f"{source}:{row_id}", "justification": "test justification",
        "source_row_refs": f"{source}:{row_id}", "dedup_key": None,
    }
    row.update(overrides)
    return row


def _key(row_id, source=TEST_SOURCE):
    return f"{source}:{TEST_ROW_PREFIX}{row_id}"


@pytest.fixture(scope="module")
def engine():
    eng = get_engine()
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="module")
def session_factory(engine):
    return get_session_factory()


@pytest.fixture
def clean_db(session_factory):
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
                session.delete(r)  # one DELETE per row, highest version first -- avoids the
                session.flush()    # self-referential `supersedes` FK complaining about a row
            if ids:                # that's still referenced by a not-yet-deleted later version
                session.query(AuditLog).filter(
                    AuditLog.table_name == "standard_line", AuditLog.record_id.in_(ids)
                ).delete(synchronize_session=False)
            session.commit()

    _cleanup()
    yield
    _cleanup()


# --- write_consolidated_rows ---------------------------------------------

def test_write_creates_draft_version_1(clean_db, session_factory):
    row = make_row("ROW-1")
    summary = write_consolidated_rows(session_factory(), [row], actor=TEST_ACTOR)
    assert summary == {"inserted": 1, "unchanged": 0, "superseded": 0}

    with session_factory() as session:
        record = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).one()
    assert record.version == 1
    assert record.status == "draft"
    assert record.supersedes is None
    assert record.product_family == "chokes"
    assert record.operation == "OP10"
    assert record.line_payload == row


def test_write_never_sets_status_approved(clean_db, session_factory):
    write_consolidated_rows(session_factory(), [make_row("ROW-1"), make_row("ROW-2")], actor=TEST_ACTOR)
    with session_factory() as session:
        statuses = {r.status for r in session.query(StandardLine).filter(
            StandardLine.line_key.like(f"%{TEST_ROW_PREFIX}%")
        )}
    assert statuses == {"draft"}


def test_every_write_creates_an_audit_log_entry(clean_db, session_factory):
    write_consolidated_rows(session_factory(), [make_row("ROW-1")], actor=TEST_ACTOR)
    with session_factory() as session:
        record = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).one()
        entry = session.query(AuditLog).filter(
            AuditLog.table_name == "standard_line", AuditLog.record_id == record.id
        ).one()
    assert entry.actor == TEST_ACTOR
    assert entry.action == "CREATE_DRAFT"
    assert entry.before is None
    assert entry.after["failure_mode"] == "Test failure mode"


def test_reingesting_unchanged_row_is_a_noop(clean_db, session_factory):
    row = make_row("ROW-1")
    write_consolidated_rows(session_factory(), [row], actor=TEST_ACTOR)
    summary = write_consolidated_rows(session_factory(), [row], actor=TEST_ACTOR)
    assert summary == {"inserted": 0, "unchanged": 1, "superseded": 0}

    with session_factory() as session:
        count = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).count()
    assert count == 1  # no duplicate version written


def test_changed_payload_creates_new_version_and_supersedes_previous(clean_db, session_factory):
    write_consolidated_rows(session_factory(), [make_row("ROW-1", failure_mode="Original wording")], actor=TEST_ACTOR)
    with session_factory() as session:
        v1_id = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).one().id

    row_v2 = make_row("ROW-1", failure_mode="Revised wording")
    summary = write_consolidated_rows(session_factory(), [row_v2], actor=TEST_ACTOR, change_reason="reworded")
    assert summary == {"inserted": 1, "unchanged": 0, "superseded": 1}

    with session_factory() as session:
        versions = (
            session.query(StandardLine)
            .filter(StandardLine.line_key == _key("ROW-1"))
            .order_by(StandardLine.version)
            .all()
        )
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].supersedes == v1_id
    assert versions[1].status == "draft"
    assert versions[1].change_reason == "reworded"
    assert versions[1].line_payload["failure_mode"] == "Revised wording"


def test_superseded_version_keeps_its_prior_approval_status(clean_db, session_factory):
    """Approving v1 and then superseding it with v2 must not silently
    flip v1's status -- history is immutable; only a human re-approves v2."""
    write_consolidated_rows(session_factory(), [make_row("ROW-1", failure_mode="v1")], actor=TEST_ACTOR)
    with session_factory() as session:
        v1_id = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).one().id
    approve_standard_line(session_factory(), v1_id, approved_by="qa.engineer@example.com")

    write_consolidated_rows(session_factory(), [make_row("ROW-1", failure_mode="v2")], actor=TEST_ACTOR)

    with session_factory() as session:
        v1_after = session.get(StandardLine, v1_id)
        v2 = session.query(StandardLine).filter(
            StandardLine.line_key == _key("ROW-1"), StandardLine.version == 2
        ).one()
    assert v1_after.status == "approved"
    assert v1_after.approved_by == "qa.engineer@example.com"
    assert v2.status == "draft"
    assert v2.approved_by is None


def test_operation_derived_from_process_step_when_missing(clean_db, session_factory):
    write_consolidated_rows(
        session_factory(), [make_row("ROW-1", process_step="Coil winding turns check")], actor=TEST_ACTOR,
    )
    with session_factory() as session:
        record = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).one()
    assert record.operation == "OP60"


def test_operation_field_on_row_takes_precedence_over_process_step(clean_db, session_factory):
    write_consolidated_rows(
        session_factory(),
        [make_row("ROW-1", process_step="RM incoming inspection", operation="OP99")],
        actor=TEST_ACTOR,
    )
    with session_factory() as session:
        record = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).one()
    assert record.operation == "OP99"


def test_unresolvable_operation_raises_and_names_the_row(clean_db, session_factory):
    row = make_row("ROW-1", process_step="")
    with pytest.raises(OperationResolutionError, match="ROW-1"):
        write_consolidated_rows(session_factory(), [row], actor=TEST_ACTOR)


# --- approve_standard_line -------------------------------------------------

def test_approve_sets_status_and_records_audit_log(clean_db, session_factory):
    write_consolidated_rows(session_factory(), [make_row("ROW-1")], actor=TEST_ACTOR)
    with session_factory() as session:
        record_id = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).one().id

    approve_standard_line(session_factory(), record_id, approved_by="qa.engineer@example.com", reason="looks good")

    with session_factory() as session:
        record = session.get(StandardLine, record_id)
        entry = session.query(AuditLog).filter(
            AuditLog.table_name == "standard_line", AuditLog.record_id == record_id, AuditLog.action == "APPROVE"
        ).one()
    assert record.status == "approved"
    assert record.approved_by == "qa.engineer@example.com"
    assert record.approved_at is not None
    assert entry.actor == "qa.engineer@example.com"
    assert entry.after["status"] == "approved"


def test_approve_requires_a_named_approver(clean_db, session_factory):
    write_consolidated_rows(session_factory(), [make_row("ROW-1")], actor=TEST_ACTOR)
    with session_factory() as session:
        record_id = session.query(StandardLine).filter(StandardLine.line_key == _key("ROW-1")).one().id

    with pytest.raises(ValueError, match="approved_by"):
        approve_standard_line(session_factory(), record_id, approved_by="")


def test_approve_unknown_id_raises(clean_db, session_factory):
    with pytest.raises(ValueError, match="no standard_line row"):
        approve_standard_line(session_factory(), 999_999_999, approved_by="qa.engineer@example.com")


# --- write_from_review_workbook: full round trip from a reviewed xlsx ------

BASE_ROW_ID = TEST_ROW_PREFIX + "BASE-1"
MERGE_ROW_ID = TEST_ROW_PREFIX + "MERGE-1"
KEEP_ROW_ID = TEST_ROW_PREFIX + "KEEP-1"

SAMPLE_CLUSTERS = [
    {
        "operation": "OP10", "size": 3, "has_baseline": True,
        "rows": [
            {"row_id": BASE_ROW_ID, "source": "MASTER_REF", "failure_mode": "Damaged packaging",
             "failure_cause": "Bad storage", "failure_effect": "Customer claim"},
            {"row_id": MERGE_ROW_ID, "source": TEST_SOURCE, "failure_mode": "Torn packaging",
             "failure_cause": "Bad storage handling", "failure_effect": "Claim"},
            {"row_id": KEEP_ROW_ID, "source": TEST_SOURCE, "failure_mode": "New distinct risk",
             "failure_cause": "Different cause", "failure_effect": "Different effect"},
        ],
    },
]

SYNTHETIC_FINAL_LINE = {
    "row_id": BASE_ROW_ID, "source": "MASTER_REF", "process_item": "RM Reception",
    "process_step": "RM incoming inspection", "work_element": "SQA",
    "function_item": "fi", "function_step": "fs", "function_element": "fe",
    "failure_mode": "Damaged packaging", "failure_effect": "Customer claim",
    "failure_cause": "Bad storage", "prevention_control": "pc", "detection_control": "dc",
    "severity": 7, "occurrence": 3, "detection": 3, "ap": "L", "special_char": None,
    "consolidation_tag": "MASTER_REF_BASELINE_PRESERVED", "master_comparator": "BASELINE_MASTER_REFERENCE",
    "justification": "Preserved as baseline.",
    "source_row_refs": f"MASTER_REF:{BASE_ROW_ID}@01_ALL_SOURCES_GROUPED!R10",
    "selected_source_rows": f"MASTER_REF:{BASE_ROW_ID}@01_ALL_SOURCES_GROUPED!R10", "op_sequence_index": 1,
    "dedup_key": "OP10|packaging_material|damaged packaging", "operation": "OP10",
}

SYNTHETIC_RAW_LINES = [
    {"excel_row": 50, "row_id": MERGE_ROW_ID, "source": TEST_SOURCE, "process_step": "RM incoming inspection",
     "failure_mode": "Torn packaging", "failure_cause": "Bad storage handling", "failure_effect": "Claim",
     "severity": 6, "occurrence": 3, "detection": 3, "ap": "L"},
    {"excel_row": 60, "row_id": KEEP_ROW_ID, "source": TEST_SOURCE, "process_step": "RM incoming inspection",
     "failure_mode": "New distinct risk", "failure_cause": "Different cause", "failure_effect": "Different effect",
     "severity": 8, "occurrence": 2, "detection": 4, "ap": "M"},
]


def make_synthetic_ground_truth_dir(tmp_path):
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "final_lines.jsonl").write_text(json.dumps(SYNTHETIC_FINAL_LINE) + "\n", encoding="utf-8")
    with (gt_dir / "raw_lines.jsonl").open("w", encoding="utf-8") as fh:
        for r in SYNTHETIC_RAW_LINES:
            fh.write(json.dumps(r) + "\n")
    return gt_dir


def make_reviewed_workbook_path(tmp_path):
    wb = build_review_workbook(SAMPLE_CLUSTERS, [])
    ws = wb["Clusters for Review"]
    decisions = {MERGE_ROW_ID: "Merge", KEEP_ROW_ID: "Keep Separate"}
    for row in ws.iter_rows():
        if row[0].value in decisions:
            row[5].value = decisions[row[0].value]
    path = tmp_path / "reviewed.xlsx"
    wb.save(path)
    return path


def test_write_from_review_workbook_writes_kept_rows_as_drafts_and_skips_merged(clean_db, session_factory, tmp_path):
    review_path = make_reviewed_workbook_path(tmp_path)
    gt_dir = make_synthetic_ground_truth_dir(tmp_path)

    summary = write_from_review_workbook(
        session_factory(), review_path, actor=TEST_ACTOR, ground_truth_dir=gt_dir,
    )
    assert summary == {"inserted": 2, "unchanged": 0, "superseded": 0}  # baseline + keep-separate; merge excluded

    with session_factory() as session:
        rows = session.query(StandardLine).filter(StandardLine.line_key.like(f"%{TEST_ROW_PREFIX}%")).all()

    keys = {r.line_key for r in rows}
    assert keys == {f"MASTER_REF:{BASE_ROW_ID}", f"{TEST_SOURCE}:{KEEP_ROW_ID}"}
    assert all(r.status == "draft" for r in rows)
