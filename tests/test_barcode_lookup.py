"""Regression tests for cli-market-backend#127 (O6): barcode lookup errors
were generic "not found" with no distinction between invalid format, a
genuine miss, and a network failure — and the CLI suggested irrelevant
`market doctor`/`market hello` for all of them."""

from __future__ import annotations

import httpx
import pytest

from routers import search as search_router


def test_invalid_barcode_format_rejected_before_network_call(monkeypatch):
    def _should_not_call(*_a, **_k):
        raise AssertionError("should not hit the network for an invalid barcode")

    monkeypatch.setattr(search_router, "request_with_retry", _should_not_call)

    result = search_router.barcode_lookup("not-a-barcode")
    assert result["error"] == "invalid barcode format"
    assert result["status"] == 400


def test_barcode_not_found_returns_404_status(monkeypatch):
    class _Resp:
        status_code = 404

        def json(self):
            return {}

    monkeypatch.setattr(search_router, "request_with_retry", lambda *_a, **_k: _Resp())

    result = search_router.barcode_lookup("7790070010158")
    assert result["error"] == "not found"
    assert result["status"] == 404


def test_barcode_network_failure_reports_distinct_error(monkeypatch):
    def _raise(*_a, **_k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(search_router, "request_with_retry", _raise)

    result = search_router.barcode_lookup("7790070010158")
    assert "network error" in result["error"]
    assert result["status"] == 503
