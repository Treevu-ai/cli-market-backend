"""Retailer onboarding and public contact endpoints.

Endpoints:
  POST /v1/retailers/apply   Self-serve retailer listing request
  POST /v1/contact           Developer / plan access request
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, HTTPException

from market_core import get_db

router = APIRouter(tags=["retailers"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLATFORMS = {"vtex", "shopify", "magento", "woocommerce", "other"}


def _insert_application(
    *,
    store_name: str,
    platform: str,
    country: str,
    contact_email: str,
    contact_name: str = "",
    website: str = "",
    api_token: str = "",
    api_token_hint: str = "",
    notes: str = "",
) -> str:
    from backend_interface import token_hint

    app_id = f"RET-{uuid.uuid4().hex[:8].upper()}"
    secret = (api_token or "").strip()
    hint = api_token_hint or token_hint(secret)
    db = get_db()
    db.execute(
        """
        INSERT INTO retailer_applications
            (id, store_name, platform, country, contact_email, contact_name,
             website, api_token, api_token_hint, notes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            app_id,
            store_name.strip(),
            platform.lower(),
            country.upper(),
            contact_email.strip().lower(),
            contact_name.strip(),
            website.strip(),
            secret,
            hint,
            notes.strip(),
        ),
    )
    db.commit()
    db.close()
    return app_id


@router.post("/v1/retailers/apply")
def apply_retailer(body: dict):
    """Self-serve retailer onboarding — stores request for review / auto-validation."""
    from market_connectors.email_outbound import (
        send_retailer_application_notify,
        send_retailer_application_received_email,
    )

    store_name = (body.get("store_name") or "").strip()
    platform = (body.get("platform") or "").strip().lower()
    country = (body.get("country") or "").strip().upper()
    contact_email = (body.get("contact_email") or body.get("email") or "").strip()
    contact_name = (body.get("contact_name") or body.get("name") or "").strip()
    lang = (body.get("lang") or "en").strip().lower()[:2]

    if not store_name or len(store_name) < 2:
        raise HTTPException(status_code=400, detail="store_name is required")
    if platform not in _PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"platform must be one of: {', '.join(sorted(_PLATFORMS))}",
        )
    if not country or len(country) != 2:
        raise HTTPException(status_code=400, detail="country must be a 2-letter code (e.g. PE)")
    if not contact_email or not _EMAIL_RE.match(contact_email):
        raise HTTPException(status_code=400, detail="valid contact_email is required")

    api_token = (body.get("api_token") or body.get("api_token_hint") or "").strip()
    website = (body.get("website") or "").strip()

    app_id = _insert_application(
        store_name=store_name,
        platform=platform,
        country=country,
        contact_email=contact_email,
        contact_name=contact_name,
        website=website,
        api_token=api_token,
        notes=(body.get("notes") or "").strip(),
    )

    try:
        from market_funnel import record_funnel_event

        record_funnel_event(
            "retailer_apply",
            meta={"platform": platform, "country": country, "application_id": app_id},
            dedupe=False,
        )
    except Exception:
        pass

    ack = send_retailer_application_received_email(
        to_email=contact_email,
        application_id=app_id,
        store_name=store_name,
        platform=platform,
        country=country,
        lang=lang,
        contact_name=contact_name,
    )
    notify = send_retailer_application_notify(
        application_id=app_id,
        store_name=store_name,
        platform=platform,
        country=country,
        contact_email=contact_email,
        contact_name=contact_name,
        website=website,
        has_token=bool(api_token),
    )

    if lang == "es":
        message = (
            f"Recibimos su solicitud ({app_id}). "
            "Validamos el catálogo de solo lectura y le escribimos en ≤24h hábiles. Gratis para siempre."
        )
    else:
        message = (
            f"Application received ({app_id}). "
            "We validate read-only catalog access and email you within 24 business hours. Free forever."
        )

    return {
        "ok": True,
        "application_id": app_id,
        "status": "pending",
        "email_sent": bool(ack.get("sent")),
        "notify_sent": bool(notify.get("sent")),
        "message": message,
        "next_steps": [
            "Keep your read-only API token ready (Shopify/Magento/WooCommerce) or confirm VTEX/Woo Store API public catalog.",
            f"Track status: hello@cli-market.dev with application ID {app_id}.",
        ],
    }




