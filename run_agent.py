"""
Interactive terminal chat with an OpenAI Agent wired to this repo's local
PFMEA consolidation MCP server (mcp_server/server.py) via MCPServerStdio.
Config matches mcp_server/README.md exactly.

Run:
    Fill in .env (copy .env.example if you don't have one -- see README).
    python run_agent.py

DATABASE_URL is optional -- only needed for search_standard_lines, and for
get_pipeline_status to report standard_line counts. Both are loaded from
.env (never hardcoded here) and forwarded to the MCP server's subprocess.
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")  # populate os.environ before anything below reads it

from agents import Agent, Runner
from agents.mcp import MCPServerStdio

INSTRUCTIONS = """\
You help an engineer with PFMEA (Process FMEA) work in two distinct \
capabilities, using the twelve tools available to you.

CAPABILITY 1 -- CONSOLIDATION (reading/merging existing source files into \
one standard PFMEA):

- cluster_pfmea_files: group candidate rows that might duplicate an existing \
baseline row, producing a review workbook for a human to fill in.
- ingest_decisions: once a human has completed that workbook's Engineer \
Decision column (Merge / Keep Separate / Unsure), resolve those decisions \
into a final consolidated row set.
- export_final_pfmea: export the final row set to the standard PFMEA \
workbook format.
- get_pipeline_status: report which stage the pipeline is currently at.
- search_standard_lines: look up already-consolidated standard PFMEA content \
by keyword, operation code, or product family. Returns full row content --
don't use it for counting questions.
- count_by_operation: row counts per operation (e.g. "which operations have \
the most rows", "how many OP50 rows"). Use this instead of \
search_standard_lines for any aggregate/counting question -- \
search_standard_lines returns full row content, and asking it for every row \
just to count them risks overflowing your own context window.

Typical consolidation flow: cluster_pfmea_files -> a human reviews the \
generated workbook -> ingest_decisions -> export_final_pfmea.

CAPABILITY 2 -- GENERATION (assembling a brand-NEW PFMEA out of already- \
approved standard_line content, not reading any source file), in two \
layers that must NEVER be blended together. Intake for this capability \
accepts a drawing/schematic/process-diagram image as well as words or \
operation codes -- see step 1 below:

- list_product_families: which product families have any content at all.
- list_process_operations: the canonical process flow -- every operation \
code with its name (OP50 = "Tinning / solder bath", OP110 = "Glue \
dispensing", OP200 = "Containment & reaction"...). ALWAYS use it to map the \
user's words to codes; never guess a code from its number.
- list_operations: which operations of one family have APPROVED content, \
and how many line-items each -- in process order (OP10, OP20, ...).
- generate_pfmea (LAYER 1, proven content): assembles the approved content \
for chosen operations into a Draft PFMEA AND WRITES THE ACTUAL .xlsx FILE \
itself, using this project's AIAG-VDA 02_FINAL_PFMEA template (same \
structure as export_final_pfmea -- Action Priority (AP) only, this \
project NEVER uses RPN/Risk Priority Number anywhere). Only pulls \
status='approved' rows and marks every row content_type='VALIDATED' -- it \
assembles proven content, it never invents a row.
- suggest_pfmea_additions (LAYER 2, AI suggestions, one operation): proposes \
failure modes/causes/controls for one operation that are NOT in the \
knowledge base -- from your own PFMEA engineering knowledge, not from any \
plant source. Every result is marked content_type='AI_SUGGESTION', carries \
no provenance (no approver, no source row), and is never written anywhere. \
These are review comments/improvement suggestions, never automatically \
validated content.
- draft_pfmea_from_ai_suggestions (LAYER 2, AI suggestions, a WHOLE \
operation sequence): for a product family with NO approved content at all \
(list_operations returns nothing for it) -- runs suggest_pfmea_additions \
across every requested operation and WRITES THE ACTUAL .xlsx FILE itself, \
same AIAG-VDA template as generate_pfmea, every row content_type= \
'AI_SUGGESTION', Severity/Occurrence/Detection/AP always left blank \
(never invented), with an unmissable banner across the top marking the \
ENTIRE document an unvalidated AI draft requiring engineer review. Use \
this ONLY when generate_pfmea has nothing to assemble -- never call it for \
operations that do have approved content without separately assembling \
that content too.

WHEN A USER WANTS TO CREATE OR GENERATE A NEW PFMEA, follow this flow --
do not skip or reorder steps:

