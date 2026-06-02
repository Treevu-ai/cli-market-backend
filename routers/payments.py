"""Payment-gateway-specific checkout endpoints.

Each endpoint:
  1. Creates an order in `pending` status.
  2. Calls the relevant payment connector (Lemon, PayPal, Wise, Stripe).
  3. Returns the gateway's approve/redirect URL or QR data.
  4. Webhooks mark orders paid or upgrade subscription tier.

Endpoints:
  POST /checkout/yape       Yape/Plin QR (Peru)
  POST /checkout/lemon      Lemon Cash checkout URL (Argentina)
  POST /checkout/paypal     PayPal approval URL
  POST /checkout/wise       Wise pay-link + QR
  POST /checkout/paypal/capture  Capture approved PayPal order (return URL)
  POST /checkout/webhook    Generic mark order paid/failed
  POST /checkout/paypal-webhook  PayPal IPN/webhooks
  GET  /checkout/rates      FX rates with PEN base (Wise; fallback if down)
  POST /billing/request-pro  Email payment link (manual Pro — default)
  POST /billing/paypal      PayPal Subscription (optional automation)
  POST /billing/checkout    Stripe Checkout for Pro subscription
  GET  /paypal-status       PayPal config diagnostic
"""

from __future__ import annotations

import logging
import os
import re
import uuid

from fastapi import APIRouter, Header, HTTPException, Request

from market_core import (
    db_delete_billing_pending,
    db_find_order_by_gateway_ref,
    db_find_order_by_id,
    db_get_billing_pending,
    db_create_subscription_request,
    db_mark_subscription_request_emailed,
    db_recent_subscription_request,
    db_save_billing_pending,
    db_set_order_gateway_ref,
    db_set_subscription,
    db_update_order_status,
    db_clear_cart,
    db_create_order,
    db_get_cart,
)
from market_security import is_production_deploy, paypal_allow_unverified_webhooks
from server_deps import check_rate_limit, require_checkout_access, require_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payments"])

_ORDER_REF_RE = re.compile(r"CLI-Market-(ORD-[A-F0-9]+)", re.I)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _cart_total(cart: list[dict]) -> float:
    return round(sum(i["price"] * i["quantity"] for i in cart), 2)


def _prepare_pending_order(username: str, method: str) -> tuple[list[dict], float, str]:
    """Common preamble: get cart, compute total, create pending order, clear cart."""
    require_checkout_access(username)
    cart = db_get_cart(username)
    if not cart:
        raise HTTPException(status_code=400, detail="Carrito vacío")
    total = _cart_total(cart)
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    db_create_order(username, cart, method, total, status="pending", order_id=order_id)
    db_clear_cart(username)
    return cart, total, order_id


def _parse_market_order_ref(resource: dict) -> str | None:
    """Extract ORD-xxx from PayPal purchase unit reference_id."""
    units = resource.get("purchase_units") or []
    for unit in units:
        ref = unit.get("reference_id") or unit.get("custom_id") or ""
        m = _ORDER_REF_RE.search(ref)
        if m:
            return m.group(1).upper()
    ref = resource.get("custom_id") or ""
    m = _ORDER_REF_RE.search(ref)
    return m.group(1).upper() if m else None


async def _handle_paypal_event(event: dict) -> dict:
    event_type = event.get("event_type", "")
    resource = event.get("resource") or {}
    actions: list[str] = []

    if event_type == "CHECKOUT.ORDER.APPROVED":
        paypal_order_id = resource.get("id", "")
        if paypal_order_id:
            from market_connectors.paypal_payments import capture_order

            cap = await capture_order(paypal_order_id)
            actions.append(f"capture:{cap.get('status', cap.get('error', 'err'))}")
            market_order_id = _parse_market_order_ref(resource)
            if market_order_id:
                db_set_order_gateway_ref(market_order_id, paypal_order_id)

    elif event_type in ("PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.COMPLETED"):
        paypal_order_id = (
            resource.get("supplementary_data", {})
            .get("related_ids", {})
            .get("order_id")
            or resource.get("id", "")
        )
        order_row = db_find_order_by_gateway_ref(paypal_order_id)
        if not order_row:
            market_order_id = _parse_market_order_ref(resource)
            if market_order_id:
                order_row = db_find_order_by_id(market_order_id)
                if order_row and paypal_order_id:
                    db_set_order_gateway_ref(market_order_id, paypal_order_id)
        if order_row:
            db_update_order_status(order_row["order_id"], "paid")
            actions.append(f"order_paid:{order_row['order_id']}")
        else:
            actions.append(f"order_not_found:{paypal_order_id}")

    elif event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        sub_id = resource.get("id", "")
        username = resource.get("custom_id") or ""
        if not username and sub_id:
            pending = db_get_billing_pending(sub_id)
            username = (pending or {}).get("username", "")
        if username:
            db_set_subscription(username, "pro", paypal_subscription_id=sub_id)
            if sub_id:
                db_delete_billing_pending(sub_id)
            actions.append(f"pro_activated:{username}")
        else:
            actions.append(f"subscription_no_user:{sub_id}")

    elif event_type in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.EXPIRED",
                        "BILLING.SUBSCRIPTION.SUSPENDED"):
        sub_id = resource.get("id", "")
        username = resource.get("custom_id") or ""
        if not username and sub_id:
            pending = db_get_billing_pending(sub_id)
            username = (pending or {}).get("username", "")
        if username:
            db_set_subscription(username, "free", paypal_subscription_id="")
            if sub_id:
                db_delete_billing_pending(sub_id)
            actions.append(f"downgraded:{username}")

    return {"event_type": event_type, "actions": actions}


