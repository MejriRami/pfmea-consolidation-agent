"""
Tests for mcp_server/server.py. Each tool is called two ways where it
matters: directly as a plain function (FastMCP's @tool() decorator
returns the original function unchanged -- see mcp.server.fastmcp.FastMCP.tool's
source, `decorator` returns `fn` as-is) and through mcp.call_tool(), the
actual MCP registration/schema-validation path an OpenAI agent goes
through.

Offline-safe: cluster_pfmea_files's embedding call is monkeypatched (same
fake-vector approach as tests/test_clustering.py) so no OPENAI_API_KEY is
needed. search_standard_lines and the DB branch it exercises are gated
behind DATABASE_URL, same as the other DB tests in this repo.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import openpyxl
import pytest

import pfmea_agents.clustering as clustering_module
from mcp_server.server import (
    cluster_pfmea_files,
    count_by_operation,
    export_final_pfmea,
    generate_pfmea,
    get_pipeline_status,
    ingest_decisions,
    list_operations,
    list_product_families,
    mcp,
    search_standard_lines,
    suggest_pfmea_additions,
)

requires_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — suggest_pfmea_additions needs a real model call",
)

CAUSE_VECTORS = {
    "bad storage": [1.0, 0.0],
    "bad storage handling": [0.9, 0.43588989435406733],
}


def run(coro):
    return asyncio.run(coro)


async def fake_embed_texts_async(texts, *, model=None, client=None):
    return [CAUSE_VECTORS[t] for t in texts]


@pytest.fixture
def patched_embeddings(monkeypatch):
    monkeypatch.setattr(clustering_module, "embed_texts_async", fake_embed_texts_async)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# --- cluster_pfmea_files -----------------------------------------------------

CLUSTER_INPUT_ROWS = [
    {"row_id": "BASE-1", "source": "MASTER_REF", "process_step": "RM incoming inspection",
     "failure_mode": "Damaged packaging", "failure_cause": "bad storage", "failure_effect": "claim"},
    {"row_id": "CAND-1", "source": "SITE_B", "process_step": "RM incoming inspection",
     "failure_mode": "Torn packaging", "failure_cause": "bad storage handling", "failure_effect": "claim"},
]


def test_cluster_pfmea_files_direct_call(tmp_path, patched_embeddings):
    raw_lines_path = tmp_path / "raw_lines.jsonl"
    write_jsonl(raw_lines_path, CLUSTER_INPUT_ROWS)

    summary = run(cluster_pfmea_files(
        [str(raw_lines_path)], similarity_threshold=0.85,
        output_json_path=str(tmp_path / "clusters.json"),
        output_workbook_path=str(tmp_path / "review.xlsx"),
    ))

    assert summary["clusters"] == 1
    assert summary["threshold_used"] == 0.85
    workbook_path = Path(summary["workbook_path"])
    assert workbook_path.exists()
    wb = openpyxl.load_workbook(workbook_path)
    assert wb.sheetnames == ["Clusters for Review", "Unclustered Rows"]


def test_cluster_pfmea_files_reports_progress_via_ctx(tmp_path, patched_embeddings):
    """Confirms the tool's ctx.report_progress wiring actually fires --
    using a bare duck-typed fake instead of a real FastMCP Context, since
    calling the plain function directly (as opposed to through
    mcp.call_tool()) bypasses FastMCP's own Context construction/injection."""
    raw_lines_path = tmp_path / "raw_lines.jsonl"
    write_jsonl(raw_lines_path, CLUSTER_INPUT_ROWS)

    calls = []

    class FakeContext:
        async def report_progress(self, progress, total=None, message=None):
            calls.append((progress, total, message))

    run(cluster_pfmea_files(
        [str(raw_lines_path)], similarity_threshold=0.85,
        output_json_path=str(tmp_path / "clusters.json"),
        output_workbook_path=str(tmp_path / "review.xlsx"),
        ctx=FakeContext(),
    ))

    # run_path2_clustering's own 3 stage-boundary reports (reading, writing
    # workbook, done) plus cluster_raw_rows's 5 inner stages, rescaled --
    # see test_run_path2_clustering_reports_progress_including_file_io_steps
    # in test_clustering.py for the rescaling itself.
    assert len(calls) == 8
    assert calls[-1] == (6, 6, "done")


