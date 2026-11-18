"""
MCP server exposing this repo's already-built PFMEA consolidation engine
as tools, so an OpenAI agent (or any MCP client) can drive it. Every tool
below is a thin wrapper: it validates/shapes arguments, calls one existing
function, and returns its result. None of them re-implement business
logic that already lives elsewhere, and none of them make an API call the
wrapped function wasn't already going to make.

| Tool                     | Wraps                                                          |
|--------------------------|-----------------------------------------------------------------|
| cluster_pfmea_files      | pfmea_agents.clustering.run_path2_clustering                    |
| ingest_decisions         | pfmea_agents.decision_ingestion.ingest_and_export                |
| export_final_pfmea       | exports.export_final_pfmea.export                                |
| get_pipeline_status      | pfmea_agents.pipeline_status.get_pipeline_status                 |
| search_standard_lines    | db.standard_line_query.search_standard_lines                    |
| count_by_operation       | db.standard_line_query.count_by_operation                        |
| list_product_families    | pfmea_agents.generation.list_product_families                    |
| list_operations          | pfmea_agents.generation.list_operations                          |
| generate_pfmea           | pfmea_agents.generation.generate_pfmea (Layer 1) + exports.export_generated_pfmea (writes the .xlsx) |
| suggest_pfmea_additions  | pfmea_agents.generation.suggest_additions (Layer 2: AI suggestions, one operation) |
| draft_pfmea_from_ai_suggestions | pfmea_agents.generation.draft_pfmea_from_ai_suggestions (Layer 2 batch) + exports.export_generated_pfmea.export_ai_draft |

GENERATION (list_product_families / list_operations / generate_pfmea /
suggest_pfmea_additions / draft_pfmea_from_ai_suggestions) is a separate
capability from the consolidation tools above it: it ASSEMBLES a NEW
PFMEA from the already-approved standard_line knowledge base, rather than
reading/consolidating existing source files, in two clearly separated
layers. Layer 1 (generate_pfmea) never invents content -- it only pulls
status='approved' rows, each marked content_type='VALIDATED', and WRITES
the actual deliverable .xlsx itself (exports.export_generated_pfmea, the
same AIAG-VDA 02_FINAL_PFMEA template export_final_pfmea uses -- AP only,
never RPN) so the caller never has to reformat the row JSON into a
workbook by hand. Layer 2 (suggest_pfmea_additions) proposes content NOT
in the knowledge base from the model's own PFMEA engineering knowledge,
marked content_type='AI_SUGGESTION', carrying no provenance and never
written to standard_line under any circumstance. draft_pfmea_from_ai_suggestions
is Layer 2 run across a whole operation sequence at once and written to
its own .xlsx (same template, an unmissable "unvalidated AI draft" banner,
no S/O/D/AP ever populated) -- for a product_family with no validated
content at all, so a whole-document AI draft has somewhere to go besides
the agent improvising a table in chat. See pfmea_agents/generation.py's
docstring for the full contract.

NAMED `mcp_server`, NOT `mcp`: a top-level `mcp/` package directory would
sit earlier on sys.path than the installed `mcp` SDK package and shadow
it -- the exact collision this repo already hit once (`agents` vs. the
OpenAI Agents SDK's own `agents` package, fixed by renaming to
`pfmea_agents`). Same fix, applied before it could bite here too.

APPROVAL STAYS HUMAN-ONLY: no tool here calls
db.standard_line_writer.approve_standard_line, and none ever will --
that function requires a named human `approved_by` and is intentionally
not exposed to the agent (spec §7: "No MCP tool may write to
standard_line with status='approved'. Approval is human-only, via the
app.").

TRANSPORTS: stdio (default -- what run_agent.py's MCPServerStdio talks to)
and streamable-http (for exposing this server over the network, e.g.
tunneled through ngrok for a demo). Select with `--transport http` /
`MCP_TRANSPORT=http`, or leave it on stdio.

HTTP MODE IS TOKEN-GATED: every HTTP request must carry
`Authorization: Bearer <MCP_HTTP_TOKEN>`, checked by TokenAuthMiddleware
before it ever reaches the MCP app. Starting HTTP mode without
MCP_HTTP_TOKEN set is a hard error -- this transport is the one meant to
be reachable from the public internet, so it never starts unauthenticated.
See mcp_server/README.md for the ngrok setup.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")  # populate os.environ (DATABASE_URL, OPENAI_API_KEY, MCP_HTTP_TOKEN) before anything runs

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from exports.export_final_pfmea import export as _export_final_pfmea
from pfmea_agents.clustering import run_path2_clustering
from pfmea_agents.decision_ingestion import ingest_and_export
from pfmea_agents.pipeline_status import get_pipeline_status as _get_pipeline_status

EXPORTS_DIR = REPO_ROOT / "exports"

mcp = FastMCP(
    "pfmea-consolidation",
    # FastMCP's default DNS-rebinding protection only allows Host headers
    # matching 127.0.0.1/localhost/[::1] -- which silently 421s every real
    # request through ngrok (a random *.ngrok-free.app hostname) or behind
    # any reverse proxy (Azure App Service, etc.), since neither can be
    # hardcoded into an allowlist ahead of time. TokenAuthMiddleware is this
    # server's real access control (a secret the Host header can't forge);
    # the Host check adds little on top of that for our threat model, so
    # it's disabled rather than trying to guess every deployment's hostname.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    # Stateless HTTP: the default streamable-http mode keeps each MCP
    # session's state in-process memory, keyed by a session ID handed out
    # on `initialize`. That breaks the moment a deployment has more than
    # one worker process or instance (Azure App Service, any autoscaled
    # host, gunicorn -w N): the session created on worker A doesn't exist
    # on worker B, so the very next request 404s with "Session not found"
    # even though the token was correct. Stateless mode makes every
    # request self-contained instead, so it works the same whether this
    # server is one local process or N instances behind a load balancer.
    stateless_http=True,
    instructions=(
        "Tools for consolidating the company chokes PFMEA source files into one standard PFMEA. "
        "Typical flow: cluster_pfmea_files -> a human fills in the generated review workbook's "
        "Engineer Decision column -> ingest_decisions -> export_final_pfmea. "
        "get_pipeline_status reports where things stand; search_standard_lines queries already-"
        "written standard content (full row detail); count_by_operation answers aggregate/"
        "counting questions (row counts per operation) without loading full rows. "
        "list_product_families / list_operations / generate_pfmea / suggest_pfmea_additions / "
        "draft_pfmea_from_ai_suggestions are a separate GENERATION capability: assembling a NEW "
        "PFMEA from already-approved standard_line content, rather than reading/consolidating "
        "source files. generate_pfmea (Layer 1) only pulls status='approved' rows, marks every one "
        "content_type='VALIDATED', and writes the actual deliverable .xlsx itself (same AIAG-VDA "
        "template as export_final_pfmea -- AP only, never RPN) -- present that file, never build "
        "your own workbook from the returned row JSON. suggest_pfmea_additions (Layer 2, one "
        "operation) proposes content NOT in the knowledge base, marked content_type='AI_SUGGESTION', "
        "carrying no provenance and never written anywhere -- always present suggestions separately "
        "from validated content, never as if they were approved. draft_pfmea_from_ai_suggestions "
        "(Layer 2, a whole operation sequence at once) is for a product with NO validated content at "
        "all -- it also writes an actual .xlsx (same template, no S/O/D/AP populated, an unmissable "
        "banner marking the whole document an unvalidated AI draft) -- use it only when "
        "list_operations reports nothing approved, and present that file the same way, never a "
        "hand-built table. "
        "No tool here can approve a standard_line row -- that is a human-only action outside "
        "this server."
    ),
)


@mcp.tool()
async def cluster_pfmea_files(
    files: list[str] | None = None,
    similarity_threshold: float | None = None,
    output_json_path: str | None = None,
    output_workbook_path: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Run the Phase 4 Path 2 clustering engine over one or more
    raw_lines-shaped JSONL files (defaults to ground_truth/raw_lines.jsonl
    if `files` is omitted) and produce the human review workbook.

    This can take several minutes on a full dataset (normalizing +
    embedding hundreds of rows). It reports MCP progress notifications as
    it goes (resolve operations -> normalize -> embed -> cluster -> write
    workbook) for clients that display them -- but progress notifications
    do NOT extend a client's response-wait timeout (that's a fixed wait on
    the underlying JSON-RPC request), so a client invoking this on a large
    input still needs a generously long session timeout configured (see
    run_agent.py's `client_session_timeout_seconds`).

    Args:
        files: paths to raw_lines-shaped JSONL files (each line a raw PFMEA
            row dict with at least row_id/source/process_step/failure_cause).
            Omit to use ground_truth/raw_lines.jsonl.
        similarity_threshold: cosine-similarity cutoff for grouping a
            candidate row with a baseline row. Omit to calibrate it live
            from ground_truth/decisions.jsonl (the 25th percentile of the
            true-duplicate similarity distribution) -- a deployment with no
            ground-truth answer key must supply this explicitly.
        output_json_path: where to write the clusters JSON. Defaults to
            exports/phase4_clusters_for_review.json.
        output_workbook_path: where to write the review workbook. Defaults
            to exports/Phase4_Cluster_Review.xlsx.

    Returns: {workbook_path, clusters_json_path, threshold_used, clusters,
    clustered_rows, unclustered_rows}.
    """
    paths = [Path(f) for f in files] if files else None

    async def _on_progress(progress: float, total: float, message: str) -> None:
        if ctx is None:
            return
        try:
            await ctx.report_progress(progress, total, message)
        except Exception:
            # Progress is observability, not correctness -- a client/context
            # that can't accept a progress notification (e.g. no progress
            # token on this request, or a Context not bound to a live
            # request) must never fail the actual clustering run over it.
            pass

    return await run_path2_clustering(
        paths,
        threshold=similarity_threshold,
        output_json_path=Path(output_json_path) if output_json_path else None,
        output_workbook_path=Path(output_workbook_path) if output_workbook_path else None,
        on_progress=_on_progress if ctx is not None else None,
    )