def process_starter_subscription_request(
    *,
    email: str,
    lang: str = "en",
    note: str = "",
    profile: str = "",
    name: str = "",
) -> dict:
    """Persist Starter lead and send acknowledgment + ops notify."""
    import uuid

    from market_connectors.email_outbound import (
        send_starter_request_notify,
        send_starter_request_received_email,
    )

    email = email.strip().lower()
    lang = (lang or "en").strip().lower()[:2]
    request_id = f"STR-{uuid.uuid4().hex[:8].upper()}"

    db = get_db()
    db.execute(
        """
        INSERT INTO contacts (chat_id, first_name, username, last_message, created_at, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (request_id, "starter", email, f"[starter] {note[:2000]}"),
    )
    db.commit()
    db.close()

    try:
        from market_funnel import record_funnel_event
        record_funnel_event("starter_request", meta={"email": email}, dedupe=False)
    except Exception:
        pass
    ack = send_starter_request_received_email(
        to_email=email,
        request_id=request_id,
        lang=lang,
        name=name,
    )
    notify = send_starter_request_notify(
        subscriber_email=email,
        request_id=request_id,
        profile=profile,
        name=name,
        note=note,
    )

    if lang == "es":
        message = (
            f"Recibimos su solicitud Starter ({request_id}). "
            "Le activamos en ≤24h hábiles por email."
        )
    else:
        message = (
            f"We received your Starter request ({request_id}). "
            "We activate within 24 business hours by email."
        )

    return {
        "ok": True,
        "request_id": request_id,
        "email_sent": bool(ack.get("sent")),
        "notify_sent": bool(notify.get("sent")),
        "message": message,
    }


@router.post("/v1/contact")
def contact_request(body: dict):
    """Developer / plan access form from landing."""
    email = (body.get("email") or "").strip()
    use_case = (body.get("use_case") or body.get("message") or "").strip()
    plan = (body.get("plan") or "free").strip().lower()
    lang = (body.get("lang") or "en").strip().lower()[:2]

    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="valid email is required")
    if not use_case or len(use_case) < 10:
        raise HTTPException(status_code=400, detail="use_case is required (min 10 chars)")

    if plan not in ("pro", "starter"):
        db = get_db()
        chat_id = f"web-{uuid.uuid4().hex[:10]}"
        db.execute(
            """
            INSERT INTO contacts (chat_id, first_name, username, last_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (chat_id, plan, email, f"[{plan}] {use_case[:2000]}"),
        )
        db.commit()
        db.close()

    if plan == "pro":
        from routers.payments import process_pro_subscription_request

        pro = process_pro_subscription_request(
            email=email,
            lang=lang,
            note=use_case,
        )
        return {
            "ok": True,
            "plan": "pro",
            "request_id": pro.get("request_id"),
            "payment_link": pro.get("payment_link"),
            "email_sent": pro.get("email_sent", False),
            "message": pro.get("message", "Pro request received."),
        }

    if plan == "starter":
        starter = process_starter_subscription_request(
            email=email,
            lang=lang,
            note=use_case,
            profile=(body.get("profile") or "").strip(),
            name=(body.get("name") or "").strip(),
        )
        return {
            "ok": True,
            "plan": "starter",
            "request_id": starter.get("request_id"),
            "email_sent": starter.get("email_sent", False),
            "message": starter.get("message", "Starter request received."),
        }

    return {"ok": True, "message": "Thanks — we'll reply within 24 hours."}
