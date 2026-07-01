"""Regression test for cli-market-backend#132 (T1/O-enrich): /products/enrich
silently returned {"results": [], "total": 0} with a 200 status for a
network failure — indistinguishable from a genuine "OFF has no results",
and the CLI exited 0 believing it had succeeded."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import httpx
import pytest
from fastapi import HTTPException

from routers import search as search_router


def test_enrich_raises_503_on_network_failure(monkeypatch):
    monkeypatch.setattr(search_router, "require_api_key", lambda *_a, **_k: "test-user")

    def _raise(*_a, **_k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(search_router, "request_with_retry", _raise)

    with pytest.raises(HTTPException) as exc_info:
        search_router.enrich_products("leche entera", limit=5, authorization="Bearer test")
    assert exc_info.value.status_code == 503


def test_enrich_sends_search_simple_and_action_params(monkeypatch):
    monkeypatch.setattr(search_router, "require_api_key", lambda *_a, **_k: "test-user")
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"products": [], "count": 0}

    def _fake_request(method, url, params=None, timeout=None):
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(search_router, "request_with_retry", _fake_request)

    result = search_router.enrich_products("leche entera", limit=5, authorization="Bearer test")
    assert result == {"results": [], "total": 0}
    assert captured["params"]["search_simple"] == 1
    assert captured["params"]["action"] == "process"
    assert captured["params"]["search_terms"] == "leche entera"