@router.post("/checkout/yape")
def checkout_yape(authorization: str | None = Header(None)):
    username = require_user(authorization)
    _, total, order_id = _prepare_pending_order(username, "yape")
    yape_number = os.getenv("YAPE_PLIN_NUMBER", "")
    qr_data = yape_number or f"yape-{order_id.lower()}"
    if yape_number:
        qr_data = f"yape://pay?phone={yape_number}&amount={total:.2f}&ref={order_id}"
    return {
        "order_id": order_id,
        "total": total,
        "currency": "PEN",
        "payment_method": "yape",
        "qr_reference": order_id,
        "qr_url": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={qr_data}",
        "status": "pending",
        "message": (
            f"Escanea con Yape/Plin. Monto: S/ {total:.2f}. Referencia: {order_id}. "
            "Confirmación manual hasta integrar agregador."
        ),
    }


@router.post("/checkout/lemon")
async def checkout_lemon(authorization: str | None = Header(None)):
    username = require_user(authorization)
    _, total, order_id = _prepare_pending_order(username, "lemon")
    from market_connectors.lemon_payments import create_checkout

    try:
        lc = await create_checkout(total, "ARS", f"CLI-Market-{order_id}")
        if "checkout_url" in lc:
            return {
                "order_id": order_id,
                "total": total,
                "currency": "ARS",
                "payment_method": "lemon",
                "status": "pending",
                "lemon_checkout_id": lc["checkout_id"],
                "checkout_url": lc["checkout_url"],
                "qr_url": lc.get("qr_url", ""),
                "message": "Completa el pago con Lemon.",
            }
        raise HTTPException(status_code=502, detail=lc.get("error", "Lemon error"))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=501, detail="Lemon no configurado. Set LEMON_API_KEY.")


@router.post("/checkout/paypal")
async def checkout_paypal(authorization: str | None = Header(None)):
    username = require_user(authorization)
    _, total, order_id = _prepare_pending_order(username, "paypal")
    from market_connectors.paypal_payments import create_order

    try:
        pp = await create_order(total, "USD", f"CLI-Market-{order_id}")
        if "approve_url" in pp:
            db_set_order_gateway_ref(order_id, pp["order_id"])
            return {
                "order_id": order_id,
                "total": total,
                "currency": "USD",
                "payment_method": "paypal",
                "status": "pending",
                "paypal_order_id": pp["order_id"],
                "approve_url": pp["approve_url"],
                "message": "Completa el pago en PayPal.",
            }
        raise HTTPException(status_code=502, detail=pp.get("error", "PayPal error"))
    except ValueError:
        raise HTTPException(
            status_code=501,
            detail="PayPal no configurado. Set PAYPAL_CLIENT_ID y PAYPAL_CLIENT_SECRET.",
        )


@router.post("/checkout/paypal/capture")
async def checkout_paypal_capture(
    paypal_order_id: str = "",
    authorization: str | None = Header(None),
):
    """Capture after buyer returns from PayPal (backup if webhook is delayed)."""
    require_user(authorization)
    if not paypal_order_id:
        raise HTTPException(status_code=400, detail="paypal_order_id required")
    from market_connectors.paypal_payments import capture_order

    cap = await capture_order(paypal_order_id)
    if not cap.get("ok"):
        raise HTTPException(status_code=502, detail=cap.get("error", "Capture failed"))
    row = db_find_order_by_gateway_ref(paypal_order_id)
    if row:
        db_update_order_status(row["order_id"], "paid")
    return {"ok": True, "paypal_order_id": paypal_order_id, "market_order": row}


