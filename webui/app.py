"""
Local web UI for the PFMEA consolidation agent: a single-page chat
frontend (webui/static/index.html) backed by a small FastAPI server that
runs the SAME agent setup as run_agent.py -- an OpenAI Agent connected to
this repo's MCP server via MCPServerStdio (build_mcp_server(), imported
from run_agent.py, not reimplemented), with the same five tools -- and
streams its response back to the page as it's generated.

Local-only, for demo/testing: binds to 127.0.0.1 by default, no external
hosting, no tunnel. `python -m webui.app` opens it in your browser.

Loads .env for OPENAI_API_KEY / DATABASE_URL, same as run_agent.py.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # so `import run_agent` works regardless of launch cwd
load_dotenv(REPO_ROOT / ".env")

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openai.types.responses import ResponseTextDeltaEvent
from pydantic import BaseModel

from agents import Agent, Runner

from run_agent import INSTRUCTIONS, build_mcp_server, build_user_message

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with build_mcp_server() as mcp_server:
        tools = await mcp_server.list_tools()
        print(f"Connected to pfmea-consolidation MCP server -- {len(tools)} tools available:")
        for tool in tools:
            print(f"  - {tool.name}")

        app.state.agent = Agent(
            name="PFMEA Consolidation Agent", instructions=INSTRUCTIONS, mcp_servers=[mcp_server],
        )
        yield


app = FastAPI(title="PFMEA Consolidation Agent (local demo)", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, Any]] = []
    # A data URL ("data:image/png;base64,...") for the GENERATION intake's
    # "upload a drawing" path -- the browser reads the attached file into
    # this via FileReader, nothing is stored server-side.
    image: str | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> JSONResponse:
    if not os.environ.get("OPENAI_API_KEY"):
        return JSONResponse({
            "ready": False,
            "message": "OPENAI_API_KEY is not set in .env -- chat will not work until it is.",
        })
    return JSONResponse({"ready": True})


def _ndjson(event: dict[str, Any]) -> bytes:
    return (json.dumps(event) + "\n").encode("utf-8")


async def _stream_agent_response(agent: Agent, input_items: list[dict[str, Any]]) -> AsyncIterator[bytes]:
    if not os.environ.get("OPENAI_API_KEY"):
        yield _ndjson({"type": "error", "message": "OPENAI_API_KEY is not set in .env."})
        return

    tool_name_by_call_id: dict[str, str] = {}
    try:
        result = Runner.run_streamed(agent, input_items)
        async for event in result.stream_events():
            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                yield _ndjson({"type": "delta", "text": event.data.delta})
            elif event.type == "run_item_stream_event":
                item = event.item
                if event.name == "tool_called":
                    name = getattr(item, "tool_name", None) or "tool"
                    call_id = getattr(item, "call_id", None)
                    if call_id:
                        tool_name_by_call_id[call_id] = name
                    yield _ndjson({"type": "tool_call", "name": name})
                elif event.name == "tool_output":
                    call_id = getattr(item, "call_id", None)
                    name = tool_name_by_call_id.get(call_id, "tool")
                    yield _ndjson({"type": "tool_output", "name": name})
        yield _ndjson({"type": "history", "items": result.to_input_list()})
    except Exception as e:
        yield _ndjson({"type": "error", "message": str(e)})


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    agent: Agent = request.app.state.agent
    input_items = [*req.history, build_user_message(req.message, req.image)]
    return StreamingResponse(_stream_agent_response(agent, input_items), media_type="application/x-ndjson")


def _open_browser_when_ready(url: str) -> None:
    time.sleep(1.0)
    webbrowser.open(url)


def main() -> None:
    import uvicorn

    host = os.environ.get("WEBUI_HOST", DEFAULT_HOST)
    port = int(os.environ.get("WEBUI_PORT", DEFAULT_PORT))
    url = f"http://{host}:{port}"

    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    print(f"Starting PFMEA web UI at {url}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
