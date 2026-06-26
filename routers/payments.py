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
  POST /billing/paypal      PayPal Subscription (authenticated CLI)
  POST /billing/paypal-subscribe  PayPal Subscription (landing — auto-activate)
  POST /billing/checkout    Stripe Checkout for Pro subscription
  GET  /paypal-status       PayPal config diagnostic
"""

from __future__ import annotations

import logging
import os
import re
import uuid

from fastapi import APIRouter, Body, Header, HTTPException, Request

from market_core import (
    db_claim_webhook_event,
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
from pre_checkout_validate import pre_checkout_validate
from market_core.market_billing import (
    FOUNDING_PROMO_CODE,
    FOUNDING_SEAT_LIMIT,
    db_record_promo_redemption,
    founding_seats_remaining,
    normalize_billing_plan,
    price_label_for_plan,
    tier_for_billing_plan,
    validate_founding_available,
)
from server_deps import check_rate_limit, require_api_key, require_checkout_access, require_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payments"])

_ORDER_REF_RE = re.compile(r"CLI-Market-(ORD-[A-F0-9]+)", re.I)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _prepare_pending_order(
    username: str,
    method: str,
    idempotency_key: str | None = None,
) -> tuple[list[dict], float, str]:
    """Common preamble: get cart, validate prices, create pending order, clear cart."""
    require_checkout_access(username)
    cart = db_get_cart(username)
    if not cart:
        raise HTTPException(status_code=400, detail="Carrito vacío")

    validation = pre_checkout_validate(username, cart)
    if not validation.ok:
        raise HTTPException(status_code=409, detail=validation.to_dict())

    total = validation.validated_total
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    idem = (idempotency_key or "").strip() or None
    created = db_create_order(
        username,
        cart,
        method,
        total,
        status="pending",
        order_id=order_id,
        idempotency_key=idem,
    )
    if created.get("idempotent_replay"):
        items = created.get("items") or cart
        if idem and abs(float(created.get("total", 0)) - total) > 0.01:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "idempotency_key_reused_with_different_cart",
                    "order_id": created["order_id"],
                },
            )
        return items, float(created["total"]), created["order_id"]
    db_clear_cart(username)
    return cart, total, created["order_id"]


@router.post("/checkout/validate")
def checkout_validate(authorization: str | None = Header(None)):
    """Validate cart prices and freshness without creating an order."""
    username = require_api_key(authorization)
    require_checkout_access(username)
    cart = db_get_cart(username)
    if not cart:
        raise HTTPException(status_code=400, detail="Carrito vacío")
    result = pre_checkout_validate(username, cart)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.to_dict())
    return result.to_dict()


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
        pending = None
        if sub_id:
            pending = db_get_billing_pending(sub_id)
        if not username and pending:
            username = (pending or {}).get("username", "")
        plan_slug = normalize_billing_plan((pending or {}).get("kind") or "pro")
        target_tier = tier_for_billing_plan(plan_slug)
        if username:
            db_set_subscription(username, target_tier, paypal_subscription_id=sub_id)
            if sub_id:
                db_delete_billing_pending(sub_id)
            actions.append(f"{target_tier}_activated:{username}")
            if plan_slug == "pro_founding":
                try:
                    db_record_promo_redemption(username, FOUNDING_PROMO_CODE, plan_slug)
                except Exception:
                    logger.warning(
                        "db_record_promo_redemption failed for %s (%s)", username, plan_slug, exc_info=True
                    )
            elif plan_slug == "pro_annual":
                try:
                    db_record_promo_redemption(username, "pro_annual", plan_slug)
                except Exception:
                    logger.warning(
                        "db_record_promo_redemption failed for %s (%s)", username, plan_slug, exc_info=True
                    )
            try:
                from market_funnel import record_funnel_event

                funnel_event = "starter_subscribe" if target_tier == "starter" else "activated"
                record_funnel_event(
                    funnel_event,
                    username=username,
                    meta={"source": "paypal_webhook", "plan": plan_slug},
                    dedupe=True,
                )
            except Exception:
                logger.debug("record_funnel_event(%s) failed", funnel_event, exc_info=True)
            try:
                from market_core import db_create_api_key, db_get_user_email
                key_data = db_create_api_key(username, "read_write", target_tier)
                email = db_get_user_email(username)
                if email:
                    from market_connectors.email_outbound import send_credentials_email
                    send_credentials_email(
                        to_email=email,
                        username=username,
                        api_key=key_data["key"],
                        plan=target_tier,
                    )
                    actions.append(f"key_emailed:{key_data['prefix']}")
                else:
                    actions.append(f"key_generated_no_email:{key_data['prefix']}")
            except Exception as _ke:
                logger.warning("key generation failed for %s: %s", username, _ke)
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
def checkout_yape(
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    username = require_api_key(authorization)
    _, total, order_id = _prepare_pending_order(username, "yape", idempotency_key)
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
        "confirmation_mode": "manual",
        "capabilities": {"checkout_scope": "cli_market_internal"},
        "message": (
            f"Escanea con Yape/Plin. Monto: S/ {total:.2f}. Referencia: {order_id}. "
            "Confirmación manual hasta integrar agregador."
        ),
    }


@router.post("/checkout/lemon")
async def checkout_lemon(
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    username = require_api_key(authorization)
    _, total, order_id = _prepare_pending_order(username, "lemon", idempotency_key)
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
async def checkout_paypal(
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    username = require_api_key(authorization)
    _, total, order_id = _prepare_pending_order(username, "paypal", idempotency_key)
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
    require_api_key(authorization)
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
async def checkout_wise(
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    username = require_api_key(authorization)
    _, total, order_id = _prepare_pending_order(username, "wise", idempotency_key)
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

    event_id = (
        body.get("id")
        or headers.get("paypal-transmission-id")
        or headers.get("PAYPAL-TRANSMISSION-ID")
        or ""
    )
    if event_id and not db_claim_webhook_event(str(event_id), "paypal"):
        logger.info("PayPal webhook duplicate ignored: %s", event_id)
        return {"received": True, "duplicate": True, "actions": []}

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
    event_key = f"checkout:{order_id}:{status}"
    if not db_claim_webhook_event(event_key, "checkout_webhook"):
        return {"order_id": order_id, "status": status, "duplicate": True, "message": "Already processed"}
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

    try:
        from market_funnel import record_funnel_event
        record_funnel_event("request_pro", username=username or None, meta={"email": email}, dedupe=False)
    except Exception:
        logger.debug("record_funnel_event(request_pro) failed", exc_info=True)
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
                username = require_api_key(authorization)
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


def _resolve_pro_username(
    email: str,
    *,
    body_username: str = "",
    auth_username: str = "",
) -> str:
    if auth_username:
        return auth_username.strip()
    if body_username.strip():
        return body_username.strip()
    recent = db_recent_subscription_request(email.strip().lower())
    if recent and recent.get("username"):
        return recent["username"]
    local = email.split("@")[0].lower()
    safe = re.sub(r"[^a-z0-9_-]", "", local)[:32]
    return safe or f"user-{uuid.uuid4().hex[:8]}"


async def _start_paypal_subscription(
    username: str,
    email: str,
    *,
    plan: str = "pro",
    promo_code: str = "",
) -> dict:
    from market_connectors.paypal_payments import create_subscription

    plan_slug = normalize_billing_plan(plan)
    if plan_slug == "pro_founding":
        ok, err = validate_founding_available(username, promo_code)
        if not ok:
            return {"error": err}

    site = os.getenv("CLI_MARKET_SITE_URL", "https://cli-market.dev").rstrip("/")
    return_url = f"{site}/?sub=success&plan={plan_slug}#pricing"
    cancel_url = f"{site}/?sub=cancelled&plan={plan_slug}#pricing"

    result = await create_subscription(
        username=username,
        plan=plan_slug,
        return_url=return_url,
        cancel_url=cancel_url,
    )
    if "approve_url" not in result:
        return {"error": result.get("error", "PayPal error"), "details": result}
    sub_id = result["subscription_id"]
    approve = result["approve_url"]
    db_save_billing_pending(sub_id, "paypal", username, kind=plan_slug)
    db_create_subscription_request(username, email, approve)
    tier = tier_for_billing_plan(plan_slug)
    display = plan_slug.replace("_", " ").title()
    return {
        "ok": True,
        "subscription_id": sub_id,
        "approve_url": approve,
        "plan": display,
        "plan_slug": plan_slug,
        "tier": tier,
        "amount": price_label_for_plan(plan_slug),
        "username": username,
        "auto_activate": True,
        "founding_seats_remaining": founding_seats_remaining() if plan_slug == "pro_founding" else None,
    }


async def _start_paypal_pro_subscription(username: str, email: str) -> dict:
    return await _start_paypal_subscription(username, email, plan="pro")


@router.post("/billing/paypal")
async def billing_paypal(
    body: dict = Body(default_factory=dict),
    authorization: str | None = Header(None),
):
    """PayPal Subscription — authenticated CLI (starter | pro | pro_founding | pro_annual)."""
    username = require_api_key(authorization)
    plan = normalize_billing_plan(body.get("plan") or "pro")
    promo_code = (body.get("promo_code") or "").strip()
    body_email = (body.get("email") or "").strip().lower()
    try:
        from market_core import db_get_user_email, db_save_user, db_get_users

        db_email = db_get_user_email(username) or ""
        if body_email and not db_email:
            users = db_get_users()
            user = users.get(username) or {}
            db_save_user(username, user.get("password", ""), user.get("token"), body_email)
            db_email = body_email
        email = db_email or body_email or f"{username}@cli-market.dev"
        out = await _start_paypal_subscription(username, email, plan=plan, promo_code=promo_code)
        if out.get("ok"):
            out["message"] = (
                f"{out['plan']} se activa automáticamente al confirmar la suscripción en PayPal."
            )
            return out
        raise HTTPException(status_code=502, detail=out.get("error", "PayPal error"))
    except ValueError as e:
        return {"error": "PayPal no configurado", "detail": str(e)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("billing_paypal failed")
        return {"error": str(e)}


@router.post("/billing/paypal-subscribe")
async def billing_paypal_subscribe(body: dict, authorization: str | None = Header(None)):
    """PayPal Subscription from landing — auto-activate via webhook."""
    try:
        check_rate_limit("billing-paypal-subscribe")
        email = (body.get("email") or "").strip().lower()
        lang = (body.get("lang") or "en").strip().lower()[:2]
        plan = normalize_billing_plan(body.get("plan") or "pro")
        promo_code = (body.get("promo_code") or "").strip()
        if not email or not _EMAIL_RE.match(email):
            raise HTTPException(status_code=400, detail="valid email is required")

        auth_user = ""
        if authorization:
            try:
                auth_user = require_user(authorization)
            except HTTPException:
                auth_user = ""

        username = _resolve_pro_username(
            email,
            body_username=(body.get("username") or ""),
            auth_username=auth_user,
        )

        out = await _start_paypal_subscription(
            username, email, plan=plan, promo_code=promo_code
        )
        if not out.get("ok"):
            raise HTTPException(status_code=502, detail=out.get("error", "PayPal error"))

        tier_label = out.get("tier", "pro")
        if lang == "es":
            out["message"] = (
                f"Confirme la suscripción en PayPal; {tier_label} se activa en segundos (webhook). "
                "Luego: market whoami"
            )
        else:
            out["message"] = (
                f"Confirm subscription in PayPal; {tier_label} activates in seconds (webhook). "
                "Then: market whoami"
            )
        return out
    except HTTPException:
        raise
    except ValueError as e:
        logger.info("billing_paypal_subscribe: PayPal not configured (%s), using fallback", e)
        return process_pro_subscription_request(email=email, lang=lang, username=username)
    except Exception as e:
        logger.exception("billing_paypal_subscribe failed")
        raise HTTPException(status_code=503, detail=f"billing unavailable: {e}") from e


_PRO_BILLING_METHODS = frozenset({"paypal", "yape", "plin", "mercadopago"})


@router.post("/billing/pro-checkout")
async def billing_pro_checkout(body: dict, authorization: str | None = Header(None)):
    """Pro billing from landing — PayPal, Mercado Pago, Yape, or Plin."""
    try:
        check_rate_limit("billing-pro-checkout")
        email = (body.get("email") or "").strip().lower()
        lang = (body.get("lang") or "en").strip().lower()[:2]
        method = (body.get("payment_method") or "paypal").strip().lower()
        force = bool(body.get("resend"))

        if method not in _PRO_BILLING_METHODS:
            raise HTTPException(
                status_code=400,
                detail=f"payment_method must be one of: {', '.join(sorted(_PRO_BILLING_METHODS))}",
            )
        if not email or not _EMAIL_RE.match(email):
            raise HTTPException(status_code=400, detail="valid email is required")

        auth_user = ""
        if authorization:
            try:
                auth_user = require_user(authorization)
            except HTTPException:
                auth_user = ""

        username = _resolve_pro_username(
            email,
            body_username=(body.get("username") or ""),
            auth_username=auth_user,
        )

        if method == "paypal":
            plan = normalize_billing_plan(body.get("plan") or "pro")
            promo_code = (body.get("promo_code") or "").strip()
            try:
                out = await _start_paypal_subscription(username, email, plan=plan, promo_code=promo_code)
                if out.get("ok"):
                    out["payment_method"] = "paypal"
                    return out
            except ValueError as e:
                logger.info("billing_pro_checkout paypal: not configured (%s), using fallback", e)
            except Exception as e:
                logger.warning("billing_pro_checkout paypal failed (%s), using fallback", e)
            out = process_pro_subscription_request(email=email, lang=lang, username=username, force=force)
            out["payment_method"] = "paypal"
            return out

        if method in ("yape", "plin", "mercadopago"):
            from market_connectors.paypal_payments import PRO_PRICE_USD
            raw = os.getenv("PRO_PEN_PER_USD", "3.75")
            try:
                pen_per_usd = float(str(raw).strip())
            except (TypeError, ValueError):
                pen_per_usd = 3.75
            if pen_per_usd <= 0:
                pen_per_usd = 3.75
            amount_pen = round(float(PRO_PRICE_USD) * pen_per_usd, 2)

            req = db_create_subscription_request(username, email, "")
            request_id = req["id"]

            if method == "mercadopago":
                from market_connectors.mercadopago_payments import create_preference
                mp_return = f"https://cli-market.dev/?mp=success&ref={request_id}#pricing"
                mp = await create_preference(
                    amount_pen,
                    "PEN",
                    f"CLI-Market-{request_id}",
                    title="CLI Market Pro",
                    success_url=mp_return,
                    pending_url=f"https://cli-market.dev/?mp=pending&ref={request_id}#pricing",
                    failure_url=f"https://cli-market.dev/?mp=failure&ref={request_id}#pricing",
                )
                if not mp.get("checkout_url"):
                    raise HTTPException(status_code=502, detail=mp.get("error", "Mercado Pago error"))
                checkout_url = mp["checkout_url"]
                return {
                    "ok": True,
                    "request_id": request_id,
                    "username": username,
                    "email": email,
                    "payment_method": "mercadopago",
                    "amount_pen": amount_pen,
                    "checkout_url": checkout_url,
                    "approve_url": checkout_url,
                    "auto_activate": True,
                    "amount_usd": float(PRO_PRICE_USD),
                }

            phone = os.getenv("YAPE_PLIN_NUMBER", "").strip()
            if phone:
                checkout_url = f"yape://pay?phone={phone}&amount={amount_pen:.2f}&ref={request_id}"
            else:
                checkout_url = f"https://cli-market.dev/?method={method}&amount={amount_pen:.2f}&ref={request_id}#pricing"
            return {
                "ok": True,
                "request_id": request_id,
                "username": username,
                "email": email,
                "payment_method": method,
                "amount_pen": amount_pen,
                "checkout_url": checkout_url,
                "auto_activate": False,
                "amount_usd": float(PRO_PRICE_USD),
                "message": (
                    f"Transfiere S/ {amount_pen:.2f} via {method.upper()} al número {phone} con referencia {request_id}."
                    if phone
                    else f"Paga S/ {amount_pen:.2f} via {method.upper()} — referencia {request_id}."
                ) if lang == "es" else (
                    f"Transfer S/ {amount_pen:.2f} via {method.upper()} to {phone} with reference {request_id}."
                    if phone
                    else f"Pay S/ {amount_pen:.2f} via {method.upper()} — reference {request_id}."
                ),
            }

        raise HTTPException(status_code=400, detail=f"unsupported payment_method: {method}")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        logger.exception("billing_pro_checkout failed")
        raise HTTPException(status_code=503, detail=f"billing unavailable: {e}") from e


@router.post("/billing/starter-subscribe")
async def billing_starter_subscribe(body: dict, authorization: str | None = Header(None)):
    """PayPal Starter subscription from landing."""
    body = {**body, "plan": "starter"}
    return await billing_paypal_subscribe(body, authorization)


@router.post("/billing/build-checkout")
async def billing_build_checkout(body: dict, authorization: str | None = Header(None)):
    """Unified Build tier checkout (PayPal). Supports plan=starter|pro|pro_founding|pro_annual."""
    method = (body.get("payment_method") or "paypal").strip().lower()
    if method != "paypal":
        raise HTTPException(
            status_code=501,
            detail="Only PayPal auto-checkout is available via API; use payment_method=paypal",
        )
    return await billing_paypal_subscribe(body, authorization)


@router.get("/billing/pricing-stats")
def billing_pricing_stats():
    """Public founding-seat counter for landing badges."""
    remaining = founding_seats_remaining()
    return {
        "founding_promo_code": FOUNDING_PROMO_CODE,
        "founding_seat_limit": FOUNDING_SEAT_LIMIT,
        "founding_seats_remaining": remaining,
        "founding_available": remaining > 0,
    }


_PROCURE_BILLING_METHODS = frozenset({"paypal", "mercadopago", "yape", "plin"})


@router.post("/billing/procure-subscribe")
async def billing_procure_subscribe(body: dict, authorization: str | None = Header(None)):
    """Procure Copilot subscription — PayPal, Mercado Pago, Yape, or Plin."""
    from procure_billing import (
        PROCURE_PLANS,
        procure_mp_checkout_enabled,
        procure_plan_config,
        procure_price_pen,
        procure_tier_from_request_id,
        tier_to_procure_plan,
    )

    try:
        check_rate_limit("billing-procure-subscribe")
        email = (body.get("email") or "").strip().lower()
        lang = (body.get("lang") or "en").strip().lower()[:2]
        plan_slug = (body.get("plan") or "pro").strip().lower()
        method = (body.get("payment_method") or "paypal").strip().lower()
        if method not in _PROCURE_BILLING_METHODS:
            raise HTTPException(
                status_code=400,
                detail=f"payment_method must be one of: {', '.join(sorted(_PROCURE_BILLING_METHODS))}",
            )
        if method != "paypal" and not procure_mp_checkout_enabled():
            raise HTTPException(
                status_code=501,
                detail=(
                    "Procure checkout local (Mercado Pago / Yape / Plin) no está habilitado aún"
                    if lang == "es"
                    else "Procure local checkout (Mercado Pago / Yape / Plin) is not enabled yet"
                ),
            )
        if plan_slug not in PROCURE_PLANS:
            raise HTTPException(
                status_code=400,
                detail=f"plan must be one of: {', '.join(sorted(PROCURE_PLANS))}",
            )
        if not email or not _EMAIL_RE.match(email):
            raise HTTPException(status_code=400, detail="valid email is required")

        auth_user = ""
        if authorization:
            try:
                auth_user = require_user(authorization)
            except HTTPException:
                auth_user = ""

        username = _resolve_pro_username(
            email,
            body_username=(body.get("username") or ""),
            auth_username=auth_user,
        )

        cfg = procure_plan_config(plan_slug)
        amount_usd = float(cfg["amount"])
        prefix = cfg["request_prefix"]

        if method == "paypal":
            paypal_plan_id = cfg.get("paypal_plan_id", "")
            try:
                import httpx
                from market_connectors.paypal_payments import PAYPAL_API, _ensure_billing_plan, _get_access_token
                from market_connectors.email_outbound import send_pro_subscribe_pending_email

                return_url = os.getenv(
                    "PROCURE_SUBSCRIBE_RETURN_URL",
                    "https://cli-market.dev/?sub=success&audience=procure#procure",
                )
                cancel_url = os.getenv(
                    "PROCURE_SUBSCRIBE_CANCEL_URL",
                    "https://cli-market.dev/?sub=cancelled&audience=procure#procure",
                )
                token = await _get_access_token()
                async with httpx.AsyncClient(timeout=15.0) as client:
                    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    plan_id = await _ensure_billing_plan(
                        token,
                        client,
                        amount_usd,
                        "USD",
                        env_plan_id=paypal_plan_id,
                        product_name=cfg["label"],
                        plan_name=f"{cfg['label']} Monthly",
                        description=f"${amount_usd:.0f}/month — Procure Copilot",
                    )
                    logger.info(
                        "procure paypal plan_id=%s plan_slug=%s env_var=%s",
                        plan_id, plan_slug, cfg["paypal_env"],
                    )
                    p3 = await client.post(
                        f"{PAYPAL_API}/v1/billing/subscriptions",
                        json={
                            "plan_id": plan_id,
                            "custom_id": username,
                            "application_context": {
                                "return_url": return_url,
                                "cancel_url": cancel_url,
                                "brand_name": "Procure Copilot",
                                "user_action": "SUBSCRIBE_NOW",
                                "shipping_preference": "NO_SHIPPING",
                            },
                        },
                        headers=h,
                    )
                    if p3.status_code not in (200, 201):
                        logger.warning("billing_procure_subscribe paypal error: %s", p3.text)
                        raise HTTPException(status_code=502, detail="PayPal subscription unavailable")
                    data = p3.json()
                    approve_link = next(
                        (link["href"] for link in data.get("links", []) if link.get("rel") == "approve"),
                        None,
                    )
                    sub_id = data["id"]

                db_save_billing_pending(sub_id, "paypal", username, cfg["tier"])
                req = db_create_subscription_request(username, email, approve_link or "", prefix=prefix)
                mail = send_pro_subscribe_pending_email(
                    to_email=email,
                    username=username,
                    approve_url=approve_link or "",
                    request_id=req["id"],
                )
                if mail.get("sent"):
                    db_mark_subscription_request_emailed(req["id"])
                try:
                    from market_funnel import record_funnel_event
                    record_funnel_event(
                        "procure_subscribe",
                        username=username,
                        meta={"email": email, "source": "procure_subscribe_paypal", "plan": plan_slug, "tier": cfg["tier"]},
                        dedupe=False,
                    )
                except Exception:
                    pass
                return {
                    "ok": True,
                    "subscription_id": sub_id,
                    "approve_url": approve_link,
                    "plan": cfg["label"],
                    "tier": cfg["tier"],
                    "procure_plan": plan_slug,
                    "amount": f"${amount_usd:.0f}/mo",
                    "username": username,
                    "auto_activate": True,
                    "request_id": req["id"],
                    "email_sent": mail.get("sent", False),
                    "payment_method": "paypal",
                }
            except HTTPException:
                raise
            except ValueError as exc:
                raise HTTPException(status_code=501, detail=str(exc)) from exc
            except Exception as exc:
                logger.warning("billing_procure_subscribe paypal failed: %s", exc)
                raise HTTPException(status_code=502, detail="PayPal subscription unavailable") from exc

        amount_pen = procure_price_pen(plan_slug)
        req = db_create_subscription_request(username, email, "")
        request_id = req["id"]
        request_id_procure = f"{prefix}-{request_id}"

        if method == "mercadopago":
            from market_connectors.mercadopago_payments import create_preference
            mp_return = os.getenv("PROCURE_MP_SUCCESS_URL", f"https://cli-market.dev/?mp=success&audience=procure&ref={request_id_procure}#procure")
            mp = await create_preference(
                amount_pen,
                "PEN",
                f"CLI-Market-{request_id_procure}",
                title=cfg["label"],
                success_url=mp_return.replace("{ref}", request_id_procure),
                pending_url=f"https://cli-market.dev/?mp=pending&audience=procure&ref={request_id_procure}#procure",
                failure_url=f"https://cli-market.dev/?mp=failure&audience=procure&ref={request_id_procure}#procure",
            )
            if not mp.get("checkout_url"):
                raise HTTPException(status_code=502, detail=mp.get("error", "Mercado Pago error"))
            return {
                "ok": True,
                "request_id": request_id_procure,
                "username": username,
                "email": email,
                "payment_method": "mercadopago",
                "procure_plan": plan_slug,
                "amount_pen": amount_pen,
                "amount_usd": amount_usd,
                "checkout_url": mp["checkout_url"],
                "approve_url": mp["checkout_url"],
                "auto_activate": True,
            }

        phone = os.getenv("YAPE_PLIN_NUMBER", "").strip()
        if phone:
            checkout_url = f"yape://pay?phone={phone}&amount={amount_pen:.2f}&ref={request_id_procure}"
        else:
            checkout_url = f"https://cli-market.dev/?method={method}&amount={amount_pen:.2f}&ref={request_id_procure}#procure"
        return {
            "ok": True,
            "request_id": request_id_procure,
            "username": username,
            "email": email,
            "payment_method": method,
            "procure_plan": plan_slug,
            "amount_pen": amount_pen,
            "amount_usd": amount_usd,
            "checkout_url": checkout_url,
            "auto_activate": False,
            "message": (
                f"Transfiere S/ {amount_pen:.2f} con referencia {request_id_procure}."
                if lang == "es"
                else f"Transfer S/ {amount_pen:.2f} with reference {request_id_procure}."
            ),
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("billing_procure_subscribe failed")
        raise HTTPException(status_code=503, detail=f"billing unavailable: {e}") from e


@router.post("/billing/checkout")
def billing_checkout(authorization: str | None = Header(None)):
    """Stripe Checkout for Pro subscription upgrade."""
    username = require_api_key(authorization)
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