def test_cluster_pfmea_files_via_mcp_call_tool(tmp_path, patched_embeddings):
    """Exercises the real MCP registration/schema-validation path (not just
    the plain function) -- mcp.call_tool() returns a list of TextContent
    blocks, each carrying the tool's JSON-serialized return value."""
    raw_lines_path = tmp_path / "raw_lines.jsonl"
    write_jsonl(raw_lines_path, CLUSTER_INPUT_ROWS)

    result = run(mcp.call_tool("cluster_pfmea_files", {
        "files": [str(raw_lines_path)],
        "similarity_threshold": 0.85,
        "output_json_path": str(tmp_path / "clusters2.json"),
        "output_workbook_path": str(tmp_path / "review2.xlsx"),
    }))
    payload = json.loads(result[0].text)
    assert payload["clusters"] == 1
    assert payload["threshold_used"] == 0.85
    assert Path(payload["workbook_path"]).exists()


def test_cluster_pfmea_files_requires_threshold_without_ground_truth(tmp_path, patched_embeddings):
    raw_lines_path = tmp_path / "raw_lines.jsonl"
    write_jsonl(raw_lines_path, [CLUSTER_INPUT_ROWS[0]])

    with pytest.raises(ValueError, match="ground_truth_decisions"):
        run(cluster_pfmea_files(
            [str(raw_lines_path)],
            output_json_path=str(tmp_path / "clusters.json"),
            output_workbook_path=str(tmp_path / "review.xlsx"),
        ))


# --- ingest_decisions ---------------------------------------------------------

def test_ingest_decisions_direct_call(tmp_path):
    from exports.export_phase4_review_workbook import build_workbook

    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    baseline = {
        "row_id": "00.0.1", "source": "MASTER_REF", "process_item": "RM Reception",
        "process_step": "RM incoming inspection", "work_element": "SQA",
        "function_item": "fi", "function_step": "fs", "function_element": "fe",
        "failure_mode": "Damaged packaging", "failure_effect": "Customer claim",
        "failure_cause": "Bad storage", "prevention_control": "pc", "detection_control": "dc",
        "severity": 7, "occurrence": 3, "detection": 3, "ap": "L", "special_char": None,
        "consolidation_tag": "MASTER_REF_BASELINE_PRESERVED", "master_comparator": "BASELINE_MASTER_REFERENCE",
        "justification": "Preserved as baseline.",
        "source_row_refs": "MASTER_REF:00.0.1@01_ALL_SOURCES_GROUPED!R10",
        "selected_source_rows": "MASTER_REF:00.0.1@01_ALL_SOURCES_GROUPED!R10", "op_sequence_index": 1,
        "dedup_key": "OP10|packaging_material|damaged packaging",
    }
    (gt_dir / "final_lines.jsonl").write_text(json.dumps(baseline) + "\n", encoding="utf-8")
    (gt_dir / "raw_lines.jsonl").write_text(json.dumps({
        "excel_row": 50, "row_id": "CAND-1", "source": "SITE_B", "process_step": "RM incoming inspection",
        "failure_mode": "Torn packaging", "failure_cause": "Bad storage handling", "failure_effect": "claim",
        "severity": 6, "occurrence": 3, "detection": 3, "ap": "L",
    }) + "\n", encoding="utf-8")

    clusters = [{
        "operation": "OP10", "size": 2, "has_baseline": True,
        "rows": [
            {"row_id": "00.0.1", "source": "MASTER_REF", "failure_mode": "Damaged packaging",
             "failure_cause": "Bad storage", "failure_effect": "Customer claim"},
            {"row_id": "CAND-1", "source": "SITE_B", "failure_mode": "Torn packaging",
             "failure_cause": "Bad storage handling", "failure_effect": "claim"},
        ],
    }]
    wb = build_workbook(clusters, [])
    ws = wb["Clusters for Review"]
    for row in ws.iter_rows():
        if row[0].value == "CAND-1":
            row[5].value = "Keep Separate"
    review_path = tmp_path / "reviewed.xlsx"
    wb.save(review_path)

    out_path = tmp_path / "final.xlsx"
    summary = ingest_decisions(str(review_path), str(out_path), str(gt_dir))

    assert summary["output_path"] == str(out_path)
    assert summary["baseline_rows"] == 1
    assert summary["kept_separate"] == 1
    assert summary["total_rows"] == 2
    assert out_path.exists()