@router.post("/checkout/wise")
async def checkout_wise(authorization: str | None = Header(None)):
    username = require_user(authorization)
    _, total, order_id = _prepare_pending_order(username, "wise")
    from market_connectors.wise_payments import WISE_API_TOKEN

    wise_ok = bool(WISE_API_TOKEN)
    wise_pay_me = os.getenv("WISE_PAY_ME_URL", "https://wise.com/pay/me/ricardoantonioc68")
    return {
        "order_id": order_id,
        "total": total,
        "currency": "PEN",
        "payment_method": "wise",
        "status": "pending",
        "wise_available": wise_ok,
        "wise_pay_link": wise_pay_me,
        "wise_qr_url": f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={wise_pay_me}",
        "instructions": {
            "pay_link": wise_pay_me,
            "reference": order_id,
            "amount_pen": total,
        },
        "message": "Escanea el QR o usa el link de Wise. Referencia obligatoria.",
    }


@router.post("/checkout/paypal-webhook")
async def paypal_webhook(request: Request):
    """PayPal webhooks — verify signature, capture orders, upgrade Pro tier."""
    body = await request.json()
    headers = {k.lower(): v for k, v in request.headers.items()}
    from market_connectors.paypal_payments import PAYPAL_WEBHOOK_ID, verify_webhook_signature

    if not PAYPAL_WEBHOOK_ID:
        if is_production_deploy():
            raise HTTPException(
                status_code=503,
                detail="PAYPAL_WEBHOOK_ID required in production",
            )
        if not paypal_allow_unverified_webhooks():
            raise HTTPException(
                status_code=401,
                detail="PayPal webhook verification not configured",
            )
    else:
        verified = await verify_webhook_signature(headers, body)
        if not verified:
            logger.warning("PayPal webhook signature verification failed")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    result = await _handle_paypal_event(body)
    logger.info("PayPal webhook processed: %s", result)
    return {"received": True, **result}


@router.post("/checkout/webhook")
def checkout_webhook(order_id: str = "", status: str = "paid", secret: str = ""):
    """Mark an order paid/failed. Requires CHECKOUT_WEBHOOK_SECRET in production."""
    expected = os.getenv("CHECKOUT_WEBHOOK_SECRET", "")
    if is_production_deploy():
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="CHECKOUT_WEBHOOK_SECRET required in production",
            )
        if secret != expected:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")
    elif expected and secret != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id required")
    if not db_update_order_status(order_id, status):
        raise HTTPException(status_code=404, detail="Order not found")
    return {"order_id": order_id, "status": status, "message": f"Payment {status}"}


@router.get("/checkout/rates")
async def checkout_rates():
    """FX rates with PEN as base. Falls back to a static table if Wise is unreachable."""
    try:
        from market_connectors.wise_payments import get_rates

        rates = await get_rates("PEN")
        return {"base": "PEN", "rates": rates}
    except Exception:
        return {
            "base": "PEN",
            "rates": {
                "USD": 0.27,
                "EUR": 0.25,
                "ARS": 0.0027,
                "BRL": 0.27,
                "MXN": 0.078,
                "COP": 0.00035,
                "CLP": 0.0014,
                "PEN": 1.0,
            },
            "source": "fallback",
        }


@router.get("/paypal-status")
async def paypal_status(test: bool = False):
    """Check if PayPal credentials are configured. ?test=1 verifies API auth."""
    client_id = os.getenv("PAYPAL_CLIENT_ID", "")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
    sandbox = os.getenv("PAYPAL_SANDBOX", "true").lower() == "true"
    out = {
        "configured": bool(client_id and client_secret),
        "sandbox": sandbox,
        "live": not sandbox and bool(client_id and client_secret),
        "webhook_configured": bool(os.getenv("PAYPAL_WEBHOOK_ID", "")),
        "plan_id_configured": bool(os.getenv("PAYPAL_PLAN_ID", "")),
        "api_url": "https://api-m.sandbox.paypal.com" if sandbox else "https://api-m.paypal.com",
        "webhook_url": "https://cli-market-production.up.railway.app/checkout/paypal-webhook",
        "setup_script": "python3 ops/paypal_sandbox_setup.py check",
        "endpoints": [
            "/checkout/paypal",
            "/checkout/paypal/capture",
            "/billing/paypal",
            "/checkout/paypal-webhook",
        ],
    }
    if test and out["configured"]:
        try:
            from market_connectors.paypal_payments import check_connection

            out["auth_test"] = await check_connection()
        except Exception as e:
            out["auth_test"] = {"ok": False, "error": str(e)}
    return out


