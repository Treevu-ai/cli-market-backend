"""E2E checkout flow tests — carrito → orden → pago → confirmación.

Covers the full loop that real users execute, with all external connectors
(Yape QR, PayPal, webhook) mocked at the boundary. Each test verifies the
database state at the end, not just the HTTP response.

Flows covered:
  1. Yape happy path: cart → /checkout/yape → QR returned → /checkout/webhook → paid, cart empty
  2. Yape blocked on free tier
  3. Yape blocked on empty cart
  4. Yape blocked when snapshot price drifted
  5. Checkout webhook idempotency: second call returns duplicate, status unchanged
  6. PayPal happy path: cart → /checkout/paypal → approve_url → /checkout/paypal/capture → paid
  7. PayPal missing credentials → 501 (not a crash)
  8. Cart is cleared atomically when order is created (not only when paid)
  9. Large cart (10 items) completes without error
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ─── Helpers ────────────────────────────────────────────────────────────────


def _product(product_id="p1", price=4.5, store="wong"):
    return {
        "id": product_id,
        "product_id": product_id,
        "name": f"Producto {product_id}",
        "brand": "Gloria",
        "price": price,
        "store": store,
        "store_name": store.capitalize(),
        "currency": "PEN",
        "line": "supermercados",
        "line_name": "Supermercados",
        "category": "lacteos",
        "stock": 10,
        "url": f"http://example.com/{product_id}",
    }


def _setup_pro_cart(market_core, username="buyer", n_items=1, price=4.5):
    """Create a pro user with API key and a cart with n_items identical products."""
    from market_core import db_create_api_key, db_set_subscription

    market_core.ensure_db_initialized()
    db_set_subscription(username, "pro")
    key_rec = db_create_api_key(username, scopes="read", label="e2e")
    api_key = key_rec["key"]

    for i in range(n_items):
        pid = f"p{i+1}" if n_items > 1 else "p1"
        market_core.save_price_snapshot(_product(product_id=pid, price=price))
        market_core.db_add_to_cart(
            username, pid, f"Producto {pid}", price, "wong", "Wong", 1, "http://x"
        )

    return api_key


@pytest.fixture
def checkout_client(isolated_db, monkeypatch):
    """TestClient + isolated DB with checkout env vars configured."""
    from fastapi.testclient import TestClient
    from market_server import app

    market_core = isolated_db
    market_core.ensure_db_initialized()
    monkeypatch.setenv("CHECKOUT_MAX_SNAPSHOT_AGE_SEC", "900")
    monkeypatch.setenv("CHECKOUT_MAX_PRICE_DRIFT_PCT", "3.0")
    monkeypatch.setenv("CHECKOUT_REQUIRE_INDEX_LINK", "0")
    monkeypatch.setenv("CHECKOUT_WEBHOOK_SECRET", "")
    return TestClient(app), market_core


# ─── 1. Yape happy path ──────────────────────────────────────────────────────


class TestYapeHappyPath:
    def test_yape_returns_order_and_qr(self, checkout_client):
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
        assert r.status_code == 200, r.text
        body = r.json()

        assert "order_id" in body
        assert body["order_id"].startswith("ORD-")
        assert body["payment_method"] == "yape"
        assert body["status"] == "pending"
        assert body["total"] == 4.5
        assert body["currency"] == "PEN"
        assert "qr_url" in body

    def test_yape_clears_cart_after_order(self, checkout_client):
        """Cart must be empty immediately after checkout_yape (not only after payment)."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})

        cart = mc.db_get_cart("buyer")
        assert cart == [] or cart is None, (
            "Cart must be cleared when the order is created, not only when paid"
        )

    def test_yape_then_webhook_marks_order_paid(self, checkout_client):
        """Full loop: yape → pending order → generic webhook → paid."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
        assert r.status_code == 200
        order_id = r.json()["order_id"]

        wh = client.post(f"/checkout/webhook?order_id={order_id}&status=paid")
        assert wh.status_code == 200
        assert wh.json()["status"] == "paid"

        from market_core import db_find_order_by_id

        order = db_find_order_by_id(order_id)
        assert order is not None
        assert order["status"] == "paid", (
            f"Expected order status='paid', got {order['status']!r}"
        )

    def test_yape_then_webhook_failed_marks_order_failed(self, checkout_client):
        """Webhook with status=failed must update the order accordingly."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
        order_id = r.json()["order_id"]

        wh = client.post(f"/checkout/webhook?order_id={order_id}&status=failed")
        assert wh.status_code == 200

        from market_core import db_find_order_by_id

        order = db_find_order_by_id(order_id)
        assert order["status"] == "failed"

    def test_checkout_webhook_idempotency(self, checkout_client):
        """Same webhook event twice must be idempotent — second call returns duplicate."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
        order_id = r.json()["order_id"]

        wh1 = client.post(f"/checkout/webhook?order_id={order_id}&status=paid")
        wh2 = client.post(f"/checkout/webhook?order_id={order_id}&status=paid")

        assert wh1.status_code == 200
        assert wh2.status_code == 200
        assert wh2.json().get("duplicate") is True, (
            "Second webhook call must be detected as duplicate and be a no-op"
        )

        from market_core import db_find_order_by_id

        assert db_find_order_by_id(order_id)["status"] == "paid"

    def test_checkout_webhook_unknown_order_returns_404(self, checkout_client):
        client, mc = checkout_client
        mc.ensure_db_initialized()

        r = client.post("/checkout/webhook?order_id=ORD-FFFFFFFF&status=paid")
        assert r.status_code == 404


# ─── 2. Yape failure gates ───────────────────────────────────────────────────


class TestYapeGates:
    def test_yape_blocked_on_free_tier(self, checkout_client, monkeypatch):
        """Free users cannot initiate checkout."""
        client, mc = checkout_client
        mc.ensure_db_initialized()
        from market_core import db_create_api_key

        mc.db_set_subscription("free_user", "free")
        key = db_create_api_key("free_user", scopes="read", label="e2e")["key"]

        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code in (402, 403, 409), (
            f"Free user must be blocked from checkout, got {r.status_code}"
        )

    def test_yape_blocked_on_empty_cart(self, checkout_client):
        """Checkout with an empty cart must return 400."""
        client, mc = checkout_client
        mc.ensure_db_initialized()
        from market_core import db_create_api_key, db_set_subscription

        db_set_subscription("empty_cart_user", "pro")
        key = db_create_api_key("empty_cart_user", scopes="read", label="e2e")["key"]

        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 400

    def test_yape_blocked_on_price_drift(self, checkout_client, monkeypatch):
        """Snapshot price far from cart price must return 409."""
        client, mc = checkout_client
        mc.ensure_db_initialized()
        from market_core import db_create_api_key, db_set_subscription

        db_set_subscription("drift_user", "pro")
        key = db_create_api_key("drift_user", scopes="read", label="e2e")["key"]

        # Snapshot at 12.0 but cart item has price 4.5 → drift = 167%
        mc.save_price_snapshot(_product(price=12.0))
        mc.db_add_to_cart("drift_user", "p1", "Leche", 4.5, "wong", "Wong", 1, "")

        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 409, (
            f"Price drift must block checkout with 409, got {r.status_code}"
        )
        detail = r.json().get("detail", {})
        assert detail.get("ok") is False
        assert detail.get("error") == "price_stale_or_drift"

    def test_yape_no_auth_rejected(self, checkout_client):
        client, _ = checkout_client
        r = client.post("/checkout/yape")
        assert r.status_code in (401, 403)


# ─── 3. Large cart ───────────────────────────────────────────────────────────


class TestLargeCart:
    def test_yape_10_item_cart(self, checkout_client):
        """10-item cart must complete without crash — order total must be sum of items."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc, n_items=10, price=5.0)

        r = client.post("/checkout/yape", headers={"Authorization": f"Bearer {api_key}"})
        assert r.status_code == 200, r.text
        assert r.json()["total"] == pytest.approx(50.0, abs=0.01), (
            f"Expected total=50.0 for 10×5.0, got {r.json()['total']}"
        )