# --- export_final_pfmea -------------------------------------------------------

def test_export_final_pfmea_direct_call(tmp_path):
    final_line = {
        "row_id": "00.0.1", "source": "MASTER_REF", "process_step": "RM incoming inspection",
        "process_item": "RM Reception", "work_element": None, "function_item": None, "function_step": None,
        "function_element": None, "failure_mode": "Damaged packaging", "failure_effect": "Customer claim",
        "failure_cause": "Bad storage", "prevention_control": None, "detection_control": None,
        "severity": 7, "occurrence": 3, "detection": 3, "ap": "L", "special_char": None,
        "source_row_refs": "MASTER_REF:00.0.1@X!R10", "op_sequence_index": 1,
        "consolidation_tag": "MASTER_REF_BASELINE_PRESERVED", "master_comparator": "BASELINE_MASTER_REFERENCE",
        "selected_source_rows": "MASTER_REF:00.0.1@X!R10", "justification": "baseline", "dedup_key": "k",
    }
    input_path = tmp_path / "final_lines.jsonl"
    input_path.write_text(json.dumps(final_line) + "\n", encoding="utf-8")
    output_path = tmp_path / "out.xlsx"

    result = export_final_pfmea(str(input_path), str(output_path))

    assert result == {"output_path": str(output_path), "rows_exported": 1}
    assert output_path.exists()
    wb = openpyxl.load_workbook(output_path)
    assert wb.sheetnames == ["02_FINAL_PFMEA"]


def test_export_final_pfmea_via_mcp_call_tool(tmp_path):
    final_line = {"row_id": "00.0.1", "source": "MASTER_REF", "process_step": "x",
                  "failure_mode": "m", "failure_effect": "e", "failure_cause": "c",
                  "severity": 1, "occurrence": 1, "detection": 1, "ap": "L"}
    input_path = tmp_path / "final_lines.jsonl"
    input_path.write_text(json.dumps(final_line) + "\n", encoding="utf-8")
    output_path = tmp_path / "out.xlsx"

    result = run(mcp.call_tool("export_final_pfmea", {"input_path": str(input_path), "output_path": str(output_path)}))
    payload = json.loads(result[0].text)
    assert payload == {"output_path": str(output_path), "rows_exported": 1}
    assert output_path.exists()


# --- get_pipeline_status -------------------------------------------------------

def test_get_pipeline_status_direct_call_reports_a_stage():
    status = get_pipeline_status()
    assert "stage" in status
    assert isinstance(status["raw_lines_available"], bool)


def test_get_pipeline_status_via_mcp_call_tool():
    result = run(mcp.call_tool("get_pipeline_status", {}))
    payload = json.loads(result[0].text)
    assert "stage" in payload


# --- search_standard_lines -----------------------------------------------------

def test_search_standard_lines_direct_call_raises_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from db.connection import DatabaseNotConfigured
    with pytest.raises(DatabaseNotConfigured):
        search_standard_lines("packaging")


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_search_standard_lines_direct_call_with_real_database():
    results = search_standard_lines("", limit=1)
    assert isinstance(results, list)


# --- count_by_operation -----------------------------------------------------