@mcp.tool()
def ingest_decisions(
    workbook_path: str, output_path: str | None = None, ground_truth_dir: str | None = None
) -> dict:
    """Read a COMPLETED Phase4_Cluster_Review.xlsx (an engineer's Merge /
    Keep Separate / Unsure calls), resolve those decisions into a final
    consolidated row set, and export it through the existing Phase 7
    exporter.

    Args:
        workbook_path: path to the completed review workbook.
        output_path: where to write the resulting xlsx. Defaults to
            exports/PFMEA_Ingested_Export.xlsx.
        ground_truth_dir: directory containing final_lines.jsonl /
            raw_lines.jsonl used to reconstruct row content. Defaults to
            this repo's ground_truth/ directory.

    Returns: {output_path, total_rows, baseline_rows, kept_separate,
    flagged_uncertain, merged_excluded}.
    """
    out_path = Path(output_path) if output_path else EXPORTS_DIR / "PFMEA_Ingested_Export.xlsx"
    kwargs = {"ground_truth_dir": Path(ground_truth_dir)} if ground_truth_dir else {}
    summary = ingest_and_export(Path(workbook_path), out_path, **kwargs)
    return {"output_path": str(out_path), **summary}


@mcp.tool()
def export_final_pfmea(input_path: str | None = None, output_path: str | None = None) -> dict:
    """Run the Phase 7 export engine over a final_lines.jsonl-shaped file
    (defaults to ground_truth/final_lines.jsonl) and write the
    02_FINAL_PFMEA-structured workbook.

    Args:
        input_path: path to a final_lines.jsonl-shaped file. Omit to use
            ground_truth/final_lines.jsonl.
        output_path: where to write the resulting xlsx. Defaults to
            exports/02_FINAL_PFMEA_export.xlsx.

    Returns: {output_path, rows_exported}.
    """
    out_path = Path(output_path) if output_path else EXPORTS_DIR / "02_FINAL_PFMEA_export.xlsx"
    kwargs = {"input_path": Path(input_path)} if input_path else {}
    rows_exported = _export_final_pfmea(out_path, **kwargs)
    return {"output_path": str(out_path), "rows_exported": rows_exported}


