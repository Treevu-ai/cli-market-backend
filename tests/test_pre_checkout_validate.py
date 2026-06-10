"""Tests for pre_checkout_validate gate."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _product(product_id: str = "p1", store: str = "wong", price: float = 4.5):
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
        "stock": 10,
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
    monkeypatch.setenv("CHECKOUT_MAX_SNAPSHOT_AGE_SEC", "900")
    monkeypatch.setenv("CHECKOUT_MAX_PRICE_DRIFT_PCT", "3.0")
    monkeypatch.setenv("CHECKOUT_REQUIRE_INDEX_LINK", "0")
    monkeypatch.setattr(pcv, "MAX_SNAPSHOT_AGE_SEC", lambda: 900)
    monkeypatch.setattr(pcv, "MAX_PRICE_DRIFT_PCT", lambda: 3.0)
    monkeypatch.setattr(pcv, "REQUIRE_INDEX_LINK", lambda: False)
    return market_core


def test_validate_ok_when_snapshot_matches_cart(checkout_env):
    import pre_checkout_validate as pcv

    market_core = checkout_env
    market_core.save_price_snapshot(_product(price=4.5))
    cart = [
        {
            "product_id": "p1",
            "name": "Leche Gloria 1L",
            "price": 4.5,
            "store": "wong",
            "store_name": "Wong",
            "quantity": 2,
            "url": "http://example.com/p1",
        }
    ]
    market_core.db_set_subscription("buyer", "pro")

    result = pcv.pre_checkout_validate("buyer", cart)
    assert result.ok is True
    assert result.cart_total == 9.0
    assert result.validated_total == 9.0
    assert result.items[0]["status"] == "ok"
    assert any(t["step"] == "price_freshness" and t["status"] == "ok" for t in result.trace)


def test_validate_fails_on_price_drift(checkout_env):
    import pre_checkout_validate as pcv

    market_core = checkout_env
    market_core.save_price_snapshot(_product(price=10.5))
    cart = [
        {
            "product_id": "p1",
            "name": "Leche Gloria 1L",
            "price": 9.9,
            "store": "wong",
            "quantity": 1,
            "url": "",
        }
    ]
    market_core.db_set_subscription("buyer", "pro")

    result = pcv.pre_checkout_validate("buyer", cart)
    assert result.ok is False
    assert result.error == "price_stale_or_drift"
    assert result.action == "refresh_cart"
    assert result.items[0]["status"] == "drift"


def test_validate_fails_on_stale_snapshot(checkout_env, monkeypatch):
    import pre_checkout_validate as pcv

    market_core = checkout_env
    market_core.save_price_snapshot(_product(price=4.5))
    db = sqlite3.connect(str(market_core.DB_FILE))
    db.execute(
        "UPDATE price_snapshots SET queried_at = datetime('now', '-2 hours') WHERE product_id='p1'"
    )
    db.commit()
    db.close()

    monkeypatch.setattr(pcv, "MAX_SNAPSHOT_AGE_SEC", lambda: 900)

    cart = [{"product_id": "p1", "name": "Leche", "price": 4.5, "store": "wong", "quantity": 1, "url": ""}]
    market_core.db_set_subscription("buyer", "pro")

    result = pcv.pre_checkout_validate("buyer", cart)
    assert result.ok is False
    assert result.items[0]["status"] == "stale"


def test_validate_fails_without_snapshot(checkout_env):
    import pre_checkout_validate as pcv

    checkout_env.ensure_db_initialized()
    checkout_env.db_set_subscription("buyer", "pro")
    cart = [{"product_id": "missing", "name": "X", "price": 1.0, "store": "wong", "quantity": 1, "url": ""}]

    result = pcv.pre_checkout_validate("buyer", cart)
    assert result.ok is False
    assert result.items[0]["status"] == "missing_snapshot"


def test_validate_free_tier_blocked(checkout_env, monkeypatch):
    import pre_checkout_validate as pcv

    checkout_env.ensure_db_initialized()
    monkeypatch.delenv("MARKET_LEGACY_CHECKOUT", raising=False)
    cart = [{"product_id": "p1", "name": "Leche", "price": 4.5, "store": "wong", "quantity": 1, "url": ""}]

    result = pcv.pre_checkout_validate("free-user", cart)
    assert result.ok is False
    assert result.error == "checkout_not_allowed"


def test_checkout_validate_endpoint_ok(checkout_env, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)
    market_core.save_price_snapshot(_product(price=4.5))
    market_core.db_add_to_cart("buyer", "p1", "Leche Gloria 1L", 4.5, "wong", "Wong", 1, "http://x")

    with TestClient(app) as client:
        r = client.post("/checkout/validate", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["validated_total"] == 4.5


def test_checkout_yape_409_when_drift(checkout_env, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = checkout_env
    api_key = _setup_pro_user(market_core)
    market_core.save_price_snapshot(_product(price=12.0))
    market_core.db_add_to_cart("buyer", "p1", "Leche Gloria 1L", 4.5, "wong", "Wong", 1, "http://x")

    with TestClient(app) as client:
        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["ok"] is False
    assert market_core.db_get_cart("buyer"), "cart must remain when validation fails"
