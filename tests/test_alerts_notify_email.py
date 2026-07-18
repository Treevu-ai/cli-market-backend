"""notify_email must be locked to the caller's own verified account email.

Regression test for the SMTP-relay finding: a Pro+ user could previously set
notify_email to any third-party address and use price alerts to fire emails
from CLI Market's sender against arbitrary inboxes (harassment/phishing risk).

Also covers the follow-up finding from security review: the first fix
compared notify_email against db_get_user_email()/subscription_requests,
which is writable by the caller's own POST /billing/request-pro call (just
an intent record, no OTP) — that would have let an attacker "verify" any
address against themselves and defeat the check. The fix now compares
against app_users.email, which is set once at OTP-verified registration
(auth.py verify_email) and never overwritten by billing endpoints.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_pro_user(market_core, username="alerts-user", email="owner@example.com"):
    """Mirror the real registration path: app_users.email set once, OTP-verified."""
    from market_core import db_create_api_key, db_save_user, db_set_subscription

    market_core.ensure_db_initialized()
    db_save_user(username, "salt:deadbeef", None, email)
    db_set_subscription(username, "pro")
    key_rec = db_create_api_key(username, scopes="read", label="e2e")
    return key_rec["key"]


@pytest.fixture
def alerts_client(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = isolated_db
    market_core.ensure_db_initialized()
    return TestClient(app), market_core


def _create_alert_body(notify_email: str = "") -> dict:
    return {
        "condition": "price_drop",
        "product_query": "leche gloria",
        "threshold_pct": 5.0,
        "notify_email": notify_email,
    }


class TestNotifyEmailRestriction:
    def test_notify_email_matching_account_is_accepted(self, alerts_client):
        client, mc = alerts_client
        api_key = _setup_pro_user(mc, email="owner@example.com")

        r = client.post(
            "/v1/alerts",
            json=_create_alert_body("owner@example.com"),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["alert"]["notify_email"] == "owner@example.com"

    def test_notify_email_for_third_party_is_rejected(self, alerts_client):
        client, mc = alerts_client
        api_key = _setup_pro_user(mc, email="owner@example.com")

        r = client.post(
            "/v1/alerts",
            json=_create_alert_body("victim@example.com"),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 403
        assert "verified email" in r.json()["detail"]

    def test_notify_email_blank_is_allowed(self, alerts_client):
        client, mc = alerts_client
        api_key = _setup_pro_user(mc, email="owner@example.com")

        r = client.post(
            "/v1/alerts",
            json=_create_alert_body(""),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["alert"]["notify_email"] == ""

    def test_request_pro_intent_cannot_poison_notify_email_check(self, alerts_client, monkeypatch):
        """POST /billing/request-pro only writes subscription_requests, never
        app_users — calling it with a victim's email must NOT let a
        subsequent /v1/alerts call claim that email as "verified"."""
        client, mc = alerts_client
        api_key = _setup_pro_user(mc, email="owner@example.com")

        # Avoid real SMTP send attempts in the request-pro flow.
        import market_connectors.email_outbound as email_outbound
        monkeypatch.setattr(email_outbound, "_smtp_configured", lambda: False)

        r = client.post(
            "/billing/request-pro",
            json={"email": "victim@example.com"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 200, r.text

        r2 = client.post(
            "/v1/alerts",
            json=_create_alert_body("victim@example.com"),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r2.status_code == 403, r2.text
        assert "verified email" in r2.json()["detail"]

    def test_billing_paypal_body_email_cannot_overwrite_existing_account_email(
        self, alerts_client, monkeypatch
    ):
        """POST /billing/paypal must not let a client-supplied email
        overwrite an already-registered account's app_users.email — that
        would also poison the notify_email trust anchor."""
        client, mc = alerts_client
        api_key = _setup_pro_user(mc, email="owner@example.com")

        import market_connectors.paypal_payments as paypal_payments

        async def _fake_create_subscription(**kwargs):
            return {"subscription_id": "sub-1", "approve_url": "https://paypal.example/approve"}

        monkeypatch.setattr(paypal_payments, "create_subscription", _fake_create_subscription)

        r = client.post(
            "/billing/paypal",
            json={"plan": "pro", "email": "victim@example.com"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r.status_code == 200, r.text

        from market_core import db_get_users

        assert db_get_users()["alerts-user"]["email"] == "owner@example.com"