@mcp.tool()
def get_pipeline_status() -> dict:
    """Report which stage the pipeline is at (raw lines extracted ->
    clustered -> under human review -> ingested -> exported), by
    inspecting existing artifacts and, if DATABASE_URL is set,
    standard_line row counts.

    Returns: {stage, raw_lines_available, clusters_json_available,
    review_workbook_available, review_workbook_has_decisions,
    final_export_available, [standard_line]}.
    """
    return _get_pipeline_status()


@mcp.tool()
def search_standard_lines(
    query: str,
    limit: int = 20,
    status: str | None = None,
    product_family: str | None = None,
    operation: str | None = None,
) -> list[dict]:
    """Search the standard_line table. `query` is a case-insensitive
    substring match checked against the failure mode/cause/effect text
    AND against the operation code and product family -- so query="OP10",
    query="chokes", or query="coil winding" all work directly, the same
    as searching failure-mode text. You do not need the separate
    `operation`/`product_family` arguments just to filter by an operation
    code or family name; use `query` for that. Those two arguments are for
    narrowing an already-known exact operation/family further (e.g.
    combined with a `query` keyword, or with `status`). Read-only --
    cannot approve or modify rows. Requires DATABASE_URL to be set.

    For aggregate questions ("which operations have the most rows",
    "how many per operation") use count_by_operation instead of paging
    through search results with a high `limit` -- that returns full row
    content for up to `limit` rows and is NOT meant for counting; loading
    hundreds of rows just to count them risks overflowing your context.

    Args:
        query: text or code to search for -- matches failure mode/cause/
            effect, operation (e.g. "OP10"), or product_family (e.g.
            "chokes"). Pass "" to list rows matching only the other filters.
        limit: max rows to return (default 20).
        status: exact filter on draft/reviewed/approved/archived.
        product_family: exact filter, e.g. "chokes".
        operation: exact filter, e.g. "OP60".

    Returns: a list of {id, product_family, operation, line_key, version,
    status, approved_by, approved_at, line_payload}.
    """
    from db.connection import get_session_factory
    from db.standard_line_query import search_standard_lines as _search

    session_factory = get_session_factory()
    with session_factory() as session:
        return _search(session, query, limit=limit, status=status, product_family=product_family, operation=operation)


