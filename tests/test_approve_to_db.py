"""
Tests for approve_to_db.py. --dry-run is fully offline (no DATABASE_URL
needed -- it never imports the db package). The real write+approve path
is gated behind DATABASE_URL, same pattern as the other DB tests in this
repo, and cleans up only the rows it creates (row_id under
TEST_ROW_PREFIX).
"""
from __future__ import annotations

import json
import os

import pytest

from approve_to_db import main as approve_to_db_main
from exports.export_phase4_review_workbook import build_workbook as build_review_workbook

TEST_ROW_PREFIX = "ZZTEST_ATD_"
TEST_SOURCE = "APPROVE_TO_DB_TEST_SRC"

BASE_ROW_ID = TEST_ROW_PREFIX + "BASE-1"
MERGE_ROW_ID = TEST_ROW_PREFIX + "MERGE-1"
KEEP_ROW_ID = TEST_ROW_PREFIX + "KEEP-1"
UNSURE_ROW_ID = TEST_ROW_PREFIX + "UNSURE-1"

SAMPLE_CLUSTERS = [
    {
        "operation": "OP10", "size": 4, "has_baseline": True,
        "rows": [
            {"row_id": BASE_ROW_ID, "source": "MASTER_REF", "failure_mode": "Damaged packaging",
             "failure_cause": "Bad storage", "failure_effect": "Customer claim"},
            {"row_id": MERGE_ROW_ID, "source": TEST_SOURCE, "failure_mode": "Torn packaging",
             "failure_cause": "Bad storage handling", "failure_effect": "Claim"},
            {"row_id": KEEP_ROW_ID, "source": TEST_SOURCE, "failure_mode": "New distinct risk",
             "failure_cause": "Different cause", "failure_effect": "Different effect"},
            {"row_id": UNSURE_ROW_ID, "source": TEST_SOURCE, "failure_mode": "Ambiguous case",
             "failure_cause": "Unclear cause", "failure_effect": "Unclear effect"},
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
    "dedup_key": "OP10|packaging_material|damaged packaging",
}

SYNTHETIC_RAW_LINES = [
    {"excel_row": 50, "row_id": MERGE_ROW_ID, "source": TEST_SOURCE, "process_step": "RM incoming inspection",
     "failure_mode": "Torn packaging", "failure_cause": "Bad storage handling", "failure_effect": "Claim",
     "severity": 6, "occurrence": 3, "detection": 3, "ap": "L"},
    {"excel_row": 60, "row_id": KEEP_ROW_ID, "source": TEST_SOURCE, "process_step": "RM incoming inspection",
     "failure_mode": "New distinct risk", "failure_cause": "Different cause", "failure_effect": "Different effect",
     "severity": 8, "occurrence": 2, "detection": 4, "ap": "M"},
    {"excel_row": 70, "row_id": UNSURE_ROW_ID, "source": TEST_SOURCE, "process_step": "RM incoming inspection",
     "failure_mode": "Ambiguous case", "failure_cause": "Unclear cause", "failure_effect": "Unclear effect",
     "severity": 5, "occurrence": 4, "detection": 5, "ap": "L"},
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
    decisions = {MERGE_ROW_ID: "Merge", KEEP_ROW_ID: "Keep Separate", UNSURE_ROW_ID: "Unsure"}
    for row in ws.iter_rows():
        if row[0].value in decisions:
            row[5].value = decisions[row[0].value]
    path = tmp_path / "reviewed.xlsx"
    wb.save(path)
    return path


# --- --dry-run: fully offline, no DATABASE_URL needed ------------------------

def test_dry_run_prints_counts_and_touches_no_database(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    review_path = make_reviewed_workbook_path(tmp_path)
    gt_dir = make_synthetic_ground_truth_dir(tmp_path)

    exit_code = approve_to_db_main([
        "--approved-by", "Test Reviewer",
        "--workbook", str(review_path),
        "--ground-truth-dir", str(gt_dir),
        "--dry-run",
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "baseline rows:      1" in out
    assert "kept separate:      1" in out
    assert "flagged uncertain:  1" in out
    assert "merged (excluded):  1" in out
    assert "total consolidated rows to write: 3" in out
    assert "[DRY RUN]" in out
    assert "Would write 3 row(s)" in out
    assert "Test Reviewer" in out
    assert "Wrote to standard_line" not in out
    assert "Approved" not in out.split("[DRY RUN]")[0]


def test_dry_run_requires_approved_by_argument(tmp_path):
    review_path = make_reviewed_workbook_path(tmp_path)
    gt_dir = make_synthetic_ground_truth_dir(tmp_path)

    with pytest.raises(SystemExit):
        approve_to_db_main(["--workbook", str(review_path), "--ground-truth-dir", str(gt_dir), "--dry-run"])


# --- real write + approve: gated behind DATABASE_URL -------------------------

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — approve_to_db live-write tests need a real Postgres connection",
)

if os.environ.get("DATABASE_URL"):
    from db.connection import get_engine, get_session_factory
    from db.models import AuditLog, Base, StandardLine


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
                session.delete(r)
                session.flush()
            if ids:
                session.query(AuditLog).filter(
                    AuditLog.table_name == "standard_line", AuditLog.record_id.in_(ids)
                ).delete(synchronize_session=False)
            session.commit()

    _cleanup()
    yield
    _cleanup()


@pytestmark_db
def test_real_run_writes_drafts_and_approves_them(tmp_path, capsys, clean_db, session_factory):
    review_path = make_reviewed_workbook_path(tmp_path)
    gt_dir = make_synthetic_ground_truth_dir(tmp_path)

    exit_code = approve_to_db_main([
        "--approved-by", "Test Reviewer",
        "--workbook", str(review_path),
        "--ground-truth-dir", str(gt_dir),
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Wrote to standard_line: 3 new version(s)" in out
    assert "Approved 3 row(s) as 'Test Reviewer'" in out

    with session_factory() as session:
        rows = session.query(StandardLine).filter(StandardLine.line_key.like(f"%{TEST_ROW_PREFIX}%")).all()

    assert len(rows) == 3
    assert all(r.status == "approved" for r in rows)
    assert all(r.approved_by == "Test Reviewer" for r in rows)
    keys = {r.line_key for r in rows}
    assert keys == {f"MASTER_REF:{BASE_ROW_ID}", f"{TEST_SOURCE}:{KEEP_ROW_ID}", f"{TEST_SOURCE}:{UNSURE_ROW_ID}"}
    assert not any(k.endswith(MERGE_ROW_ID) for k in keys)  # Merge -> excluded, never reaches standard_line


@pytestmark_db
def test_rerunning_after_approval_reports_already_approved(tmp_path, capsys, clean_db, session_factory):
    review_path = make_reviewed_workbook_path(tmp_path)
    gt_dir = make_synthetic_ground_truth_dir(tmp_path)
    args = [
        "--approved-by", "Test Reviewer",
        "--workbook", str(review_path),
        "--ground-truth-dir", str(gt_dir),
    ]

    approve_to_db_main(args)
    capsys.readouterr()  # discard first run's output
    exit_code = approve_to_db_main(args)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Wrote to standard_line: 0 new version(s), 3 unchanged" in out
    assert "Approved 0 row(s)" in out
    assert "3 were already approved" in out