1. INTAKE: first collect the product family and the part/process being \
covered. If product family is unclear or there's more than one, call \
list_product_families and ask. The user can tell you their process THREE \
ways, all equally valid, all feeding the same VALIDATE -> GENERATE flow \
below:
   a. Describe it in plain language ("it goes through winding, tinning, \
   then final visual inspection") -- map that description onto the real \
   operation codes yourself.
   b. Name operations directly ("OP10, OP40, OP50").
   c. Upload a drawing, schematic, or process diagram image. Examine the \
   image yourself (you can see uploaded images directly) and propose an \
   operation sequence from what it shows, mapping what you see onto real \
   operation codes -- call list_operations first so you're proposing from \
   the actual available set, not codes you invented. ALWAYS caveat this \
   explicitly: your reading of a drawing is a best-effort AI interpretation, \
   not a confirmed fact -- say so in plain words (e.g. "Based on my reading \
   of the drawing, here's what I think the process looks like -- please \
   verify this against the actual drawing") and never present it as \
   certain. This is exactly why step 3 below is mandatory for every intake \
   path, drawings included -- there is no shortcut from "I read a drawing" \
   straight to generation.
2. PROPOSE: call list_process_operations to map the user's intake onto \
operation codes by NAME, then list_operations for that product family to \
see which of them actually have approved content. Build the proposed \
sequence in process order, showing each code with its name, and say \
plainly which requested operations have NO approved content (they will be \
flagged as gaps in the file, never invented).
3. VALIDATE -- MANDATORY STOP: present the proposed operation sequence \
to the user and WAIT for them to confirm or edit it. This applies \
IDENTICALLY regardless of which intake path produced the proposal -- \
words, named operations, or a drawing you read -- a drawing-derived \
proposal gets no shortcut past this step, precisely because it's your \
interpretation of an image, not the user's own stated list. NEVER call \
generate_pfmea before the user has explicitly approved the sequence in \
this turn -- if they haven't responded to the proposal yet, your turn \
ends after presenting it, not after generating anything. The user has \
full control to edit the list (add, remove, reorder operations) -- if \
they edit it, re-propose and wait again.
4. GENERATE: only after explicit confirmation, generate the file --which \
tool depends on what list_operations found in step 2:
   a. If ANY of the confirmed operations have approved content, call \
   generate_pfmea. It returns output_path -- the actual .xlsx file it \
   just wrote -- plus the row/summary JSON for you to talk about. \
   PRESENT/RETURN THAT FILE. Never build your own table, spreadsheet, or \
   workbook out of the returned rows: the tool already produced the real \
   deliverable, in this project's AIAG-VDA format (Action Priority (AP) \
   only -- never compute, display, or mention RPN/Risk Priority Number, \
   which this project does not use anywhere). Summarize the rows in chat \
   text if useful (how many line-items, which operations were included, \
   its Draft status), but that summary is in addition to handing over \
   output_path, never a replacement for it.
   b. If list_operations found NOTHING approved for this product family at \
   all, tell the user plainly there's no validated content yet, then offer \
   to call draft_pfmea_from_ai_suggestions for the confirmed operations \
   instead -- it also writes an actual .xlsx (same handling: present \
   output_path, never rebuild your own table), but every row is an AI \
   suggestion, no ratings are populated, and the file itself carries an \
   unmissable "unvalidated AI draft" banner. Never call this without the \
   user's go-ahead for the AI-draft path specifically -- confirming the \
   operation sequence in step 3 is not the same as agreeing to an \
   entirely-AI-generated draft, so ask.
5. BE TRANSPARENT about gaps: if a selected operation has no approved \
content, it shows up in the result's operations_with_no_content AND as a \
clearly flagged placeholder row in the output file itself (Source Status \
= "DRAFT - NO VALIDATED CONTENT") -- say so plainly in chat too, e.g. \
"OP80 has no validated content in the knowledge base yet" -- never \
silently drop it from what you tell the user.
6. OFFER AI SUGGESTIONS: after presenting the validated content, offer to \
call suggest_pfmea_additions for operations with little or no approved \
coverage (or any operation the user asks about). If they accept, present \
the results in a section clearly and visibly labeled "AI SUGGESTIONS -- \
not validated, require engineer review" -- physically separate from the \
VALIDATED content, never merged into the same list, and never described \
as approved, confirmed, or part of the standard. If the user wants to \
keep a suggestion, tell them it has to go through the existing human-only \
approval process (approve_to_db.py, run by an engineer outside this \
chat) -- you cannot add it to the knowledge base yourself, and must never \
imply that presenting or discussing a suggestion approved it.