@mcp.tool()
def count_by_operation(status: str | None = None, product_family: str | None = None) -> dict[str, int]:
    """Row counts per operation across the whole standard_line table (a
    GROUP BY query run in the database) -- use this for aggregate
    questions like "which operations have the most rows" or "how many
    OP50 rows are there". Returns ONLY {operation: count}, never row
    content, so it stays cheap no matter how many rows standard_line
    holds -- unlike search_standard_lines, which returns full rows and
    was never meant to answer counting questions by paging through with a
    large `limit` (that's what previously blew an agent's context window
    trying to load all 496 rows just to count them). Read-only. Requires
    DATABASE_URL to be set.

    Args:
        status: exact filter on draft/reviewed/approved/archived. Omit to
            count across all statuses.
        product_family: exact filter, e.g. "chokes". Omit to count across
            all product families.

    Returns: {operation_code: count}, e.g. {"OP50": 145, "OP40": 123, ...},
    sorted by count descending.
    """
    from db.connection import get_session_factory
    from db.standard_line_query import count_by_operation as _count_by_operation

    session_factory = get_session_factory()
    with session_factory() as session:
        return _count_by_operation(session, status=status, product_family=product_family)


# --- GENERATION: assembling a new PFMEA from approved standard_line content ---
# Separate capability from everything above: these tools build a NEW PFMEA
# out of already-approved knowledge, never read/consolidate source files, and
# never invent content -- see pfmea_agents/generation.py's module docstring.

@mcp.tool()
def list_product_families() -> list[str]:
    """List the product families that have any standard_line content at
    all (approved or not) -- "chokes" today, more as they're added. Use
    this first when a user wants to create/generate a new PFMEA but hasn't
    said which product family. Read-only. Requires DATABASE_URL.

    Returns: a list of product family names, e.g. ["chokes"].
    """
    from db.connection import get_session_factory
    from pfmea_agents.generation import list_product_families as _list_product_families

    session_factory = get_session_factory()
    with session_factory() as session:
        return _list_product_families(session)


