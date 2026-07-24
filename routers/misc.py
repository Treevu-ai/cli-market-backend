"""Endpoints that don't fit a bigger domain.

Endpoints:
  POST /favorites             Add/remove/list user favorite products
  POST /v1/utils/exchange     Static currency conversion
  POST /telegram/webhook      Telegram bot inbound webhook
  POST /whatsapp/webhook      WhatsApp (Twilio) bot inbound webhook
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from market_core import COUNTRIES, FX_PEN_PER_UNIT, LINES, convert_currency, get_db
from market_stats import MCP_TOOLS
from server_deps import get_db_dep, require_api_key

logger = logging.getLogger("market.server").getChild("misc")

router = APIRouter(tags=["misc"])


# ── Favorites ─────────────────────────────────────────────────────────────────

@router.post("/favorites")
def favorites(body: dict, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    """Manage favorite products. action ∈ {'list', 'add', 'remove'}."""
    username = require_api_key(authorization)
    action = body.get("action", "list")
    if action == "add":
        db.execute(
            "INSERT OR IGNORE INTO app_favorites (username, product_id, name, store) VALUES (?,?,?,?)",
            (
                username,
                body.get("product_id", ""),
                body.get("name", ""),
                body.get("store", ""),
            ),
        )
        db.commit()
    elif action == "remove":
        db.execute(
            "DELETE FROM app_favorites WHERE username=? AND product_id=?",
            (username, body.get("product_id", "")),
        )
        db.commit()
    rows = db.execute(
        "SELECT product_id, name, store FROM app_favorites WHERE username=? ORDER BY product_id",
        (username,),
    ).fetchall()
    return {"favorites": [dict(r) for r in rows], "total": len(rows)}


# ── Currency conversion (static table) ────────────────────────────────────────

@router.post("/v1/utils/exchange")
def utils_exchange(body: dict):
    """Static currency conversion. Use /checkout/rates for live Wise rates."""
    amount = body.get("amount", 0)
    frm = body.get("from", "PEN").upper()
    to = body.get("to", "PEN").upper()
    if frm not in FX_PEN_PER_UNIT or to not in FX_PEN_PER_UNIT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported currency. Supported: {list(FX_PEN_PER_UNIT.keys())}",
        )
    try:
        converted = round(convert_currency(float(amount), frm, to), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid amount")
    return {
        "amount": amount,
        "from": frm,
        "to": to,
        "converted": converted,
        "rate": round(convert_currency(1.0, frm, to), 6),
    }


# ── Telegram + WhatsApp bot webhooks ─────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "")  # e.g. "whatsapp:+14155238886"
WHATSAPP_ALLOWED_NUMBERS = {
    n.strip() for n in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(",") if n.strip()
}


async def _send_telegram(chat_id: str, text: str) -> bool:
    if not TELEGRAM_TOKEN:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
            return r.status_code == 200
    except Exception:
        return False


async def _send_whatsapp(to_number: str, html_text: str) -> bool:
    """to_number must already carry the "whatsapp:" prefix (Twilio's From/To format)."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER):
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
                data={
                    "From": TWILIO_WHATSAPP_NUMBER,
                    "To": to_number,
                    "Body": _html_to_whatsapp(html_text),
                },
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            )
            return r.status_code in (200, 201)
    except Exception:
        return False


def _html_to_whatsapp(text: str) -> str:
    """Twilio/WhatsApp uses *bold* (asterisks), not <b> tags — convert the same
    reply text both bots share instead of hand-formatting it twice (that split is
    exactly the kind of drift this session kept finding elsewhere)."""
    return text.replace("<b>", "*").replace("</b>", "*")


def _validate_twilio_signature(url: str, params: dict, signature: str) -> bool:
    """https://www.twilio.com/docs/usage/webhooks/webhooks-security — HMAC-SHA1 over
    the request URL followed by each POST param (sorted by key), keyed by the auth token."""
    if not TWILIO_AUTH_TOKEN or not signature:
        return False
    data = url
    for key in sorted(params.keys()):
        data += key + str(params[key])
    digest = hmac.new(TWILIO_AUTH_TOKEN.encode(), data.encode("utf-8"), hashlib.sha1).digest()
    computed = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed, signature)


