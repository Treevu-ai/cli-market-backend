"""notify_webhook must be Enterprise-only and SSRF-validated.

Regression test: the docstring in routers/alerts.py documented
"notify_webhook (Enterprise)" but the endpoint never enforced it — any Pro
caller could set an arbitrary webhook URL, and market_alerts._send_webhook
does an unrestricted httpx.post(url, ...) when the alert fires (SSRF vector,
e.g. targeting cloud metadata endpoints or internal services).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_user(market_core, username="webhook-user", tier="pro"):
    from market_core import db_create_api_key, db_set_subscription

    market_core.ensure_db_initialized()
    db_set_subscription(username, tier)
    key_rec = db_create_api_key(username, scopes="read", label="e2e")
    return key_rec["key"]


@pytest.fixture
def alerts_client(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = isolated_db
    market_core.ensure_db_initialized()
    return TestClient(app), market_core


def _create_alert_body(notify_webhook: str = "") -> dict:
    return {
        "condition": "price_drop",
        "product_query": "leche gloria",
        "threshold_pct": 5.0,
        "notify_webhook": notify_webhook,
    }


class TestNotifyWebhookRestriction:
    def test_notify_webhook_rejected_for_pro_tier(self, alerts_client):
        client, mc = alerts_client
        api_key = _setup_user(mc, tier="pro")

        r = client.post(
            "/v1/alerts",
            json=_create_alert_body("https://example.com/hook"),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 403
        assert "Enterprise" in r.json()["detail"]

    def test_notify_webhook_accepted_for_enterprise_tier(self, alerts_client, monkeypatch):
        client, mc = alerts_client
        api_key = _setup_user(mc, tier="enterprise")

        import routers.alerts as alerts_module
        monkeypatch.setattr(
            alerts_module, "validate_public_http_url", lambda u: u
        )

        r = client.post(
            "/v1/alerts",
            json=_create_alert_body("https://example.com/hook"),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["alert"]["notify_webhook"] == "https://example.com/hook"

    def test_notify_webhook_blocks_private_network_target(self, alerts_client):
        """Enterprise tier does not bypass SSRF validation."""
        client, mc = alerts_client
        api_key = _setup_user(mc, tier="enterprise")

        r = client.post(
            "/v1/alerts",
            json=_create_alert_body("http://localhost:8080/hook"),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 400

    def test_blank_notify_webhook_is_allowed_for_any_tier(self, alerts_client):
        client, mc = alerts_client
        api_key = _setup_user(mc, tier="pro")

        r = client.post(
            "/v1/alerts",
            json=_create_alert_body(""),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["alert"]["notify_webhook"] == ""
