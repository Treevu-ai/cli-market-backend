"""P3 funnel instrumentation + PAM tier 1.5 synthetic journey."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from market_core import ensure_db_initialized
from market_funnel import (
    dropoff_summary,
    ensure_funnel_schema,
    funnel_summary,
    is_test_funnel_traffic,
    record_funnel_event,
)
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
    assert "excluded_test_events" in body


def test_is_test_funnel_traffic_patterns():
    assert is_test_funnel_traffic("smoke+123", {"email": "smoke+123@cli-market.dev"})
    assert is_test_funnel_traffic("pam-user", {"email": "pam+abc@cli-market.dev"})
    assert is_test_funnel_traffic("test", {"email": "test@example.com"})
    assert is_test_funnel_traffic("user-deadbeefcafe", None)
    assert not is_test_funnel_traffic("acme-buyer", {"email": "buyer@acme.com"})


def test_funnel_summary_excludes_test_traffic():
    suffix = uuid.uuid4().hex[:8]
    smoke_user = f"smoke+{suffix}"
    real_user = f"buyer-{suffix}"
    record_funnel_event(
        "request_pro",
        username=smoke_user,
        meta={"email": f"{smoke_user}@cli-market.dev"},
    )
    record_funnel_event(
        "request_pro",
        username=real_user,
        meta={"email": f"{real_user}@acme.com"},
    )
    filtered = funnel_summary(days=30, include_test=False)
    raw = funnel_summary(days=30, include_test=True)
    assert raw["events"]["request_pro"] - filtered["events"]["request_pro"] >= 1
    assert filtered["excluded_test_events"] >= 1
    assert raw["excluded_test_events"] == 0


def test_cli_dropoff_events_accepted():
    """cli_command_attempted / cli_command_result / cli_auth_wall_hit must pass validation."""
    sid = f"test-sess-{uuid.uuid4().hex[:8]}"
    for event, meta in [
        ("cli_command_attempted", {"command": "search", "cli_version": "0.1.0", "platform": "Linux"}),
        ("cli_command_result", {"command": "search", "success": True, "elapsed_ms": 420}),
        ("cli_auth_wall_hit", {"command": "account", "registered": False}),
    ]:
        r = client.post("/v1/events", json={"event": event, "session_id": sid, "meta": meta})
        assert r.status_code == 200, f"{event} rejected: {r.text}"
        assert r.json().get("ok") is True


def test_dropoff_summary_and_endpoint():
    sid = f"drop-{uuid.uuid4().hex[:8]}"
    record_funnel_event("cli_command_attempted", session_id=sid, meta={"command": "search"})
    record_funnel_event("cli_auth_wall_hit", session_id=sid, meta={"command": "search"})
    record_funnel_event("cli_command_result", session_id=sid, meta={"command": "search", "success": False})

    data = dropoff_summary(days=30, include_test=True)
    assert data["cli_sessions"]["attempted"] >= 1
    assert data["cli_sessions"]["hit_auth_wall"] >= 1
    assert "command_distribution" in data
    assert "auth_wall_by_command" in data
    assert "command_results" in data

    r = client.get("/analytics/dropoff")
    assert r.status_code == 200
    body = r.json()
    assert "cli_sessions" in body
    assert "command_distribution" in body


def test_pam_journey_synthetic():
    """PAM tier 1.5 — market init path: register → whoami → search → account."""
    from tests.auth_test_utils import complete_registration

    reg = complete_registration(client)
    key = reg["api_key"]
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