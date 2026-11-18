# PFMEA Consolidation Agent — Local Web UI

A small local chat page for demoing/testing the PFMEA consolidation agent
in a browser instead of a terminal. Same agent setup as [run_agent.py](../run_agent.py)
(same instructions, same MCP server, same six tools) — this just gives
it a web front end and streams the response back as it's generated.

**Local only.** No external hosting, no tunnel (that's what [mcp_server](../mcp_server/README.md)'s
ngrok mode is for, and it's a separate concern). This binds to
`127.0.0.1` and is meant to be opened in your own browser, nothing else.

## Run it

```bash
python -m webui.app
```

This loads `.env` (same `OPENAI_API_KEY` / `DATABASE_URL` as everywhere
else in this repo — see the repo root README / `.env.example` if you
don't have a `.env` yet), starts the pfmea-consolidation MCP server as a
subprocess, starts the web server, and opens your browser to
`http://127.0.0.1:8001` automatically. Ctrl-C in the terminal to stop
(this also shuts down the MCP subprocess cleanly).

If `OPENAI_API_KEY` isn't set, the page still loads and shows a banner
saying so — you don't get a chat, but you can still see the UI.

Change the port with `WEBUI_PORT` (e.g. `WEBUI_PORT=8099 python -m webui.app`)
if 8001 is already in use on your machine.

## What's on the page

- A single HTML page (`webui/static/index.html`) — a text box, a Send
  button, and a scrolling conversation view. No build step, no framework,
  inline CSS/JS.
- Your messages and the agent's replies appear as chat bubbles.
- Tool calls the agent makes (`cluster_pfmea_files`, `get_pipeline_status`,
  etc.) show up as small inline "🔧 calling ... / ✓ ... finished" lines,
  so you can see when it's actually running something vs. just talking.
- A 📎 attach button next to the message box lets you upload a product
  drawing, schematic, or process diagram image -- one of the three ways to
  describe your process to the GENERATION capability (the other two:
  describing it in words, or naming operation codes directly). The agent
  reads the image and proposes an operation sequence from it, but always
  as a best-effort interpretation you must confirm or correct before
  anything gets generated -- the same mandatory validation step every
  intake path goes through.
- The assistant's reply streams in token by token as the model generates
  it (via `Runner.run_streamed()` + newline-delimited JSON events over a
  regular streaming HTTP response — no WebSocket needed).

## How it's wired

`webui/app.py` is a small FastAPI app:

- `GET /` — serves the static HTML page.
- `GET /api/health` — `{"ready": bool}`, used by the page to show a
  banner if `OPENAI_API_KEY` is missing.
- `POST /api/chat` — body `{"message": str, "history": [...], "image":
  str | null}`, streams back newline-delimited JSON events (`delta`,
  `tool_call`, `tool_output`, `history`, `error`) as the agent runs. The
  browser sends the whole conversation history back on each request (no
  server-side session state) — the same `result.to_input_list()` pattern
  `run_agent.py` uses for its terminal chat loop. `image`, when present,
  is a data URL (`data:image/png;base64,...`) read client-side via
  `FileReader` — nothing is stored server-side. `run_agent.build_user_message`
  (shared with the terminal chat loop's `/image <path>` command) turns
  `{message, image}` into the multimodal `input_text` + `input_image`
  content list the model actually sees.
- On startup (FastAPI's `lifespan`), it connects to the MCP server once
  via `run_agent.build_mcp_server()` — imported directly, not
  reimplemented — and keeps that one connection alive for the whole time
  the web server runs, rather than spawning a new subprocess per message.

## Tests

```bash
pytest tests/test_webui.py -q
```

Uses FastAPI's `TestClient`, which runs the real startup lifespan — so
these genuinely spin up the MCP server subprocess, same as a real run.
Two of the six tests make a real OpenAI API call and are skipped
cleanly if `OPENAI_API_KEY` isn't set, same pattern as the `DATABASE_URL`-gated
tests elsewhere in this repo.
