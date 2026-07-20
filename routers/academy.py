"""Academy waitlist — self-serve signup for the CLI Market Academy landing.

Endpoints:
  POST /v1/academy/waitlist   Public waitlist form (academy.cli-market.dev)
                                → stores the lead, notifies ops.
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

router = APIRouter(tags=["academy"])
logger = logging.getLogger("market.server").getChild("academy")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TRACKS = {"Procure", "Intelligence", "Ambos", "No sé"}
NOTIFY_EMAIL = os.getenv("BILLING_NOTIFY_EMAIL", "hello@cli-market.dev")


def _ensure_schema() -> None:
    db = get_db()
    if market_core.USE_PG:
        db.execute("""
            CREATE TABLE IF NOT EXISTS academy_waitlist (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                rol TEXT NOT NULL,
                track TEXT NOT NULL,
                pais TEXT NOT NULL,
                empresa TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS academy_waitlist (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                rol TEXT NOT NULL,
                track TEXT NOT NULL,
                pais TEXT NOT NULL,
                empresa TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    db.commit()
    db.close()


def _insert_waitlist(*, email: str, rol: str, track: str, pais: str, empresa: str) -> str:
    """Id generated in Python (like taller_registrations' TALLER-<uuid>) —
    psycopg2 never populates cursor.lastrowid, so it silently returns 0 on
    every Postgres (production) insert."""
    _ensure_schema()
    entry_id = f"ACADEMY-{uuid.uuid4().hex[:8].upper()}"
    db = get_db()
    db.execute(
        "INSERT INTO academy_waitlist (id, email, rol, track, pais, empresa) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (entry_id, email, rol, track, pais, empresa),
    )
    db.commit()
    db.close()
    return entry_id


def _send_ops_notification(*, email: str, rol: str, track: str, pais: str, empresa: str) -> dict:
    from market_connectors.email_outbound import _send, _smtp_configured

    if not _smtp_configured():
        return {"sent": False, "reason": "smtp_not_configured"}

    subject = f"[Academy waitlist] {email} — {track}"
    text = (
        "Nuevo registro — CLI Market Academy waitlist\n\n"
        f"Email: {email}\n"
        f"Rol: {rol}\n"
        f"Track: {track}\n"
        f"País: {pais}\n"
        f"Empresa: {empresa or '(sin especificar)'}\n"
    )
    return _send(NOTIFY_EMAIL, subject, text, f"<pre>{text}</pre>")


@router.post("/v1/academy/waitlist")
def academy_waitlist(body: dict):
    """Public Academy waitlist — academy.cli-market.dev form posts here."""
    check_rate_limit("academy-waitlist")

    email = (body.get("email") or "").strip().lower()
    rol = (body.get("rol") or "").strip()
    track = (body.get("track") or "").strip()
    pais = (body.get("pais") or "").strip().upper()
    empresa = (body.get("empresa") or "").strip()

    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="valid email is required")
    if not rol:
        raise HTTPException(status_code=400, detail="rol is required")
    if track not in _TRACKS:
        raise HTTPException(status_code=400, detail=f"track must be one of: {', '.join(sorted(_TRACKS))}")
    if not pais:
        raise HTTPException(status_code=400, detail="pais is required")

    entry_id = _insert_waitlist(email=email, rol=rol, track=track, pais=pais, empresa=empresa)

    try:
        from market_funnel import record_funnel_event

        record_funnel_event(
            "academy_waitlist",
            meta={"track": track, "pais": pais, "entry_id": entry_id},
            dedupe=False,
        )
    except Exception:
        logger.debug("record_funnel_event(academy_waitlist) failed", exc_info=True)

    notify = _send_ops_notification(email=email, rol=rol, track=track, pais=pais, empresa=empresa)

    return {
        "registered": True,
        "id": entry_id,
        "message": "Recibimos su solicitud. Le escribiremos con el siguiente paso del track.",
        "ops_notified": notify.get("sent", False),
    }