@mcp.tool()
def list_operations(product_family: str) -> list[dict]:
    """List the operations within `product_family` that have at least one
    APPROVED standard_line row, each with how many -- in canonical process
    order (OP10, OP20, ...), not ranked by count. Use this to check what's
    actually available before proposing an operation sequence to a user
    creating a new PFMEA, and to catch operations they name that have no
    approved content yet. Read-only. Requires DATABASE_URL.

    Args:
        product_family: exact match, e.g. "chokes".

    Returns: a list of {operation, name, approved_count}, e.g.
    [{"operation": "OP10", "name": "Incoming material & lot ID", "approved_count": 16}, ...].
    An operation with zero approved rows simply does not appear in this list --
    use list_process_operations to map the user's words to operation codes.
    """
    from dataclasses import asdict

    from db.connection import get_session_factory
    from pfmea_agents.generation import list_operations as _list_operations
    from rules.operations import OPERATIONS

    names = {op.code: op.name for op in OPERATIONS}
    session_factory = get_session_factory()
    with session_factory() as session:
        return [{**asdict(o), "name": names.get(o.operation, "")} for o in _list_operations(session, product_family)]


@mcp.tool()
def list_process_operations() -> list[dict]:
    """The canonical process flow: every operation code with its name and what
    it covers, in process order (OP10 ... OP200). Use this FIRST to translate
    the user's own words ("tinning", "glue", "containment"...) into operation
    codes -- never guess a code from its number. Then use list_operations to
    see which of those codes have approved content; a code the user needs that
    has none must be reported as a gap, not silently dropped. Read-only, no
    database needed.

    Returns: [{"operation": "OP50", "name": "Tinning / solder bath",
    "covers": "Flux / fluxing / bath temp / tinning length"}, ...]
    """
    from rules.operations import OPERATIONS

    return [{"operation": op.code, "name": op.name, "covers": op.detail} for op in OPERATIONS]


@mcp.tool()
def generate_pfmea(product_family: str, selected_operations: list[str], output_path: str | None = None) -> dict:
    """Assemble a NEW Draft PFMEA from already-approved standard_line
    content for `selected_operations` within `product_family`, and WRITE IT
    to an actual .xlsx file using this project's AIAG-VDA 02_FINAL_PFMEA
    template (the same structure/columns as export_final_pfmea -- AP only,
    never RPN). ONLY pulls status='approved' rows (never draft/reviewed/
    archived, never a row still flagged uncertain) and marks every included
    row content_type='VALIDATED' -- this tool assembles proven content, it
    never invents a row for an operation that has none.

    IMPORTANT WORKFLOW RULE: never call this until the user has explicitly
    confirmed the proposed operation sequence (from list_operations). Present
    the sequence and wait for their go-ahead first -- see your instructions
    for the full intake -> propose -> confirm -> generate flow.

    THE FILE AT `output_path` IS THE DELIVERABLE: it already carries the
    correct AIAG-VDA structure (planning header, STEP 2-6 columns, AP
    ratings, no RPN) plus a "Source Status" column marking each row
    VALIDATED vs. a gap placeholder. Present/return THIS file to the user --
    never reformat the `rows` JSON into your own table or workbook; `rows`
    is there for you to talk about the content in chat, not to rebuild it.

    If an operation in `selected_operations` has no approved content, it is
    reported in `summary.operations_with_no_content` AND gets one clearly
    flagged placeholder row in the output file (Source Status = "DRAFT - NO
    VALIDATED CONTENT") rather than silently vanishing from the sheet --
    tell the user plainly which operation(s) that was. Nothing is written
    to standard_line. Requires DATABASE_URL.

    Args:
        product_family: exact match, e.g. "chokes".
        selected_operations: operation codes to include, in any order, e.g.
            ["OP10", "OP40", "OP50"]. Order of the *output* rows follows
            canonical process sequence regardless of the order given here.
        output_path: where to write the resulting xlsx. Defaults to
            exports/Generated_PFMEA_<product_family>.xlsx.

    Returns: {output_path, status: "Draft", rows: [...], summary:
    {product_family, operations_requested, operations_included,
    operations_with_no_content, total_line_items}}. Each row includes
    content_type, its standard_line provenance (id/line_key/version/
    approved_by/approved_at), and the full line_payload (process_step,
    failure_mode, failure_cause, failure_effect, severity/occurrence/
    detection/ap, etc.) -- for reference only; the xlsx at output_path is
    what to hand to the user.
    """
    from dataclasses import asdict

    from db.connection import get_session_factory
    from exports.export_generated_pfmea import export as _export_generated_pfmea
    from pfmea_agents.generation import generate_pfmea as _generate_pfmea

    session_factory = get_session_factory()
    with session_factory() as session:
        result = _generate_pfmea(session, product_family, selected_operations)

    safe_family = "".join(c if c.isalnum() else "_" for c in product_family)
    out_path = Path(output_path) if output_path else EXPORTS_DIR / f"Generated_PFMEA_{safe_family}.xlsx"
    _export_generated_pfmea(result, out_path)

    return {"output_path": str(out_path), **asdict(result)}


