"""Mercado Pago checkout and webhook endpoints."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request

from market_core import (
    db_set_order_gateway_ref,
    db_update_order_status,
)
from server_deps import require_api_key

from routers.payments import _prepare_pending_order

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payments"])


@router.post("/checkout/mercadopago")
async def checkout_mercadopago(authorization: str | None = Header(None)):
    """Mercado Pago Checkout Pro — redirect URL for cart payment."""
    username = require_api_key(authorization)
    _, total, order_id = _prepare_pending_order(username, "mercadopago")
    from market_connectors.mercadopago_payments import create_preference

    currency = os.getenv("MERCADOPAGO_CURRENCY", "PEN").upper()
    success = os.getenv("MERCADOPAGO_SUCCESS_URL", "https://cli-market.dev?mp=success")
    failure = os.getenv("MERCADOPAGO_FAILURE_URL", "https://cli-market.dev?mp=failure")
    pending = os.getenv("MERCADOPAGO_PENDING_URL", "https://cli-market.dev?mp=pending")
    try:
        mp = await create_preference(
            total,
            currency,
            f"CLI-Market-{order_id}",
            success_url=success,
            failure_url=failure,
            pending_url=pending,
            title=f"CLI Market {order_id}",
        )
        if mp.get("checkout_url"):
            db_set_order_gateway_ref(order_id, mp["preference_id"])
            return {
                "order_id": order_id,
                "total": total,
                "currency": currency,
                "payment_method": "mercadopago",
                "status": "pending",
                "preference_id": mp["preference_id"],
                "checkout_url": mp["checkout_url"],
                "sandbox": mp.get("sandbox", True),
                "message": "Completa el pago en Mercado Pago.",
            }
        raise HTTPException(status_code=502, detail=mp.get("error", "Mercado Pago error"))
    except ValueError:
        raise HTTPException(
            status_code=501,
            detail=(
                "Mercado Pago no configurado. Set MERCADOPAGO_ACCESS_TOKEN "
                "(o MERCADOPAGO_ACCESS_TOKEN_SANDBOX / _PRODUCTION)."
            ),
        )


async def _handle_mercadopago_payment(payment_id: str) -> dict:
    from market_connectors.mercadopago_payments import get_payment, parse_external_order_id

    payment = await get_payment(payment_id)
    if payment.get("error"):
        return {"payment_id": payment_id, "error": payment["error"]}
    status = payment.get("status", "")
    external = payment.get("external_reference", "")
    market_order_id = parse_external_order_id(external)
    actions: list[str] = []
    if status == "approved" and market_order_id:
        if db_update_order_status(market_order_id, "paid"):
            db_set_order_gateway_ref(market_order_id, str(payment_id))
            actions.append(f"order_paid:{market_order_id}")
        else:
            actions.append(f"order_not_found:{market_order_id}")
    elif status in ("rejected", "cancelled"):
        if market_order_id:
            db_update_order_status(market_order_id, "failed")
            actions.append(f"order_failed:{market_order_id}")
    return {
        "payment_id": payment_id,
        "status": status,
        "external_reference": external,
        "market_order_id": market_order_id,
        "actions": actions,
    }


@router.api_route("/checkout/mercadopago-webhook", methods=["GET", "POST"])
async def mercadopago_webhook(request: Request):
    """Mercado Pago Webhooks (preferred) and legacy IPN query notifications."""
    from market_connectors.mercadopago_payments import (
        parse_webhook_payment_id,
        validate_webhook_signature,
        webhook_secret,
    )

    body: dict = {}
    if request.method == "POST":
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception:
            body = {}

    flat_query = dict(request.query_params)
    payment_id, ntype = parse_webhook_payment_id(query_params=flat_query, body=body)

    topic = flat_query.get("topic", "")
    if topic and topic not in ("payment", ""):
        return {"received": True, "ignored_topic": topic}
    if ntype and ntype not in ("payment", ""):
        return {"received": True, "ignored_type": ntype}

    action = str(body.get("action") or "")
    if action and "payment" not in action.lower():
        return {"received": True, "ignored_action": action}

    x_signature = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")
    data_id = flat_query.get("data.id", payment_id)
    secret = webhook_secret()
    if secret:
        if not validate_webhook_signature(
            x_signature=x_signature,
            x_request_id=x_request_id,
            data_id=data_id,
            secret=secret,
        ):
            logger.warning("Mercado Pago webhook signature invalid")
            raise HTTPException(status_code=401, detail="invalid x-signature")

    if not payment_id:
        return {"received": True, "message": "no payment id"}

    try:
        result = await _handle_mercadopago_payment(payment_id)
        logger.info("Mercado Pago webhook: %s", result)
        return {"received": True, **result}
    except ValueError:
        logger.exception("mercadopago webhook: not configured")
        return {"received": True, "error": "mercadopago_not_configured"}
    except Exception:
        logger.exception("mercadopago webhook failed")
        # MP retries on non-2xx; acknowledge receipt and log for manual replay
        return {"received": True, "error": "processing_failed"}


@router.get("/mercadopago-status")
async def mercadopago_status(test: bool = False):
    """Check Mercado Pago credentials. ?test=1 calls users/me."""
    import market_connectors.mercadopago_payments as mp

    token = mp.access_token()
    out = {
        "configured": bool(token),
        "sandbox": mp.is_sandbox(),
        "public_key_configured": bool(mp.public_key()),
        "currency": os.getenv("MERCADOPAGO_CURRENCY", "PEN").upper(),
        "notification_url": mp.notification_url(),
        "webhook_secret_configured": bool(mp.webhook_secret()),
        "env_keys": {
            k: bool(os.getenv(k, "").strip())
            for k in (
                "MERCADOPAGO_ACCESS_TOKEN",
                "MERCADOPAGO_ACCESS_TOKEN_SANDBOX",
                "MERCADOPAGO_ACCESS_TOKEN_PRODUCTION",
                "MERCADO_PAGO_ACCESS_TOKEN",
                "MP_ACCESS_TOKEN",
                "MERCADOPAGO_PUBLIC_KEY",
                "MERCADOPAGO_SANDBOX",
                "MERCADOPAGO_WEBHOOK_URL",
                "MERCADOPAGO_WEBHOOK_SECRET",
                "MERCADOPAGO_WEBHOOK_TOKEN",
                "MERCADOPAGO_SECRET_SIGNATURE",
                "MP_WEBHOOK_SECRET",
                "RAILWAY_PUBLIC_DOMAIN",
            )
        },
        "endpoints": [
            "/checkout/mercadopago",
            "/checkout/mercadopago-webhook",
        ],
    }
    if test and out["configured"]:
        out["auth_test"] = await mp.check_connection()
    return out