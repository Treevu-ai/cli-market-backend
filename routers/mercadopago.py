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
    """Mercado Pago IPN (query) and Webhooks v1 (JSON body)."""
    payment_id = ""
    if request.method == "GET":
        payment_id = request.query_params.get("id") or request.query_params.get("data.id") or ""
        topic = request.query_params.get("topic", "")
        if topic and topic != "payment":
            return {"received": True, "ignored_topic": topic}
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            data = body.get("data") or {}
            payment_id = str(data.get("id") or body.get("id") or "")
            action = body.get("action", "")
            if action and "payment" not in action.lower():
                return {"received": True, "ignored_action": action}

    if not payment_id:
        return {"received": True, "message": "no payment id"}
    try:
        result = await _handle_mercadopago_payment(payment_id)
        logger.info("Mercado Pago webhook: %s", result)
        return {"received": True, **result}
    except ValueError:
        raise HTTPException(status_code=503, detail="Mercado Pago not configured")
    except Exception as e:
        logger.exception("mercadopago webhook failed")
        raise HTTPException(status_code=502, detail=str(e)) from e


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