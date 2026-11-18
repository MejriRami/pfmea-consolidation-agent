"""
Read-only pipeline-stage inspector. Not a new pipeline stage of its own --
just checks for the artifacts each existing stage already produces (Path 2
clustering's clusters JSON / review workbook, Phase 7's final export), plus
standard_line row counts when DATABASE_URL is set, so an operator (or the
get_pipeline_status MCP tool) can see progress without grepping the
filesystem or the database by hand.

Whether the review workbook has actually been reviewed is checked by
reusing pfmea_agents.decision_ingestion.read_review_workbook (existing,
unmodified) rather than re-parsing the xlsx here.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH_DIR = REPO_ROOT / "ground_truth"
EXPORTS_DIR = REPO_ROOT / "exports"

DEFAULT_RAW_LINES_PATH = GROUND_TRUTH_DIR / "raw_lines.jsonl"
DEFAULT_CLUSTERS_JSON_PATH = EXPORTS_DIR / "phase4_clusters_for_review.json"
DEFAULT_REVIEW_WORKBOOK_PATH = EXPORTS_DIR / "Phase4_Cluster_Review.xlsx"
DEFAULT_FINAL_EXPORT_PATH = EXPORTS_DIR / "02_FINAL_PFMEA_export.xlsx"


def _review_workbook_has_any_decision(path: Path) -> bool:
    from pfmea_agents.decision_ingestion import read_review_workbook
    reviewed = read_review_workbook(Path(path))
    return any((e.decision or "").strip() for e in reviewed.cluster_rows)


def _standard_line_counts() -> dict:
    from sqlalchemy import func

    from db.connection import get_session_factory
    from db.models import StandardLine

    session_factory = get_session_factory()
    with session_factory() as session:
        total = session.query(StandardLine).count()
        by_status = dict(session.query(StandardLine.status, func.count()).group_by(StandardLine.status).all())
    return {"total_rows": total, "by_status": by_status}


def get_pipeline_status(
    *,
    raw_lines_path: Path = DEFAULT_RAW_LINES_PATH,
    clusters_json_path: Path = DEFAULT_CLUSTERS_JSON_PATH,
    review_workbook_path: Path = DEFAULT_REVIEW_WORKBOOK_PATH,
    final_export_path: Path = DEFAULT_FINAL_EXPORT_PATH,
    check_database: bool = True,
) -> dict:
    raw_lines_path = Path(raw_lines_path)
    clusters_json_path = Path(clusters_json_path)
    review_workbook_path = Path(review_workbook_path)
    final_export_path = Path(final_export_path)

    raw_lines_ready = raw_lines_path.exists()
    clusters_ready = clusters_json_path.exists()
    review_workbook_ready = review_workbook_path.exists()
    final_export_ready = final_export_path.exists()

    review_completed = _review_workbook_has_any_decision(review_workbook_path) if review_workbook_ready else None

    if final_export_ready:
        stage = "PHASE7_EXPORT_COMPLETE"
    elif review_completed:
        stage = "REVIEW_COMPLETE_READY_TO_INGEST"
    elif review_workbook_ready:
        stage = "AWAITING_HUMAN_REVIEW"
    elif clusters_ready:
        stage = "CLUSTERS_GENERATED_NO_WORKBOOK"
    elif raw_lines_ready:
        stage = "RAW_LINES_READY_NOT_CLUSTERED"
    else:
        stage = "NO_DATA"

    status = {
        "stage": stage,
        "raw_lines_available": raw_lines_ready,
        "clusters_json_available": clusters_ready,
        "review_workbook_available": review_workbook_ready,
        "review_workbook_has_decisions": review_completed,
        "final_export_available": final_export_ready,
    }

    if check_database and os.environ.get("DATABASE_URL"):
        status["standard_line"] = _standard_line_counts()

    return status