def _bot_command_reply(text: str, *, first_name: str = "") -> str:
    """Shared command handler for Telegram + WhatsApp — same commands, same data,
    HTML-ish <b> markup that each channel's sender renders for its own platform
    (Telegram natively, _html_to_whatsapp() for Twilio). One copy of this logic
    so the two channels can never drift the way STORES did across ~20 modules today."""
    if text in ("/start", "hola", "hi", "hello"):
        reply = (
            f"Hola <b>{first_name}</b> \U0001f44b\n\n"
            "Soy el bot de <b>CLI Market</b> — infraestructura de comercio para agentes de IA.\n\n"
            "<b>Comandos:</b>\n"
            "/search leche — buscar productos\n"
            "/status — estado\n"
            "/coverage — cobertura\n"
            "/pricing — acceso\n"
            "/docs — docs\n"
            "/help — ayuda"
        )
    elif text.startswith("/search") or text.startswith("buscar"):
        query = text.replace("/search", "").replace("buscar", "").strip() or "leche"
        reply = f"\U0001f50d <b>Buscando:</b> {query}\n\n"
        try:
            db_q = get_db()
            try:
                rows = db_q.execute(
                    "SELECT * FROM price_snapshots WHERE name LIKE ? "
                    "ORDER BY queried_at DESC LIMIT 5",
                    (f"%{query}%",),
                ).fetchall()
            finally:
                db_q.close()
            if rows:
                for r in rows:
                    reply += f"• <b>{r['name']}</b>\n  {r['store_name']} — {r['currency']} {r['price']}\n"
                reply += f"\n{len(rows)} resultados del data moat."
            else:
                reply += "No hay datos todavía."
        except Exception:
            reply += "Error consultando."
    elif text.startswith("/status") or text == "status":
        from store_credentials import get_all_stores

        reply = (
            f"<b>CLI Market</b> — ONLINE\n━━━━━━━━━\n"
            f"• {len(get_all_stores())} retailers en {len(LINES)} líneas\n"
            f"• {len(COUNTRIES)} países\n"
            f"• {MCP_TOOLS} MCP tools\n"
            "• API: cli-market-api.fly.dev\n"
            "• Pro: cli-market.dev/#pricing"
        )
    elif text.startswith("/coverage") or text in ("coverage", "cobertura"):
        from store_credentials import get_all_stores

        reply = "<b>Cobertura por línea:</b>\n"
        all_stores = get_all_stores()
        for lk in LINES:
            c = sum(1 for v in all_stores.values() if v.get("line") == lk)
            reply += f"{LINES[lk]['emoji']} {LINES[lk]['name']}: {c}\n"
        reply += "\n<b>Por pais:</b>\n"
        for _ck, cv in COUNTRIES.items():
            reply += f"{cv['name']}: {len(cv['stores'])}\n"
    elif text in ("/pricing", "pricing", "precio", "costo"):
        from market_billing import price_label_for_plan
        reply = (
            "<b>Planes CLI Market:</b>\n"
            "• Free: 1,000 req/día — pip install cli-market\n"
            f"• Starter: {price_label_for_plan('starter')} — export + alertas\n"
            f"• Pro: {price_label_for_plan('pro')} — cli-market.dev/#pricing\n"
            "• Enterprise: hello@cli-market.dev\n\n"
            "Docs: cli-market.dev/docs"
        )
    elif text in ("/docs", "docs", "api"):
        reply = (
            "<b>Documentación:</b>\n"
            "• Swagger: /docs\n"
            "• llms.txt: cli-market.dev/llms.txt\n"
            "• README: github.com/Treevu-ai/cli-market-world"
        )
    else:
        reply = "<b>CLI Market Bot</b>\n\nComandos: /search /status /coverage /pricing /docs /help"
    return reply


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Webhook endpoint registered in @BotFather → receives Telegram updates."""
    if not TELEGRAM_TOKEN:
        return {"status": "disabled", "hint": "Set TELEGRAM_BOT_TOKEN env var"}
    try:
        body = await request.json()
    except Exception:
        return {"status": "invalid_json"}
    message = body.get("message", {})
    chat = message.get("chat", {})
    text = (message.get("text") or "").strip().lower()
    chat_id = str(chat.get("id", ""))
    first_name = chat.get("first_name", "")
    if not text or not chat_id:
        return {"status": "no_message"}
    try:
        db = get_db()
        try:
            db.execute(
                "INSERT OR REPLACE INTO contacts (chat_id, first_name, username, last_message, created_at) "
                "VALUES (?,?,?,?,datetime('now'))",
                (chat_id, first_name, chat.get("username", ""), text),
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("failed to persist telegram contact chat_id=%s", chat_id, exc_info=True)

    reply = _bot_command_reply(text, first_name=first_name)
    await _send_telegram(chat_id, reply)
    return {"status": "ok", "reply": reply[:100]}


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    """Webhook registered in the Twilio WhatsApp sender config → receives inbound
    WhatsApp messages. Shares _bot_command_reply() with the Telegram bot (see that
    function's docstring) so the two channels never answer differently to the same
    command."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER):
        return {
            "status": "disabled",
            "hint": "Set TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_WHATSAPP_NUMBER env vars",
        }
    form = await request.form()
    params = dict(form)

    signature = request.headers.get("X-Twilio-Signature", "")
    if not _validate_twilio_signature(str(request.url), params, signature):
        raise HTTPException(status_code=403, detail="invalid_signature")

    from_number = str(params.get("From", ""))  # "whatsapp:+51987654321"
    text = str(params.get("Body", "")).strip().lower()
    profile_name = str(params.get("ProfileName", ""))
    if not text or not from_number:
        return {"status": "no_message"}

    if WHATSAPP_ALLOWED_NUMBERS:
        bare_number = from_number.removeprefix("whatsapp:")
        if bare_number not in WHATSAPP_ALLOWED_NUMBERS and from_number not in WHATSAPP_ALLOWED_NUMBERS:
            return {"status": "not_allowed"}

    try:
        db = get_db()
        try:
            db.execute(
                "INSERT OR REPLACE INTO contacts (chat_id, first_name, username, last_message, created_at) "
                "VALUES (?,?,?,?,datetime('now'))",
                (from_number, profile_name, "", text),
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("failed to persist whatsapp contact from=%s", from_number, exc_info=True)

    reply = _bot_command_reply(text, first_name=profile_name)
    await _send_whatsapp(from_number, reply)
    return {"status": "ok", "reply": reply[:100]}