def process_pro_subscription_request(
    *,
    email: str,
    lang: str = "en",
    username: str = "",
    force: bool = False,
    note: str = "",
) -> dict:
    """Shared Pro request flow: dedupe, persist, email subscriber + notify hello@."""
    from market_connectors.email_outbound import PRO_PAYMENT_URL, send_pro_payment_email, send_pro_request_notify

    email = email.strip().lower()
    lang = (lang or "en").strip().lower()[:2]

    if not username:
        username = email.split("@")[0]

    recent = db_recent_subscription_request(email)
    if recent and not force:
        return {
            "ok": True,
            "request_id": recent["id"],
            "username": recent["username"],
            "email": recent["email"],
            "payment_link": recent["payment_link"] or PRO_PAYMENT_URL,
            "email_sent": bool(recent.get("email_sent")),
            "message": (
                "Ya enviamos el link de pago recientemente. Revisa tu bandeja (y spam). "
                "Pasa resend: true para reenviar."
                if lang == "es"
                else "We already sent a payment link recently. Check inbox (and spam). "
                "Pass resend: true to send again."
            ),
            "duplicate": True,
        }

    req = db_create_subscription_request(username, email, PRO_PAYMENT_URL)
    sub_mail = send_pro_payment_email(
        to_email=email,
        username=username,
        request_id=req["id"],
        lang=lang,
    )
    notify_mail = send_pro_request_notify(
        subscriber_email=email,
        username=username,
        request_id=req["id"],
        note=note,
    )
    if sub_mail.get("sent"):
        db_mark_subscription_request_emailed(req["id"])

    if lang == "es":
        if sub_mail.get("sent"):
            message = f"Te enviamos el link de pago a {email}. Activa Pro tras confirmar el pago."
        else:
            message = (
                f"Link de pago: {PRO_PAYMENT_URL}. "
                "Configura SMTP en el servidor para envío automático por email."
            )
    elif sub_mail.get("sent"):
        message = f"We emailed the payment link to {email}."
    else:
        message = f"Payment link: {PRO_PAYMENT_URL}. Configure SMTP for automatic email."

    return {
        "ok": True,
        "request_id": req["id"],
        "username": username,
        "email": email,
        "payment_link": PRO_PAYMENT_URL,
        "email_sent": sub_mail.get("sent", False),
        "email_error": sub_mail.get("reason") if not sub_mail.get("sent") else None,
        "notify_sent": notify_mail.get("sent", False),
        "notify_error": notify_mail.get("reason") if not notify_mail.get("sent") else None,
        "message": message,
    }


@router.post("/billing/request-pro")
def request_pro_subscription(body: dict, authorization: str | None = Header(None)):
    """Request Pro — stores intent and emails payment link from hello@cli-market.dev.

    Default billing flow (no PayPal API friction). Requires subscriber email.
    """
    try:
        check_rate_limit("billing-request-pro")
        email = (body.get("email") or "").strip().lower()
        lang = (body.get("lang") or "en").strip().lower()[:2]
        force = bool(body.get("resend"))
        note = (body.get("note") or body.get("use_case") or "").strip()

        if not email or not _EMAIL_RE.match(email):
            raise HTTPException(status_code=400, detail="valid email is required")

        username = (body.get("username") or "").strip()
        if authorization:
            try:
                username = require_user(authorization)
            except HTTPException:
                if not username:
                    raise

        return process_pro_subscription_request(
            email=email,
            lang=lang,
            username=username,
            force=force,
            note=note,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("request-pro failed")
        raise HTTPException(status_code=503, detail=f"billing unavailable: {e}") from e


@router.post("/billing/paypal")
async def billing_paypal(authorization: str | None = Header(None)):
    """PayPal Subscription for Pro plan ($49/mo)."""
    username = require_user(authorization)
    try:
        from market_connectors.paypal_payments import create_subscription

        result = await create_subscription(username=username)
        if "approve_url" in result:
            db_save_billing_pending(result["subscription_id"], "paypal", username, "subscription")
            return {
                "subscription_id": result["subscription_id"],
                "approve_url": result["approve_url"],
                "plan": "Pro",
                "amount": "$49/mo",
                "username": username,
            }
        return {"error": result.get("error", "PayPal error"), "details": result}
    except ValueError as e:
        return {"error": "PayPal no configurado", "detail": str(e)}
    except Exception as e:
        logger.exception("billing_paypal failed")
        return {"error": str(e)}


@router.post("/billing/checkout")
def billing_checkout(authorization: str | None = Header(None)):
    """Stripe Checkout for Pro subscription upgrade."""
    username = require_user(authorization)
    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key:
        raise HTTPException(status_code=501, detail="Stripe not configured")
    try:
        import stripe

        stripe.api_key = stripe_key
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": os.getenv("STRIPE_PRICE_PRO", "price_pro"), "quantity": 1}],
            mode="subscription",
            success_url="https://cli-market.dev?upgraded=true",
            cancel_url="https://cli-market.dev?upgraded=false",
            client_reference_id=username,
        )
        return {"url": session.url}
    except ImportError:
        raise HTTPException(status_code=501, detail="pip install stripe")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
