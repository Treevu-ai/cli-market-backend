"""
market_connectors/paypal_payments.py — PayPal REST API integration.

Handles PayPal checkout for subscriptions and one-time purchases.
Uses PayPal Orders API v2. Sandbox-ready.
"""

from __future__ import annotations

import base64
import os

import httpx

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_SANDBOX = os.getenv("PAYPAL_SANDBOX", "true").lower() == "true"
PAYPAL_PLAN_ID = os.getenv("PAYPAL_PLAN_ID", "")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")

PAYPAL_API = "https://api-m.sandbox.paypal.com" if PAYPAL_SANDBOX else "https://api-m.paypal.com"


async def _get_access_token() -> str:
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise ValueError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET not configured")
    auth = base64.b64encode(f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()).decode()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{PAYPAL_API}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if resp.status_code == 200:
            return resp.json()["access_token"]
        raise Exception(f"PayPal auth failed: {resp.text}")


async def verify_webhook_signature(headers: dict, event: dict) -> bool:
    """Verify PayPal webhook via REST API. Returns False if webhook ID is not configured."""
    if not PAYPAL_WEBHOOK_ID:
        return False
    token = await _get_access_token()
    body = {
        "auth_algo": headers.get("paypal-auth-algo") or headers.get("PAYPAL-AUTH-ALGO", ""),
        "cert_url": headers.get("paypal-cert-url") or headers.get("PAYPAL-CERT-URL", ""),
        "transmission_id": headers.get("paypal-transmission-id") or headers.get("PAYPAL-TRANSMISSION-ID", ""),
        "transmission_sig": headers.get("paypal-transmission-sig") or headers.get("PAYPAL-TRANSMISSION-SIG", ""),
        "transmission_time": headers.get("paypal-transmission-time") or headers.get("PAYPAL-TRANSMISSION-TIME", ""),
        "webhook_id": PAYPAL_WEBHOOK_ID,
        "webhook_event": event,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{PAYPAL_API}/v1/notifications/verify-webhook-signature",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            return False
        return resp.json().get("verification_status") == "SUCCESS"


async def create_order(
    amount: float,
    currency: str = "USD",
    reference: str = "",
    return_url: str = "https://cli-market.dev?order=success",
    cancel_url: str = "https://cli-market.dev?order=cancelled",
) -> dict:
    """Create a PayPal order and return the approval link."""
    token = await _get_access_token()
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
            "reference_id": reference,
            "description": f"CLI Market — {reference}",
        }],
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
            "brand_name": "CLI Market",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{PAYPAL_API}/v2/checkout/orders",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            approve_link = next(
                (l["href"] for l in data.get("links", []) if l["rel"] == "approve"),
                None,
            )
            return {"order_id": data["id"], "status": data["status"], "approve_url": approve_link}
        return {"error": f"PayPal order failed: {resp.text}", "status": resp.status_code}


async def capture_order(paypal_order_id: str) -> dict:
    """Capture funds after buyer approval."""
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{PAYPAL_API}/v2/checkout/orders/{paypal_order_id}/capture",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return {"ok": True, "status": data.get("status"), "paypal_order_id": paypal_order_id}
        return {"ok": False, "error": resp.text, "status": resp.status_code}


async def _ensure_billing_plan(token: str, client: httpx.AsyncClient, amount: float, currency: str) -> str:
    if PAYPAL_PLAN_ID:
        return PAYPAL_PLAN_ID
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    p1 = await client.post(
        f"{PAYPAL_API}/v1/catalogs/products",
        json={
            "name": "CLI Market Pro",
            "type": "SERVICE",
            "description": "Monthly subscription — 10,000 req/day, checkout, export",
        },
        headers=h,
    )
    if p1.status_code not in (200, 201):
        raise RuntimeError(f"Product failed: {p1.text}")
    product_id = p1.json()["id"]
    p2 = await client.post(
        f"{PAYPAL_API}/v1/billing/plans",
        json={
            "product_id": product_id,
            "name": "CLI Market Pro Monthly",
            "description": "$49/month",
            "status": "ACTIVE",
            "billing_cycles": [{
                "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                "tenure_type": "REGULAR",
                "sequence": 1,
                "total_cycles": 0,
                "pricing_scheme": {
                    "fixed_price": {"value": f"{amount:.2f}", "currency_code": currency},
                },
            }],
            "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 3},
        },
        headers=h,
    )
    if p2.status_code not in (200, 201):
        raise RuntimeError(f"Plan failed: {p2.text}")
    return p2.json()["id"]


async def create_subscription(
    username: str,
    amount: float = 49.0,
    currency: str = "USD",
    return_url: str = "https://cli-market.dev?sub=success",
    cancel_url: str = "https://cli-market.dev?sub=cancelled",
) -> dict:
    """Create a PayPal subscription (Pro plan $49/mo)."""
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        plan_id = await _ensure_billing_plan(token, client, amount, currency)
        p3 = await client.post(
            f"{PAYPAL_API}/v1/billing/subscriptions",
            json={
                "plan_id": plan_id,
                "custom_id": username,
                "application_context": {
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                    "brand_name": "CLI Market",
                    "user_action": "SUBSCRIBE_NOW",
                    "shipping_preference": "NO_SHIPPING",
                },
            },
            headers=h,
        )
        if p3.status_code in (200, 201):
            data = p3.json()
            approve_link = next(
                (l["href"] for l in data.get("links", []) if l["rel"] == "approve"),
                None,
            )
            return {
                "subscription_id": data["id"],
                "status": data["status"],
                "approve_url": approve_link,
                "plan_id": plan_id,
            }
        return {"error": f"Subscription failed: {p3.text}"}


WEBHOOK_EVENT_TYPES = [
    "BILLING.SUBSCRIPTION.ACTIVATED",
    "BILLING.SUBSCRIPTION.CANCELLED",
    "BILLING.SUBSCRIPTION.EXPIRED",
    "BILLING.SUBSCRIPTION.SUSPENDED",
    "CHECKOUT.ORDER.APPROVED",
    "CHECKOUT.ORDER.COMPLETED",
    "PAYMENT.CAPTURE.COMPLETED",
]


async def create_pro_plan(amount: float = 49.0, currency: str = "USD") -> dict:
    """Create catalog product + billing plan. Returns {product_id, plan_id}."""
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        plan_id = await _ensure_billing_plan(token, client, amount, currency)
    return {"plan_id": plan_id, "amount": amount, "currency": currency}


async def register_webhook(url: str) -> dict:
    """Register PayPal REST webhook for CLI Market events."""
    token = await _get_access_token()
    body = {
        "url": url,
        "event_types": [{"name": n} for n in WEBHOOK_EVENT_TYPES],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{PAYPAL_API}/v1/notifications/webhooks",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return {"webhook_id": data["id"], "url": url, "event_types": WEBHOOK_EVENT_TYPES}
        return {"error": resp.text, "status": resp.status_code}


async def list_webhooks() -> dict:
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{PAYPAL_API}/v1/notifications/webhooks",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 200:
            return {"webhooks": resp.json().get("webhooks", [])}
        return {"error": resp.text, "status": resp.status_code}


async def check_connection() -> dict:
    """Verify credentials and return environment info."""
    token = await _get_access_token()
    return {
        "ok": True,
        "sandbox": PAYPAL_SANDBOX,
        "api_url": PAYPAL_API,
        "token_preview": f"{token[:8]}...",
        "plan_id_env": PAYPAL_PLAN_ID or None,
        "webhook_id_env": PAYPAL_WEBHOOK_ID or None,
    }