@mcp.tool()
async def suggest_pfmea_additions(product_family: str, operation: str) -> list[dict]:
    """LAYER 2: propose PFMEA failure modes/causes/controls for `operation`
    that are NOT already in the approved standard_line knowledge base --
    from the model's general PFMEA engineering knowledge, explicitly
    prompted with what's already covered so it proposes GAPS, not
    duplicates. Use this AFTER presenting generate_pfmea's validated
    content for an operation, especially one with little or no approved
    coverage.

    Every suggestion is structurally distinct from a validated row: it
    carries content_type='AI_SUGGESTION' and a `rationale`, and has NO
    id/line_key/version/status/approved_by/approved_at -- it was never a
    standard_line row and this tool NEVER writes one. Present these in a
    clearly separate section (e.g. "AI SUGGESTIONS -- not validated,
    require engineer review") -- never merge them into validated content
    or imply they're approved. The only way a suggestion becomes real
    content is the existing human-only approval path (approve_to_db.py) --
    nothing here can do that, and you cannot either. Requires
    OPENAI_API_KEY and DATABASE_URL.

    Args:
        product_family: e.g. "chokes".
        operation: the operation to find gaps for, e.g. "OP80".

    Returns: a list of {content_type, product_family, operation,
    line_payload: {failure_mode, failure_cause, failure_effect,
    prevention_control, detection_control}, rationale}. May be empty if
    the model finds nothing genuinely new to add.
    """
    from db.connection import get_session_factory
    from db.standard_line_query import search_standard_lines as _search
    from pfmea_agents.generation import suggest_additions as _suggest_additions

    session_factory = get_session_factory()
    with session_factory() as session:
        existing_rows = _search(
            session, "", status="approved", product_family=product_family, operation=operation, limit=1_000_000,
        )
    return await _suggest_additions(product_family, operation, existing_rows)


