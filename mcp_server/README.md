# PFMEA Consolidation MCP Server

Exposes this repo's already-built PFMEA consolidation engine as MCP tools,
using the official Python MCP SDK (`pip install mcp`, already a dependency
of `openai-agents`). Every tool is a thin wrapper around one existing
function — see `mcp_server/server.py`'s module docstring for the exact
mapping. Nothing here re-implements Phase 4/7 logic, and no tool can set a
`standard_line` row's status to `approved` (that stays human-only, per
spec §7).

## Tools

| Tool | Wraps | Requires |
|---|---|---|
| `cluster_pfmea_files(files?, similarity_threshold?, output_json_path?, output_workbook_path?)` | `pfmea_agents.clustering.run_path2_clustering` | `OPENAI_API_KEY` |
| `ingest_decisions(workbook_path, output_path?, ground_truth_dir?)` | `pfmea_agents.decision_ingestion.ingest_and_export` | — |
| `export_final_pfmea(input_path?, output_path?)` | `exports.export_final_pfmea.export` | — |
| `get_pipeline_status()` | `pfmea_agents.pipeline_status.get_pipeline_status` | — (checks `DATABASE_URL` opportunistically) |
| `search_standard_lines(query, limit?, status?, product_family?, operation?)` | `db.standard_line_query.search_standard_lines` | `DATABASE_URL` |
| `count_by_operation(status?, product_family?)` | `db.standard_line_query.count_by_operation` | `DATABASE_URL` |

`count_by_operation` exists because `search_standard_lines` returns full
row content — an agent asked "which operations have the most rows" once
tried to answer it by paging through all 496 rows via `search_standard_lines`
and overflowed its own context. `count_by_operation` runs a `GROUP BY
operation` query in the database and returns only `{operation: count}`,
so aggregate/counting questions never need to load a single row of content.

## Install

```bash
pip install mcp   # official Python MCP SDK — everything else (openpyxl,
                   # sqlalchemy, psycopg2, openai, openai-agents) is
                   # already a dependency elsewhere in this repo.
```

## Run it standalone (for a quick manual check)

```bash
cd pfmea-agent
python -m mcp_server.server
```

This starts the server on **stdio** (the default transport) and blocks,
waiting for an MCP client to talk to it over stdin/stdout. Ctrl-C to stop.

## HTTP mode (for exposing the server via ngrok)

The server also supports **streamable-http** transport, for when you want
to reach it over the network instead of spawning it as a stdio subprocess
(e.g. a demo where the agent runs somewhere else, or you just want to hit
it with `curl`). Select it with `--transport http` or `MCP_TRANSPORT=http`.

**Every HTTP request must carry a bearer token** — `Authorization: Bearer
<MCP_HTTP_TOKEN>`. The server refuses to start in HTTP mode at all if
`MCP_HTTP_TOKEN` isn't set (it's the transport meant to be reachable from
the public internet, so it never starts unauthenticated). `.env` already
has a pre-generated `MCP_HTTP_TOKEN` — change it any time with:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Run it locally in HTTP mode

```bash
python -m mcp_server.server --transport http --port 8000
```
```
pfmea-consolidation MCP server (streamable-http) listening on http://127.0.0.1:8000/mcp
Every request requires: Authorization: Bearer <MCP_HTTP_TOKEN>
```

Sanity-check it (should be 401 without the token, not-401 with it):
```bash
curl -i -X POST http://127.0.0.1:8000/mcp                                            # -> 401
curl -i -X POST http://127.0.0.1:8000/mcp -H "Authorization: Bearer <MCP_HTTP_TOKEN>"  # -> not 401
```

### Expose it with ngrok