# ─── 4. PayPal happy path ────────────────────────────────────────────────────


class TestPayPalHappyPath:
    def test_paypal_returns_approve_url(self, checkout_client, monkeypatch):
        """POST /checkout/paypal must return approve_url when connector is configured."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        fake_pp_order_id = f"PP-{uuid.uuid4().hex[:12].upper()}"

        async def _fake_create_order(amount, currency, ref):
            return {
                "order_id": fake_pp_order_id,
                "approve_url": f"https://sandbox.paypal.com/checkoutnow?token={fake_pp_order_id}",
            }

        import market_connectors.paypal_payments as pp_mod

        monkeypatch.setattr(pp_mod, "create_order", _fake_create_order)

        r = client.post("/checkout/paypal", headers={"Authorization": f"Bearer {api_key}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "approve_url" in body
        assert body["paypal_order_id"] == fake_pp_order_id
        assert body["status"] == "pending"

    def test_paypal_capture_marks_order_paid(self, checkout_client, monkeypatch):
        """After buyer approves, /checkout/paypal/capture must mark the order paid."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        fake_pp_order_id = f"PP-{uuid.uuid4().hex[:12].upper()}"

        async def _fake_create_order(amount, currency, ref):
            return {"order_id": fake_pp_order_id, "approve_url": "https://paypal.com/fake"}

        async def _fake_capture(pp_order_id):
            return {"ok": True, "status": "COMPLETED"}

        import market_connectors.paypal_payments as pp_mod

        monkeypatch.setattr(pp_mod, "create_order", _fake_create_order)
        monkeypatch.setattr(pp_mod, "capture_order", _fake_capture)

        # Step 1: create order
        r = client.post("/checkout/paypal", headers={"Authorization": f"Bearer {api_key}"})
        assert r.status_code == 200
        market_order_id = r.json()["order_id"]

        # Step 2: capture (buyer returned from PayPal)
        r2 = client.post(
            f"/checkout/paypal/capture?paypal_order_id={fake_pp_order_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["ok"] is True

        from market_core import db_find_order_by_id

        order = db_find_order_by_id(market_order_id)
        assert order is not None
        assert order["status"] == "paid", (
            f"Expected order paid after PayPal capture, got {order['status']!r}"
        )

    def test_paypal_missing_credentials_returns_501(self, checkout_client, monkeypatch):
        """PayPal not configured must return 501, not crash with 500."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        async def _raise_value_error(*a, **kw):
            raise ValueError("PAYPAL_CLIENT_ID not set")

        import market_connectors.paypal_payments as pp_mod

        monkeypatch.setattr(pp_mod, "create_order", _raise_value_error)

        r = client.post("/checkout/paypal", headers={"Authorization": f"Bearer {api_key}"})
        assert r.status_code == 501, (
            f"Missing PayPal credentials must return 501, got {r.status_code}"
        )

    def test_paypal_gateway_error_returns_502(self, checkout_client, monkeypatch):
        """PayPal returning an error dict (no approve_url) must return 502."""
        client, mc = checkout_client
        api_key = _setup_pro_cart(mc)

        async def _fake_error(*a, **kw):
            return {"error": "INTERNAL_SERVER_ERROR", "message": "PayPal down"}

        import market_connectors.paypal_payments as pp_mod

        monkeypatch.setattr(pp_mod, "create_order", _fake_error)

        r = client.post("/checkout/paypal", headers={"Authorization": f"Bearer {api_key}"})
        assert r.status_code == 502
