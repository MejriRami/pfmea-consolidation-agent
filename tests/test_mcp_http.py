"""
Tests for mcp_server/server.py's HTTP transport (TokenAuthMiddleware,
build_http_app, run_http). Two layers:

  - In-process ASGI tests (fast, no real socket) using httpx's
    ASGITransport directly against build_http_app()'s wrapped app --
    covers the token-check logic itself. ASGITransport is async-only
    (no sync handle_request), so these use httpx.AsyncClient via the
    same asyncio.run() wrapper pattern used elsewhere in this repo
    (see tests/test_clustering.py).
  - One real subprocess test that actually launches
    `python -m mcp_server.server --transport http` on a free local port
    and hits it over a real TCP connection, confirming the server starts
    and enforces the token end to end, exactly as ngrok would see it.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from mcp_server.server import TokenAuthMiddleware, build_http_app, mcp, run_http

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_PATH = "/mcp"


def run(coro):
    return asyncio.run(coro)


async def _post(app, headers: dict | None = None) -> httpx.Response:
    # raise_app_exceptions=False: once the token check passes, the request
    # reaches FastMCP's streamable-http session manager, which requires its
    # lifespan/task-group to have been started -- something only a real
    # ASGI server run (uvicorn) does, not ASGITransport. A real server would
    # turn that into a 500 response rather than raise; do the same here so
    # these tests only need to prove the auth layer's behavior, not stand
    # up a full MCP session.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(MCP_PATH, json={}, headers=headers or {})


# --- in-process ASGI tests ---------------------------------------------------

def test_request_without_authorization_header_is_rejected():
    app = build_http_app(token="secret-token")
    resp = run(_post(app))
    assert resp.status_code == 401


def test_request_with_wrong_token_is_rejected():
    app = build_http_app(token="secret-token")
    resp = run(_post(app, {"Authorization": "Bearer wrong-token"}))
    assert resp.status_code == 401


def test_request_with_correct_token_is_let_through_to_the_mcp_app():
    """Not asserting a specific success status -- the underlying
    streamable-http app has its own protocol requirements (proper MCP
    JSON-RPC body/headers) that a bare test request won't satisfy. The
    point is only that the middleware itself stops blocking once the
    token is right, i.e. the response is no longer a 401 from OUR check."""
    app = build_http_app(token="secret-token")
    resp = run(_post(app, {"Authorization": "Bearer secret-token"}))
    assert resp.status_code != 401


def test_non_local_host_header_is_not_rejected_by_dns_rebinding_protection():
    """Regression test: FastMCP's default DNS-rebinding protection only
    allows Host headers matching 127.0.0.1/localhost/[::1] -- which would
    421 every real request through a reverse proxy (ngrok's random
    hostname, Azure App Service, etc.) even with a correct token, since
    neither can be hardcoded into an allowlist ahead of time. server.py
    disables that check (TokenAuthMiddleware is the real access control
    here); this proves a real public-looking hostname completes an actual
    MCP handshake rather than being 421'd before ever reaching it."""
    app = build_http_app(token="secret-token")

    async def go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with mcp.session_manager.run():
            async with httpx.AsyncClient(transport=transport, base_url="https://example-app.azurewebsites.net") as client:
                return await client.post(
                    MCP_PATH,
                    json={
                        "jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"},
                        },
                    },
                    headers={
                        "Authorization": "Bearer secret-token",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )

    resp = run(go())
    assert resp.status_code == 200
    # streamable-http responds as SSE ("event: message\ndata: {...}"), not a
    # bare JSON body -- pull the JSON out of the data: line.
    data_line = next(line for line in resp.text.splitlines() if line.startswith("data: "))
    body = json.loads(data_line.removeprefix("data: "))
    assert body["result"]["serverInfo"]["name"] == "pfmea-consolidation"


def test_no_token_configured_rejects_every_request_fail_closed():
    app = build_http_app(token=None)
    resp = run(_post(app, {"Authorization": "Bearer anything"}))
    assert resp.status_code == 401


def test_build_http_app_reads_mcp_http_token_env_var_when_not_passed_explicitly(monkeypatch):
    monkeypatch.setenv("MCP_HTTP_TOKEN", "env-token")
    app = build_http_app()  # token=None -> falls back to os.environ
    assert isinstance(app, TokenAuthMiddleware)
    assert app.token == "env-token"


def test_run_http_refuses_to_start_without_a_token(monkeypatch):
    monkeypatch.delenv("MCP_HTTP_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="MCP_HTTP_TOKEN"):
        run_http(token=None)


# --- real subprocess: server actually starts and enforces the token ---------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_http_server_actually_starts_and_requires_the_token():
    port = _free_port()
    token = "integration-test-token"
    env = {**os.environ, "MCP_HTTP_TOKEN": token}

    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server", "--transport", "http", "--port", str(port)],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        url = f"http://127.0.0.1:{port}{MCP_PATH}"
        deadline = time.monotonic() + 20
        last_error = None
        connected = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break  # process died -- stop polling, report below
            try:
                httpx.post(url, json={}, timeout=2)
                connected = True
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                last_error = e
                time.sleep(0.3)
        assert connected, (
            f"server never came up on {url}: {last_error}\n"
            f"output:\n{proc.stdout.read() if proc.stdout else ''}"
        )

        no_token_resp = httpx.post(url, json={}, timeout=5)
        assert no_token_resp.status_code == 401

        wrong_token_resp = httpx.post(url, json={}, headers={"Authorization": "Bearer nope"}, timeout=5)
        assert wrong_token_resp.status_code == 401

        right_token_resp = httpx.post(url, json={}, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        assert right_token_resp.status_code != 401
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
