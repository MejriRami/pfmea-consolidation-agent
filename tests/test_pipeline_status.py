"""Offline tests for pfmea_agents/pipeline_status.py -- pure filesystem
inspection, no API key or database needed (check_database=False, or
DATABASE_URL simply unset, both skip the DB branch)."""
from __future__ import annotations

import json

from pfmea_agents.pipeline_status import get_pipeline_status


def status_for(tmp_path, *, raw_lines=False, clusters=False, workbook_decisions=None, final_export=False):
    raw_lines_path = tmp_path / "raw_lines.jsonl"
    clusters_json_path = tmp_path / "clusters.json"
    review_workbook_path = tmp_path / "review.xlsx"
    final_export_path = tmp_path / "final.xlsx"

    if raw_lines:
        raw_lines_path.write_text("{}\n", encoding="utf-8")
    if clusters:
        clusters_json_path.write_text("[]", encoding="utf-8")
    if workbook_decisions is not None:
        from exports.export_phase4_review_workbook import build_workbook
        clusters = [{
            "operation": "OP10", "size": 2, "has_baseline": True,
            "rows": [
                {"row_id": "00.0.1", "source": "MASTER_REF", "failure_mode": "m",
                 "failure_cause": "c", "failure_effect": "e"},
                {"row_id": "CAND-1", "source": "SITE_B", "failure_mode": "m2",
                 "failure_cause": "c2", "failure_effect": "e2"},
            ],
        }]
        wb = build_workbook(clusters, [])
        if workbook_decisions:
            ws = wb["Clusters for Review"]
            for row in ws.iter_rows():
                if row[0].value == "CAND-1":
                    row[5].value = "Keep Separate"
        wb.save(review_workbook_path)
    if final_export:
        final_export_path.write_text("x", encoding="utf-8")

    return get_pipeline_status(
        raw_lines_path=raw_lines_path, clusters_json_path=clusters_json_path,
        review_workbook_path=review_workbook_path, final_export_path=final_export_path,
        check_database=False,
    )


def test_no_data_when_nothing_exists(tmp_path):
    status = status_for(tmp_path)
    assert status["stage"] == "NO_DATA"
    assert status["raw_lines_available"] is False
    assert status["review_workbook_has_decisions"] is None
    assert "standard_line" not in status


def test_raw_lines_ready_not_clustered(tmp_path):
    status = status_for(tmp_path, raw_lines=True)
    assert status["stage"] == "RAW_LINES_READY_NOT_CLUSTERED"


def test_clusters_generated_no_workbook(tmp_path):
    status = status_for(tmp_path, raw_lines=True, clusters=True)
    assert status["stage"] == "CLUSTERS_GENERATED_NO_WORKBOOK"


def test_awaiting_human_review_when_workbook_has_no_decisions(tmp_path):
    status = status_for(tmp_path, raw_lines=True, clusters=True, workbook_decisions=False)
    assert status["stage"] == "AWAITING_HUMAN_REVIEW"
    assert status["review_workbook_has_decisions"] is False


def test_review_complete_ready_to_ingest_when_workbook_has_decisions(tmp_path):
    status = status_for(tmp_path, raw_lines=True, clusters=True, workbook_decisions=True)
    assert status["stage"] == "REVIEW_COMPLETE_READY_TO_INGEST"
    assert status["review_workbook_has_decisions"] is True


def test_phase7_export_complete_takes_priority_over_everything_else(tmp_path):
    status = status_for(tmp_path, raw_lines=True, clusters=True, workbook_decisions=True, final_export=True)
    assert status["stage"] == "PHASE7_EXPORT_COMPLETE"
    assert status["final_export_available"] is True