@mcp.tool()
async def draft_pfmea_from_ai_suggestions(
    product_family: str, selected_operations: list[str], output_path: str | None = None,
) -> dict:
    """LAYER 2 BATCH: for a product_family with NO (or only partial)
    approved standard_line content -- generate_pfmea has nothing, or not
    enough, to assemble -- run suggest_additions across every operation in
    `selected_operations` and write the combined result to an actual .xlsx
    file, the same AIAG-VDA structure generate_pfmea uses (AP only, never
    RPN), with every row content_type='AI_SUGGESTION' and an unmissable
    banner across the top marking the ENTIRE document an unvalidated AI
    draft requiring engineer review.

    ONLY use this when list_operations reports zero approved rows for the
    operations you need -- for a product with any real validated content,
    call generate_pfmea first; use suggest_pfmea_additions (singular,
    per-operation) to fill specific gaps in an otherwise-validated
    document instead of this. Never call this tool for operations that
    already have approved content without separately assembling that
    content too -- doing so would present an AI draft as if it were the
    only thing available when validated content actually exists.

    THE FILE AT `output_path` IS THE DELIVERABLE, same as generate_pfmea:
    present/return it, never reformat the `rows` JSON into your own table.
    No Severity/Occurrence/Detection/AP is ever included -- Layer 2 does
    not invent ratings; those columns are intentionally left blank pending
    a real engineering exercise, not fabricated. Nothing here is ever
    written to standard_line. Requires OPENAI_API_KEY and DATABASE_URL.

    Args:
        product_family: e.g. "brushes" -- a family with no approved content yet.
        selected_operations: operation codes to draft, e.g. ["OP10", "OP20"].
        output_path: where to write the resulting xlsx. Defaults to
            exports/AI_Draft_PFMEA_<product_family>.xlsx.

    Returns: {output_path, status: "Draft", rows: [...], summary:
    {product_family, operations_requested, operations_with_suggestions,
    operations_with_no_suggestions, total_line_items}}.
    """
    from dataclasses import asdict

    from db.connection import get_session_factory
    from db.standard_line_query import search_standard_lines as _search
    from exports.export_generated_pfmea import export_ai_draft as _export_ai_draft
    from pfmea_agents.generation import draft_pfmea_from_ai_suggestions as _draft

    session_factory = get_session_factory()
    existing_by_operation: dict[str, list[dict]] = {}
    with session_factory() as session:
        for operation in selected_operations:
            existing_by_operation[operation] = _search(
                session, "", status="approved", product_family=product_family, operation=operation, limit=1_000_000,
            )

    result = await _draft(product_family, selected_operations, existing_by_operation)

    safe_family = "".join(c if c.isalnum() else "_" for c in product_family)
    out_path = Path(output_path) if output_path else EXPORTS_DIR / f"AI_Draft_PFMEA_{safe_family}.xlsx"
    _export_ai_draft(result, out_path)

    return {"output_path": str(out_path), **asdict(result)}


DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000


class TokenAuthMiddleware:
    """Minimal ASGI middleware: every HTTP request must carry
    `Authorization: Bearer <token>` or it's rejected with 401 before ever
    reaching the MCP app. Fails CLOSED -- if no token is configured
    (`token=None`), every request is rejected rather than let through,
    since this is the transport meant to be tunneled onto the public
    internet via ngrok."""

    def __init__(self, app, token: str | None):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {self.token}" if self.token else None

        if expected is None or auth_header != expected:
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def build_http_app(token: str | None = None):
    """Wraps the FastMCP streamable-http ASGI app with TokenAuthMiddleware.
    Kept separate from run_http() so tests can exercise it in-process
    (e.g. via httpx's ASGITransport) without binding a real socket."""
    if token is None:
        token = os.environ.get("MCP_HTTP_TOKEN")
    return TokenAuthMiddleware(mcp.streamable_http_app(), token=token)


def run_http(host: str = DEFAULT_HTTP_HOST, port: int = DEFAULT_HTTP_PORT, token: str | None = None) -> None:
    import uvicorn

    if token is None:
        token = os.environ.get("MCP_HTTP_TOKEN")
    if not token:
        raise SystemExit(
            "MCP_HTTP_TOKEN is not set -- refusing to start in HTTP mode without a shared-secret token "
            "(this transport is meant to be tunneled onto the public internet via ngrok). "
            "Set MCP_HTTP_TOKEN in .env, then retry."
        )

    app = build_http_app(token=token)
    path = mcp.settings.streamable_http_path
    print(f"pfmea-consolidation MCP server (streamable-http) listening on http://{host}:{port}{path}")
    print("Every request requires: Authorization: Bearer <MCP_HTTP_TOKEN>")
    uvicorn.run(app, host=host, port=port, log_level=mcp.settings.log_level.lower())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="pfmea-consolidation MCP server")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="stdio (default -- what run_agent.py's MCPServerStdio talks to) or http (streamable-http, for ngrok)",
    )
    parser.add_argument("--host", default=os.environ.get("MCP_HTTP_HOST", DEFAULT_HTTP_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_HTTP_PORT", DEFAULT_HTTP_PORT)))
    args = parser.parse_args(argv)

    if args.transport == "http":
        run_http(host=args.host, port=args.port)
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
