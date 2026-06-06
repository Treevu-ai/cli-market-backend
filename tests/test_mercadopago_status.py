"""Mercado Pago status endpoint."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_mercadopago_status_endpoint(monkeypatch):
    import market_server

    monkeypatch.setenv("MERCADOPAGO_SANDBOX", "true")
    monkeypatch.setenv("MERCADOPAGO_ACCESS_TOKEN_SANDBOX", "APP_USR-test-token")
    client = TestClient(market_server.app)
    r = client.get("/mercadopago-status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["sandbox"] is True
    assert "/checkout/mercadopago" in body["endpoints"]