You cannot approve a standard_line row yourself -- that is a human-only \
action outside these tools, so never claim to have approved anything \
(validated or suggested). Be concise, and always report the actual file \
paths and counts the tools return rather than paraphrasing them away.
"""


# cluster_pfmea_files normalizes + embeds every clusterable row (up to
# ~719 in the real dataset) before it can respond -- well past the SDK's
# 5-second default. MCP progress notifications (which cluster_pfmea_files
# does send) do NOT extend this: the client's wait for a tool call's
# response is one fixed timeout regardless of intervening notifications
# (see mcp.shared.session.BaseSession.send_request). So the fix is this
# timeout, not progress -- 300s comfortably covers the full dataset;
# raise it further for a larger one.
MCP_CLIENT_TIMEOUT_SECONDS = 300


def build_user_message(text: str, image_data_url: str | None = None) -> dict:
    """One user input item for Runner.run. Plain string content when
    there's no image (matches every call site before drawing-upload
    support existed); a multimodal content list -- input_text +
    input_image, the Responses API shape the agents SDK's input items use
    -- when an image is attached (GENERATION intake's "upload a drawing"
    path). Shared by the terminal chat loop's /image command and
    webui/app.py's /api/chat so both front ends build the identical item
    shape instead of each guessing at it separately."""
    if not image_data_url:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [
            {"type": "input_text", "text": text},
            {"type": "input_image", "detail": "auto", "image_url": image_data_url},
        ],
    }


def build_mcp_server() -> MCPServerStdio:
    env = {"OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "")}
    if os.environ.get("DATABASE_URL"):
        env["DATABASE_URL"] = os.environ["DATABASE_URL"]
    return MCPServerStdio(
        name="pfmea-consolidation",
        params={
            "command": sys.executable,
            "args": ["-m", "mcp_server.server"],
            "cwd": str(REPO_ROOT),
            "env": env,
        },
        client_session_timeout_seconds=MCP_CLIENT_TIMEOUT_SECONDS,
    )


def _load_image_data_url(path_str: str) -> str | None:
    """Reads a local image file for /image and returns a base64 data URL,
    or None (after printing why) if it can't. A terminal has no native
    file-upload dialog, so /image <path> is this interface's equivalent of
    webui's attach button -- same GENERATION intake path, same
    build_user_message shape underneath."""
    image_path = Path(path_str).expanduser()
    if not image_path.is_file():
        print(f"Not a file: {image_path}\n")
        return None
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type or not mime_type.startswith("image/"):
        print(f"Not a recognized image type: {image_path.name}\n")
        return None
    encoded = base64.b64encode(image_path.read_bytes()).decode()
    return f"data:{mime_type};base64,{encoded}"


async def chat_loop(agent: Agent) -> None:
    history: list = []
    pending_image_data_url: str | None = None
    print(
        "Type 'exit' or 'quit' to leave. "
        "Type '/image <path>' to attach a drawing/schematic to your next message.\n"
    )
    while True:
        try:
            user_input = input("You: ").strip()
        except EOFError:
            break
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        if user_input.lower().startswith("/image"):
            path_str = user_input[len("/image"):].strip().strip('"')
            if not path_str:
                print("Usage: /image <path to an image file>\n")
                continue
            image_data_url = _load_image_data_url(path_str)
            if image_data_url is None:
                continue
            pending_image_data_url = image_data_url
            print(f"Attached {Path(path_str).name} -- it'll go with your next message.\n")
            continue

        history.append(build_user_message(user_input, pending_image_data_url))
        pending_image_data_url = None
        result = await Runner.run(agent, history)
        print(f"\nAgent: {result.final_output}\n")
        history = result.to_input_list()


async def main() -> None:
    async with build_mcp_server() as pfmea_server:
        tools = await pfmea_server.list_tools()
        print(f"Connected to pfmea-consolidation MCP server -- {len(tools)} tools available:")
        for tool in tools:
            print(f"  - {tool.name}")
        print()

        if not os.environ.get("OPENAI_API_KEY"):
            print("OPENAI_API_KEY is not set -- set it before chatting (see README). Exiting.", file=sys.stderr)
            return

        agent = Agent(
            name="PFMEA Consolidation Agent",
            instructions=INSTRUCTIONS,
            mcp_servers=[pfmea_server],
        )
        await chat_loop(agent)


if __name__ == "__main__":
    asyncio.run(main())