def test_count_by_operation_direct_call_raises_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from db.connection import DatabaseNotConfigured
    with pytest.raises(DatabaseNotConfigured):
        count_by_operation()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_count_by_operation_direct_call_returns_counts_only_no_row_content():
    counts = count_by_operation(product_family="chokes")
    assert isinstance(counts, dict)
    assert counts, "expected at least one operation with rows in the live table"
    assert all(isinstance(op, str) and isinstance(n, int) for op, n in counts.items())
    # the whole point: a dict of small ints, never full row payloads
    assert sum(counts.values()) < 100_000  # sanity: these are counts, not serialized rows


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_count_by_operation_via_mcp_call_tool():
    """count_by_operation's return type (dict[str, int]) is specific
    enough that FastMCP auto-detects it as a structured tool, so
    call_tool() returns (content_blocks, structured_dict) instead of the
    plain content-blocks list the other, generically-`dict`-typed tools
    return -- use the structured dict directly rather than re-parsing the
    text block."""
    content_blocks, structured = run(mcp.call_tool("count_by_operation", {"product_family": "chokes"}))
    assert isinstance(structured, dict)
    assert structured  # non-empty against the real, populated table
    assert json.loads(content_blocks[0].text) == structured


# --- list_product_families ----------------------------------------------------

def test_list_product_families_direct_call_raises_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from db.connection import DatabaseNotConfigured
    with pytest.raises(DatabaseNotConfigured):
        list_product_families()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_list_product_families_direct_call_includes_chokes():
    families = list_product_families()
    assert isinstance(families, list)
    assert "chokes" in families


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_list_product_families_via_mcp_call_tool():
    # list[str] is specific enough for FastMCP to treat this as a
    # structured tool -- call_tool() returns (content_blocks, structured),
    # with structured wrapped as {"result": [...]} for a bare-list return.
    # (content_blocks is one text block PER LIST ITEM here, not one block
    # holding the whole JSON array -- structured is the reliable one to check.)
    _content_blocks, structured = run(mcp.call_tool("list_product_families", {}))
    assert "chokes" in structured["result"]


# --- list_operations -----------------------------------------------------------

def test_list_operations_direct_call_raises_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from db.connection import DatabaseNotConfigured
    with pytest.raises(DatabaseNotConfigured):
        list_operations("chokes")


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_list_operations_direct_call_returns_operation_and_count_in_process_order():
    ops = list_operations("chokes")
    assert isinstance(ops, list)
    assert ops, "expected at least one operation with approved chokes content"
    for entry in ops:
        assert set(entry.keys()) == {"operation", "approved_count"}
        assert isinstance(entry["operation"], str)
        assert isinstance(entry["approved_count"], int)
        assert entry["approved_count"] > 0
    codes = [e["operation"] for e in ops]
    # process order, not count order -- OP10 before OP40 before OP50 etc.
    assert codes == sorted(codes, key=lambda c: int(c[2:]))


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_list_operations_direct_call_empty_for_unknown_family():
    assert list_operations("no_such_product_family") == []


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_list_operations_via_mcp_call_tool():
    _content_blocks, structured = run(mcp.call_tool("list_operations", {"product_family": "chokes"}))
    payload = structured["result"]
    assert isinstance(payload, list)
    assert payload
    assert {"operation", "approved_count"} <= set(payload[0].keys())


# --- generate_pfmea --------------------------------------------------------------

