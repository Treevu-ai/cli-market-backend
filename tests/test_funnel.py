"""P3 funnel instrumentation + PAM tier 1.5 synthetic journey."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from market_core import ensure_db_initialized
from market_funnel import ensure_funnel_schema, funnel_summary, record_funnel_event
from market_server import app

ensure_db_initialized()
ensure_funnel_schema()
client = TestClient(app)


def test_record_and_summary():
    record_funnel_event("register", username="u-funnel-1", dedupe=True)
    record_funnel_event("first_search", username="u-funnel-1", dedupe=True)
    record_funnel_event("request_pro", username="u-funnel-1")
    record_funnel_event("activated", username="u-funnel-1", dedupe=True)
    data = funnel_summary(days=30)
    assert data["events"]["register"] >= 1
    assert len(data["funnel_steps"]) >= 3


def test_v1_events_endpoint():
    r = client.post("/v1/events", json={"event": "install", "session_id": "test-sess"})
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_analytics_funnel_public():
    r = client.get("/analytics/funnel")
    assert r.status_code == 200
    body = r.json()
    assert "funnel_steps" in body
    assert "ttfv_median_minutes" in body


def test_pam_journey_synthetic():
    """PAM tier 1.5 — market init path: register → whoami → search → account."""
    reg = client.post("/auth/register")
    assert reg.status_code == 200
    key = reg.json()["api_key"]
    headers = {"Authorization": f"Bearer {key}"}

    who = client.get("/auth/whoami", headers=headers)
    assert who.status_code == 200

    search = client.post(
        "/products/search",
        json={"query": "leche", "limit": 3, "country": "PE"},
        headers=headers,
    )
    assert search.status_code == 200

    acct = client.get("/auth/account?lang=en", headers=headers)
    assert acct.status_code == 200
    body = acct.json()
    assert body["tier"] == "free"
    assert "usage" in body
    assert body["upgrade"]["next_tier"] == "starter"