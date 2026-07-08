"""/mcp must not mislabel non-auth failures (e.g. rate limits) as bad tokens."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.testclient import TestClient

from market_server import app

client = TestClient(app)


def _call_tool(monkeypatch, exc: Exception):
    def _boom(_auth):
        raise exc

    monkeypatch.setattr("routers.mcp_http.require_api_key", _boom)
    return client.post(
        "/mcp?token=sk-whatever",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "market_whoami", "arguments": {}},
        },
    )


def test_rate_limit_reports_429_not_invalid_token(monkeypatch):
    resp = _call_tool(
        monkeypatch,
        HTTPException(
            status_code=429,
            detail="Daily limit reached (15 req/day). Resets in 3h. Upgrade at https://cli-market.dev",
            headers={"Retry-After": "10800"},
        ),
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == -32029
    assert "Daily limit reached" in body["error"]["message"]
    assert resp.headers.get("Retry-After") == "10800"


def test_bad_token_still_reports_401(monkeypatch):
    resp = _call_tool(monkeypatch, HTTPException(status_code=401, detail="Token inválido"))
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == -32001
    assert body["error"]["message"] == "Token inválido"