def test_generate_pfmea_direct_call_raises_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from db.connection import DatabaseNotConfigured
    with pytest.raises(DatabaseNotConfigured):
        generate_pfmea("chokes", ["OP10"])


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_generate_pfmea_direct_call_assembles_only_approved_validated_rows(tmp_path):
    result = generate_pfmea("chokes", ["OP10"], output_path=str(tmp_path / "out.xlsx"))
    assert result["status"] == "Draft"
    assert result["summary"]["product_family"] == "chokes"
    assert result["summary"]["operations_included"] == ["OP10"]
    assert result["summary"]["total_line_items"] == len(result["rows"])
    assert result["summary"]["total_line_items"] > 0
    for row in result["rows"]:
        assert row["content_type"] == "VALIDATED"
        assert row["status"] == "approved"
        assert row["operation"] == "OP10"


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_generate_pfmea_direct_call_writes_the_actual_workbook_no_rpn(tmp_path):
    import openpyxl

    from exports.export_generated_pfmea import SOURCE_STATUS_COLUMN, SOURCE_STATUS_HEADER

    out_path = tmp_path / "Generated_PFMEA_chokes.xlsx"
    result = generate_pfmea("chokes", ["OP10"], output_path=str(out_path))
    assert result["output_path"] == str(out_path)
    assert out_path.exists()

    wb = openpyxl.load_workbook(out_path)
    ws = wb["02_FINAL_PFMEA"]
    assert ws[f"{SOURCE_STATUS_COLUMN}9"].value == SOURCE_STATUS_HEADER
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                assert "RPN" not in cell.value.upper()


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_generate_pfmea_direct_call_reports_operation_with_no_content_transparently(tmp_path):
    import openpyxl

    from exports.export_generated_pfmea import SOURCE_STATUS_COLUMN, SOURCE_STATUS_GAP

    out_path = tmp_path / "out.xlsx"
    result = generate_pfmea("chokes", ["OP10", "OP_DOES_NOT_EXIST"], output_path=str(out_path))
    assert "OP_DOES_NOT_EXIST" in result["summary"]["operations_with_no_content"]
    assert "OP_DOES_NOT_EXIST" not in result["summary"]["operations_included"]
    assert all(r["operation"] != "OP_DOES_NOT_EXIST" for r in result["rows"])

    wb = openpyxl.load_workbook(out_path)
    ws = wb["02_FINAL_PFMEA"]
    gap_statuses = [
        ws[f"{SOURCE_STATUS_COLUMN}{r}"].value
        for r in range(10, ws.max_row + 1)
    ]
    assert SOURCE_STATUS_GAP in gap_statuses


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_generate_pfmea_via_mcp_call_tool(tmp_path):
    out_path = tmp_path / "out.xlsx"
    result = run(mcp.call_tool(
        "generate_pfmea",
        {"product_family": "chokes", "selected_operations": ["OP10"], "output_path": str(out_path)},
    ))
    payload = json.loads(result[0].text)
    assert payload["status"] == "Draft"
    assert payload["summary"]["operations_included"] == ["OP10"]
    assert all(r["content_type"] == "VALIDATED" for r in payload["rows"])
    assert payload["output_path"] == str(out_path)
    assert out_path.exists()


# --- suggest_pfmea_additions (Layer 2: AI suggestions) --------------------------
# Gated behind BOTH DATABASE_URL (to fetch real existing validated rows for
# context) and OPENAI_API_KEY (the suggestion call itself is a real model call).

def test_suggest_pfmea_additions_direct_call_raises_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from db.connection import DatabaseNotConfigured
    with pytest.raises(DatabaseNotConfigured):
        run(suggest_pfmea_additions("chokes", "OP10"))


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
@requires_openai
def test_suggest_pfmea_additions_direct_call_returns_ai_suggestions_with_no_provenance():
    suggestions = run(suggest_pfmea_additions("chokes", "OP10"))
    assert isinstance(suggestions, list)
    for s in suggestions:
        assert s["content_type"] == "AI_SUGGESTION"
        assert s["operation"] == "OP10"
        assert s["product_family"] == "chokes"
        assert s["rationale"].strip()
        for forbidden_key in ("id", "line_key", "version", "status", "approved_by", "approved_at"):
            assert forbidden_key not in s


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
@requires_openai
def test_suggest_pfmea_additions_never_writes_to_standard_line():
    from db.connection import get_session_factory
    from db.models import StandardLine

    session_factory = get_session_factory()
    with session_factory() as session:
        count_before = session.query(StandardLine).count()

    run(suggest_pfmea_additions("chokes", "OP10"))

    with session_factory() as session:
        count_after = session.query(StandardLine).count()
    assert count_after == count_before


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
@requires_openai
def test_suggest_pfmea_additions_via_mcp_call_tool():
    # list[dict] is specific enough for FastMCP to treat this as a
    # structured tool -- call_tool() returns (content_blocks, structured),
    # with structured wrapped as {"result": [...]} (and content_blocks
    # holding one text block PER LIST ITEM, not one block with the whole
    # array) -- same shape as list_operations's tool, see its test.
    _content_blocks, structured = run(
        mcp.call_tool("suggest_pfmea_additions", {"product_family": "chokes", "operation": "OP10"})
    )
    payload = structured["result"]
    assert isinstance(payload, list)
    assert all(r["content_type"] == "AI_SUGGESTION" for r in payload)