1. Install ngrok (https://ngrok.com/download) and, once, authenticate it:
   ```bash
   ngrok config add-authtoken <your ngrok authtoken>
   ```
2. Start the MCP server in HTTP mode (leave this running):
   ```bash
   python -m mcp_server.server --transport http --port 8000
   ```
3. In a second terminal, tunnel that port:
   ```bash
   ngrok http 8000
   ```
   ngrok prints a public URL, e.g. `https://abcd-1-2-3-4.ngrok-free.app`.
4. Give whoever's demoing the agent that URL + path (`https://abcd-1-2-3-4.ngrok-free.app/mcp`)
   and the `MCP_HTTP_TOKEN` value — **out of band**, not inside the tunnel
   itself. Anyone with both can reach your tools for as long as the tunnel
   is up; stop the ngrok process (Ctrl-C) when the demo's over.
5. A remote OpenAI agent connects with `MCPServerStreamableHttp` instead of
   `MCPServerStdio`, passing the token as a header:
   ```python
   from agents.mcp import MCPServerStreamableHttp

   async with MCPServerStreamableHttp(
       name="pfmea-consolidation",
       params={
           "url": "https://abcd-1-2-3-4.ngrok-free.app/mcp",
           "headers": {"Authorization": "Bearer <MCP_HTTP_TOKEN>"},
       },
   ) as pfmea_server:
       ...  # same Agent(..., mcp_servers=[pfmea_server]) usage as the stdio example below
   ```

ngrok's free tier URLs are random and change every time you restart it —
re-share the new URL if you restart the tunnel mid-demo.

## Connect it to an OpenAI agent

The OpenAI Agents SDK (`pip install openai-agents`) talks to a stdio MCP
server via `agents.mcp.MCPServerStdio`. Exact, runnable config:

```python
import asyncio
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

async def main():
    async with MCPServerStdio(
        name="pfmea-consolidation",
        params={
            "command": "python",
            "args": ["-m", "mcp_server.server"],
            "cwd": r"/path/to/pfmea-consolidation-agent",
            "env": {
                "OPENAI_API_KEY": "sk-...",       # needed by cluster_pfmea_files
                "DATABASE_URL": "postgresql://...",  # needed by search_standard_lines
            },
        },
    ) as pfmea_server:
        agent = Agent(
            name="PFMEA Consolidation Agent",
            instructions=(
                "You help an engineer consolidate PFMEA source files into one "
                "standard PFMEA. Use cluster_pfmea_files to group candidate rows, "
                "tell the human to fill in the generated review workbook's "
                "Engineer Decision column, then use ingest_decisions once it's "
                "complete, then export_final_pfmea. Use get_pipeline_status to "
                "check progress and search_standard_lines to look up existing "
                "standard content. You cannot approve a standard_line row — "
                "that is done by a human outside this tool."
            ),
            mcp_servers=[pfmea_server],
        )
        result = await Runner.run(agent, "What's the current pipeline status?")
        print(result.final_output)

asyncio.run(main())
```

Notes:

- `cwd` **must** be this repo's root — the tools resolve default paths
  (`ground_truth/`, `exports/`) relative to it.
- `env` is passed to the subprocess as-is (it does **not** inherit your
  shell's environment) — include every variable the tools you intend to
  use actually need. Omit `DATABASE_URL` if you won't call
  `search_standard_lines` or want `get_pipeline_status` to skip its DB
  check; omit `OPENAI_API_KEY` if you won't call `cluster_pfmea_files`.
- Never hardcode real secrets in code you commit — read them from your
  own environment (`os.environ["OPENAI_API_KEY"]`) when building the
  `env` dict, the same way every other part of this repo does.

### Raw JSON server config (for MCP-config-driven tools / agent builders)

Some hosts (agent-builder UIs, other MCP clients) take a JSON block
instead of Python:

```json
{
  "mcpServers": {
    "pfmea-consolidation": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/pfmea-consolidation-agent",
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "DATABASE_URL": "postgresql://..."
      }
    }
  }
}
```

## Tests

```bash
pytest tests/test_mcp_server.py tests/test_mcp_http.py -q
```

Fully offline except two DB-gated cases (`search_standard_lines`'s live
lookup, skipped cleanly like every other DB test in this repo when
`DATABASE_URL` is unset). `cluster_pfmea_files`'s embedding call is
monkeypatched in tests — no `OPENAI_API_KEY` needed to run the suite.

`test_mcp_http.py` covers the HTTP transport: in-process token-check tests
(missing/wrong/correct token, fail-closed with no token configured), plus
one test that actually launches `python -m mcp_server.server --transport
http` as a real subprocess on a free port and hits it over a real TCP
connection — confirming the server starts and enforces the token exactly
as ngrok would see it.

## Interactive chat: run_agent.py

`run_agent.py` (repo root) is a runnable OpenAI Agents SDK script that
starts this MCP server as a subprocess (the exact `MCPServerStdio` config
above), lists its tools, and drops you into an interactive terminal chat
with an agent that has those six tools available.

```bash
set OPENAI_API_KEY=sk-...        # PowerShell: $env:OPENAI_API_KEY = "sk-..."
python run_agent.py
```

`DATABASE_URL` is optional — set it too if you want to try
`search_standard_lines` or see `standard_line` counts in
`get_pipeline_status`. The script forwards whichever of these two
variables are set in your own environment to the MCP server's subprocess;
it never hardcodes a key or connection string.

On startup it connects to the server, prints the tool list, then waits
for input (`exit` or `quit` to leave). Type `/image <path to a file>` to
attach a local drawing/schematic/process-diagram image to your next
message -- the terminal's equivalent of the web UI's 📎 attach button, for
the GENERATION capability's "upload a drawing" intake path (see below).
Example prompts to try:

1. `What's the current pipeline status?`
2. `Cluster the PFMEA files in ground_truth/raw_lines.jsonl and tell me how many clusters you found.`
3. `Search the standard lines for anything related to "coil winding".`
4. `Which operations have the most standard_line rows?`
5. `/image C:\path\to\process_diagram.png` followed by `Propose an operation sequence for this chokes process.` -- the agent reads the image, maps what it sees onto real operation codes (via `list_operations`), and presents the proposal for you to confirm or correct before generating anything.
