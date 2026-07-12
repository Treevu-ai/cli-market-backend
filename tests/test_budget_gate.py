"""Tests for the opt-in spend-cap gate wired into /checkout/* endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _product(product_id: str = "p1", store: str = "wong", price: float = 4.5, stock: int = 10):
    return {
        "id": product_id,
        "product_id": product_id,
        "name": "Leche Gloria 1L",
        "brand": "Gloria",
        "price": price,
        "store": store,
        "store_name": "Wong",
        "currency": "PEN",
        "line": "supermercados",
        "line_name": "Supermercados",
        "category": "lacteos",
        "stock": stock,
        "url": "http://example.com/p1",
    }


def _setup_pro_user(market_core, username: str = "buyer") -> str:
    from market_core import db_create_api_key, db_set_subscription

    market_core.ensure_db_initialized()
    db_set_subscription(username, "pro")
    key = db_create_api_key(username, scopes="read", label="test")
    return key["key"]


@pytest.fixture
def checkout_env(isolated_db, monkeypatch):
    import pre_checkout_validate as pcv

    market_core = isolated_db
    market_core.ensure_db_initialized()
    monkeypatch.setattr(pcv, "MAX_SNAPSHOT_AGE_SEC", lambda: 900)
    monkeypatch.setattr(pcv, "MAX_PRICE_DRIFT_PCT", lambda: 3.0)
    monkeypatch.setattr(pcv, "REQUIRE_INDEX_LINK", lambda: False)
    monkeypatch.setattr(pcv, "REQUIRE_STOCK", lambda: True)
    monkeypatch.setattr(pcv, "BLOCK_TIER_C", lambda: True)
    return market_core


def test_checkout_yape_succeeds_when_no_budget_set(checkout_env):
    """Backward compat: a user who never set a budget checks out unaffected."""
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)
    market_core.save_price_snapshot(_product(price=4.5))
    market_core.db_add_to_cart("buyer", "p1", "Leche Gloria 1L", 4.5, "wong", "Wong", 1, "http://x")

    with TestClient(app) as client:
        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200
    assert r.json()["total"] == 4.5


def test_checkout_yape_blocked_when_over_budget(checkout_env):
    from fastapi.testclient import TestClient
    from market_server import app
    from market_core.market_billing import db_set_budget

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)
    db_set_budget("buyer", "monthly", 3.0)  # cart total (4.5) exceeds this
    market_core.save_price_snapshot(_product(price=4.5))
    market_core.db_add_to_cart("buyer", "p1", "Leche Gloria 1L", 4.5, "wong", "Wong", 1, "http://x")

    with TestClient(app) as client:
        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "budget_exceeded"
    assert market_core.db_get_cart("buyer"), "cart must remain when budget blocks checkout"


def test_checkout_yape_no_order_row_created_when_budget_blocks(checkout_env):
    """The order must never be persisted at all when the budget gate fires --
    not just returned as an error, actually absent from the DB."""
    from fastapi.testclient import TestClient
    from market_server import app
    from market_core.market_billing import db_set_budget

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)
    db_set_budget("buyer", "monthly", 1.0)
    market_core.save_price_snapshot(_product(price=4.5))
    market_core.db_add_to_cart("buyer", "p1", "Leche Gloria 1L", 4.5, "wong", "Wong", 1, "http://x")

    db = market_core.get_db()
    before = db.execute("SELECT COUNT(*) AS n FROM app_orders WHERE username='buyer'").fetchone()["n"]
    db.close()

    with TestClient(app) as client:
        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 409

    db = market_core.get_db()
    after = db.execute("SELECT COUNT(*) AS n FROM app_orders WHERE username='buyer'").fetchone()["n"]
    db.close()
    assert after == before


def test_checkout_yape_succeeds_when_under_budget(checkout_env):
    from fastapi.testclient import TestClient
    from market_server import app
    from market_core.market_billing import db_set_budget

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)
    db_set_budget("buyer", "monthly", 100.0)
    market_core.save_price_snapshot(_product(price=4.5))
    market_core.db_add_to_cart("buyer", "p1", "Leche Gloria 1L", 4.5, "wong", "Wong", 1, "http://x")

    with TestClient(app) as client:
        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200
    assert not market_core.db_get_cart("buyer"), "cart must clear on a successful checkout"


def test_checkout_budget_endpoints_roundtrip(checkout_env):
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)

    with TestClient(app) as client:
        empty = client.get("/checkout/budget", headers={"Authorization": f"Bearer {api_key}"})
        assert empty.status_code == 200
        assert empty.json()["cap"] is None

        set_resp = client.post(
            "/checkout/budget",
            json={"period": "monthly", "amount": 50.0, "currency": "PEN"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert set_resp.status_code == 200
        assert set_resp.json()["amount"] == 50.0

        after = client.get("/checkout/budget", headers={"Authorization": f"Bearer {api_key}"})
        assert after.json()["cap"] == 50.0


def test_checkout_budget_endpoint_rejects_invalid_period(checkout_env):
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)

    with TestClient(app) as client:
        r = client.post(
            "/checkout/budget",
            json={"period": "weekly", "amount": 50.0},
            headers={"Authorization": f"Bearer {api_key}"},
        )
    assert r.status_code == 422


def test_checkout_yape_idempotent_retry_not_double_counted_against_budget(checkout_env):
    """Retrying the same Idempotency-Key must not be blocked by the budget
    gate re-counting the same order's spend a second time."""
    from fastapi.testclient import TestClient
    from market_server import app
    from market_core.market_billing import db_set_budget

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)
    db_set_budget("buyer", "monthly", 5.0)  # just enough for one 4.5 order, not two
    market_core.save_price_snapshot(_product(price=4.5))
    market_core.db_add_to_cart("buyer", "p1", "Leche Gloria 1L", 4.5, "wong", "Wong", 1, "http://x")

    headers = {"Authorization": f"Bearer {api_key}", "Idempotency-Key": "retry-1"}
    with TestClient(app) as client:
        first = client.post("/checkout/yape", headers=headers)
        assert first.status_code == 200
        # Simulate a client that didn't see the first response (e.g. dropped
        # connection) and resends the same cart + Idempotency-Key.
        market_core.db_add_to_cart("buyer", "p1", "Leche Gloria 1L", 4.5, "wong", "Wong", 1, "http://x")
        second = client.post("/checkout/yape", headers=headers)
    assert second.status_code == 200
    assert second.json()["order_id"] == first.json()["order_id"]
