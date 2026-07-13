"""Taller registration — self-serve workshop signup.

Endpoints:
  POST /v1/taller/registro   Public registration form (flyer modal) → confirmation
                              email with payment details + ops notification.
"""

from __future__ import annotations

import logging
import os
import re
import uuid

from fastapi import APIRouter, HTTPException

import market_core
from market_core import get_db
from server_deps import check_rate_limit

router = APIRouter(tags=["taller"])
logger = logging.getLogger("market.server").getChild("taller")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PAGO_METHODS = {"Yape", "Transferencia bancaria"}

YAPE_PLIN_NUMBER = os.getenv("YAPE_PLIN_NUMBER", "902126765")
BCP_ACCOUNT_NUMBER = os.getenv("BCP_ACCOUNT_NUMBER", "57005165126053")
BCP_CCI = os.getenv("BCP_CCI", "00257010516512605303")
NOTIFY_EMAIL = os.getenv("BILLING_NOTIFY_EMAIL", "hello@cli-market.dev")


def _ensure_schema() -> None:
    db = get_db()
    if market_core.USE_PG:
        db.execute("""
            CREATE TABLE IF NOT EXISTS taller_registrations (
                id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                email TEXT NOT NULL,
                telefono TEXT NOT NULL,
                pago TEXT NOT NULL,
                comentario TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS taller_registrations (
                id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                email TEXT NOT NULL,
                telefono TEXT NOT NULL,
                pago TEXT NOT NULL,
                comentario TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    db.commit()
    db.close()


def _insert_registration(*, nombre: str, email: str, telefono: str, pago: str, comentario: str) -> str:
    """Generates the id in Python (like retailer_applications' RET-<uuid>)
    instead of relying on cursor.lastrowid — psycopg2 never populates it,
    so it silently returned 0 for every Postgres (production) insert."""
    _ensure_schema()
    reg_id = f"TALLER-{uuid.uuid4().hex[:8].upper()}"
    db = get_db()
    db.execute(
        "INSERT INTO taller_registrations (id, nombre, email, telefono, pago, comentario) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (reg_id, nombre, email, telefono, pago, comentario),
    )
    db.commit()
    db.close()
    return reg_id


def _payment_instructions(pago: str) -> str:
    if pago == "Yape":
        return f"Yape / Plin al {YAPE_PLIN_NUMBER}"
    return (
        f"Transferencia BCP Soles — Cuenta: {BCP_ACCOUNT_NUMBER} · "
        f"CCI: {BCP_CCI}"
    )


def _send_confirmation_email(*, nombre: str, email: str, pago: str) -> dict:
    from market_connectors.email_outbound import _send, _smtp_configured

    if not _smtp_configured():
        logger.warning("SMTP not configured — taller confirmation not sent to %s", email)
        return {"sent": False, "reason": "smtp_not_configured"}

    instructions = _payment_instructions(pago)
    subject = "Registro confirmado — Taller Inteligencia de Mercados · CLI Market"
    text = f"""Hola {nombre},

Tu registro al Taller de Inteligencia de Mercados quedó confirmado.

Método de pago elegido: {pago}
{instructions}

Envía tu comprobante por WhatsApp al +51 {YAPE_PLIN_NUMBER} para asegurar tu cupo.

¿Dudas? Responde este correo o escríbenos por WhatsApp.

— Ricardo Cuba · Fundador de CLI Market y Procure Copilot
hello@cli-market.dev
"""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0a0a0b;font-family:ui-sans-serif,system-ui,sans-serif;color:#e5e2e3;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0b;padding:40px 0;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="background:#131314;border:1px solid #3b4a44;border-radius:12px;max-width:560px;width:100%;">
<tr><td style="padding:32px 36px;">
<p style="margin:0 0 4px;font-family:monospace;font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:#3afecf;">TALLER · CLI MARKET</p>
<h1 style="margin:0 0 16px;font-size:22px;color:#fff;">Registro confirmado</h1>
<p style="margin:0 0 20px;font-size:14px;color:#b9cac2;line-height:1.6;">
Hola <strong style="color:#fff">{nombre}</strong>, tu lugar en el Taller de Inteligencia de Mercados quedó reservado.
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#1c1b1c;border:1px solid #3b4a44;border-radius:8px;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#3afecf;">Método elegido: {pago}</p>
<p style="margin:0;font-size:13px;color:#b9cac2;line-height:1.7;">{instructions}</p>
</td></tr>
</table>
<p style="margin:0 0 20px;font-size:13px;color:#b9cac2;line-height:1.6;">
Envía tu comprobante por WhatsApp al <strong style="color:#fff">+51 {YAPE_PLIN_NUMBER}</strong> para asegurar tu cupo.
</p>
<p style="margin:0;font-size:12px;color:#b9cac2;">¿Dudas? Responde este correo.</p>
</td></tr>
<tr><td style="padding:20px 36px;border-top:1px solid #3b4a44;">
<p style="margin:0;font-size:12px;color:#b9cac2;">— Ricardo Cuba · Fundador de CLI Market y Procure Copilot · <a href="mailto:hello@cli-market.dev" style="color:#3afecf;text-decoration:none;">hello@cli-market.dev</a></p>
</td></tr>
</table></td></tr></table>
</body></html>"""
    return _send(email, subject, text, html)


def _send_ops_notification(*, nombre: str, email: str, telefono: str, pago: str, comentario: str) -> dict:
    from market_connectors.email_outbound import _send, _smtp_configured

    if not _smtp_configured():
        return {"sent": False, "reason": "smtp_not_configured"}

    subject = f"[Taller registro] {nombre} — {pago}"
    text = (
        "Nuevo registro — Taller Inteligencia de Mercados\n\n"
        f"Nombre: {nombre}\n"
        f"Email: {email}\n"
        f"WhatsApp: {telefono}\n"
        f"Método de pago preferido: {pago}\n"
        f"Comentario: {comentario or '(sin comentario)'}\n"
    )
    return _send(NOTIFY_EMAIL, subject, text, f"<pre>{text}</pre>")


@router.post("/v1/taller/registro")
def registro_taller(body: dict):
    """Public workshop registration — flyer modal posts here."""
    check_rate_limit("taller-registro")

    nombre = (body.get("nombre") or "").strip()
    email = (body.get("email") or "").strip().lower()
    telefono = (body.get("telefono") or "").strip()
    pago = (body.get("pago") or "").strip()
    comentario = (body.get("comentario") or "").strip()

    if not nombre or len(nombre) < 2:
        raise HTTPException(status_code=400, detail="nombre is required")
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="valid email is required")
    if not telefono or len(telefono) < 6:
        raise HTTPException(status_code=400, detail="telefono is required")
    if pago not in _PAGO_METHODS:
        raise HTTPException(status_code=400, detail=f"pago must be one of: {', '.join(sorted(_PAGO_METHODS))}")

    reg_id = _insert_registration(
        nombre=nombre, email=email, telefono=telefono, pago=pago, comentario=comentario,
    )

    try:
        from market_funnel import record_funnel_event

        record_funnel_event(
            "taller_registro",
            meta={"pago": pago, "registration_id": reg_id},
            dedupe=False,
        )
    except Exception:
        logger.debug("record_funnel_event(taller_registro) failed", exc_info=True)

    ack = _send_confirmation_email(nombre=nombre, email=email, pago=pago)
    notify = _send_ops_notification(
        nombre=nombre, email=email, telefono=telefono, pago=pago, comentario=comentario,
    )

    return {
        "registered": True,
        "id": reg_id,
        "message": "Registro confirmado. Revisa tu correo para los datos de pago.",
        "email_sent": ack.get("sent", False),
        "ops_notified": notify.get("sent", False),
    }
