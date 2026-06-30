"""Authentication, API keys, and subscription tier endpoints.

Endpoints:
  POST   /auth/register           Email + OTP → verified user + API key
  POST   /auth/verify-email       Verify OTP code to complete registration
  POST   /auth/login              Username/password → session token
  GET    /auth/whoami             Token → username + tier
  POST   /auth/keys               Create API key (sk-...) — scopes: read | read_write
  GET    /auth/keys               List user's API keys (prefix only, no secret)
  DELETE /auth/keys/{key_id}      Revoke an API key
  GET    /auth/subscription       Current tier + limits
  POST   /auth/referral           Register/refresh a referral code (install tracking)
  GET    /auth/referral/stats     Referral stats for the authenticated user
  POST   /auth/procure-magic-exchange  Exchange one-time Procure onboarding token
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from market_core import (
    USE_PG,
    db_create_api_key,
    db_get_subscription,
    db_get_users,
    db_list_api_keys,
    db_revoke_api_key,
    db_save_user,
    db_set_subscription,
    get_db,
)
from server_deps import (
    check_auth_brute_force,
    check_rate_limit,
    get_db_dep,
    hash_password,
    record_auth_failure,
    require_user,
    verify_password,
)

router = APIRouter(tags=["auth"])
logger = logging.getLogger("market.server").getChild("auth")


# ── Request models ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateApiKeyRequest(BaseModel):
    scopes: str = "read"
    label: str = ""


class RefreshRequest(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    email: str
    ref_code: str | None = None


class VerifyEmailRequest(BaseModel):
    email: str
    code: str


class ProcureMagicExchangeRequest(BaseModel):
    token: str = ""


# ── Email verification helpers ────────────────────────────────────────────────

_VERIFY_TTL = int(os.getenv("EMAIL_VERIFY_TTL_SECONDS", "600"))  # 10 min
_VERIFY_CODE_LEN = 6


def _ensure_pending_registrations_schema() -> None:
    db = get_db()
    if USE_PG:
        db.execute("""
            CREATE TABLE IF NOT EXISTS pending_registrations (
                email TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                ref_code TEXT DEFAULT '',
                expires_at DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (email)
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS pending_registrations (
                email TEXT NOT NULL PRIMARY KEY,
                code_hash TEXT NOT NULL,
                ref_code TEXT DEFAULT '',
                expires_at REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    db.commit()
    db.close()


def _generate_otp() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(_VERIFY_CODE_LEN))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _send_verification_code(email: str, code: str) -> bool:
    """Send OTP code via email. Returns True on success."""
    try:
        from market_connectors.email_outbound import _send, _smtp_configured

        if not _smtp_configured():
            logger.warning("SMTP not configured — verification email not sent to %s", email)
            return False
        subject = "CLI Market — Código de verificación"
        text = (
            f"Tu código de verificación es: {code}\n\n"
            f"Expira en {_VERIFY_TTL // 60} minutos.\n\n"
            "Si no solicitaste este código, ignora este mensaje."
        )
        html = (
            f"<h2>Código de verificación</h2>"
            f"<p style='font-size:32px;font-weight:bold;letter-spacing:8px'>{code}</p>"
            f"<p>Expira en {_VERIFY_TTL // 60} minutos.</p>"
            f"<p><small>Si no solicitaste este código, ignora este mensaje.</small></p>"
        )
        result = _send(email, subject, text, html)
        return result.get("sent", False)
    except Exception:
        logger.exception("Failed to send verification email to %s", email)
        return False


def _validate_email(email: str) -> str:
    """Basic email validation. Returns normalized email or raises HTTPException."""
    import re

    normalized = (email or "").strip().lower()
    if not normalized or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", normalized):
        raise HTTPException(status_code=422, detail="Email inválido")
    return normalized


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/auth/register")
def register(body: RegisterRequest):
    """Start registration — sends a verification code to the provided email."""
    check_rate_limit("auth")
    email = _validate_email(body.email)

    _ensure_pending_registrations_schema()
    code = _generate_otp()
    code_hash = _hash_code(code)
    expires_at = time.time() + _VERIFY_TTL

    db = get_db()
    if USE_PG:
        db.execute(
            "INSERT INTO pending_registrations (email, code_hash, ref_code, expires_at) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT(email) DO UPDATE SET code_hash=EXCLUDED.code_hash, "
            "ref_code=EXCLUDED.ref_code, expires_at=EXCLUDED.expires_at",
            (email, code_hash, body.ref_code or "", expires_at),
        )
    else:
        db.execute(
            "INSERT INTO pending_registrations (email, code_hash, ref_code, expires_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash, "
            "ref_code=excluded.ref_code, expires_at=excluded.expires_at",
            (email, code_hash, body.ref_code or "", expires_at),
        )
    db.commit()
    db.close()

    sent = _send_verification_code(email, code)
    masked = email[0] + "***" + email[email.index("@"):]
    return {
        "status": "verification_required",
        "email": masked,
        "email_sent": sent,
        "expires_in_seconds": _VERIFY_TTL,
        "message": f"Código de verificación enviado a {masked}. Usa POST /auth/verify-email para completar.",
    }


@router.post("/auth/verify-email")
def verify_email(body: VerifyEmailRequest):
    """Complete registration by verifying the OTP code sent to email."""
    check_rate_limit("auth")
    email = _validate_email(body.email)
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="Código requerido")

    _ensure_pending_registrations_schema()
    db = get_db()
    row = db.execute(
        "SELECT code_hash, ref_code, expires_at FROM pending_registrations WHERE email=?",
        (email,),
    ).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="No hay registro pendiente para este email")

    if time.time() > row["expires_at"]:
        db.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
        db.commit()
        db.close()
        raise HTTPException(status_code=410, detail="Código expirado. Registra de nuevo.")

    if _hash_code(code) != row["code_hash"]:
        db.close()
        raise HTTPException(status_code=401, detail="Código incorrecto")

    ref_code = row["ref_code"] or ""
    db.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
    db.commit()
    db.close()

    username = f"user-{uuid.uuid4().hex[:12]}"
    db_save_user(username, hash_password(uuid.uuid4().hex), None, email)
    db_set_subscription(username, "free")
    result = db_create_api_key(username, "read_write", "register")
    try:
        from market_funnel import record_funnel_event

        record_funnel_event("register", username=username, dedupe=True)
    except Exception:
        logger.debug("record_funnel_event(register) failed", exc_info=True)
    if ref_code:
        try:
            from market_billing import apply_referral_activation

            apply_referral_activation(ref_code, username)
        except Exception:
            logger.debug("apply_referral_activation failed", exc_info=True)
    try:
        import sys

        _ops = os.path.join(os.path.dirname(__file__), "..", "ops")
        if _ops not in sys.path:
            sys.path.insert(0, _ops)
        from billing_slack import notify_new_registration

        notify_new_registration(
            username=username,
            email=email,
            ref_code=ref_code,
            api_key_prefix=result.get("prefix", ""),
        )
    except Exception:
        logger.debug("notify_new_registration failed", exc_info=True)
    try:
        from market_connectors.email_outbound import _send, _smtp_configured

        if _smtp_configured():
            _send(
                email,
                "Bienvenido a CLI Market — próximos pasos",
                (
                    f"Hola {username},\n\n"
                    "Tu cuenta está verificada. Aquí tus próximos pasos:\n\n"
                    '1. Primera búsqueda:\n   market search "leche" --country PE\n\n'
                    "2. Conectar con IA (MCP):\n   market mcp-setup\n\n"
                    '3. Comparar precios:\n   market compare "arroz" --country PE\n\n'
                    "Docs: https://cli-market.dev/docs\n\n"
                    "— CLI Market"
                ),
                (
                    "<h2 style='color:#3afecf;'>Bienvenido a CLI Market</h2>"
                    f"<p>Hola <b>{username}</b>, tu cuenta está verificada.</p>"
                    "<h3>Próximos pasos:</h3>"
                    "<ol>"
                    '<li><b>Primera búsqueda:</b><br><code>market search "leche" --country PE</code></li>'
                    "<li><b>Conectar con IA (MCP):</b><br><code>market mcp-setup</code></li>"
                    '<li><b>Comparar precios:</b><br><code>market compare "arroz" --country PE</code></li>'
                    "</ol>"
                    '<p><a href="https://cli-market.dev/docs">Documentación completa →</a></p>'
                ),
            )
    except Exception:
        logger.debug("welcome email failed", exc_info=True)

    return {
        "username": username,
        "api_key": result["key"],
        "prefix": result["prefix"],
        "scopes": result["scopes"],
        "email": email,
        "verified": True,
        "message": "Email verificado. API key generada — guardala, no se vuelve a mostrar.",
        "next_steps": [
            {
                "action": "first_search",
                "command": 'market search "leche" --country PE',
                "description": "Haz tu primera búsqueda para ver precios reales",
            },
            {
                "action": "mcp_setup",
                "command": "market mcp-setup",
                "description": "Conecta con Claude/ChatGPT via MCP",
            },
            {
                "action": "compare",
                "command": 'market compare "arroz" --country PE',
                "description": "Compara precios entre tiendas",
            },
        ],
    }


@router.post("/auth/login")
def login(body: LoginRequest):
    check_rate_limit("auth")
    check_auth_brute_force(body.username)
    users = db_get_users()
    if not users:
        admin_pass = os.getenv("MARKET_ADMIN_PASSWORD")
        if not admin_pass:
            raise HTTPException(status_code=500, detail="MARKET_ADMIN_PASSWORD not set")
        db_save_user("admin", hash_password(admin_pass), str(uuid.uuid4()))
        users = db_get_users()
    user = users.get(body.username)
    if not user or not verify_password(body.password, user["password"]):
        record_auth_failure(body.username)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    from market_core.auth_tokens import issue_session_tokens

    tokens = issue_session_tokens(body.username)
    try:
        from market_funnel import record_funnel_event

        record_funnel_event("login", username=body.username, dedupe=False)
    except Exception:
        logger.debug("record_funnel_event(login) failed", exc_info=True)
    return {"message": "Autenticado", "username": body.username, **tokens}


@router.post("/auth/refresh")
def refresh_session(body: RefreshRequest):
    check_rate_limit("auth")
    from market_core.auth_tokens import rotate_token

    try:
        tokens = rotate_token(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"ok": True, **tokens}


@router.post("/auth/revoke")
def revoke_session(authorization: str | None = Header(None)):
    username = require_user(authorization)
    from market_core.auth_tokens import revoke_all_tokens

    revoke_all_tokens(username)
    return {"ok": True, "revoked": username}


@router.get("/auth/whoami")
def whoami(authorization: str | None = Header(None)):
    username = require_user(authorization)
    sub = db_get_subscription(username) or {}
    tier = (sub.get("tier") or "free").lower()
    return {
        "username": username,
        "tier": tier,
        "req_limit_day": sub.get("req_limit_day"),
        "req_limit_min": sub.get("req_limit_min"),
    }


@router.post("/auth/keys")
def create_api_key(body: CreateApiKeyRequest, authorization: str | None = Header(None)):
    username = require_user(authorization)
    if body.scopes not in ("read", "read_write"):
        raise HTTPException(status_code=400, detail="Scopes must be 'read' or 'read_write'")
    result = db_create_api_key(username, body.scopes, body.label)
    return {
        "message": "API key created. Store it safely — it won't be shown again.",
        "key": result["key"],
        "prefix": result["prefix"],
        "scopes": result["scopes"],
        "label": result["label"],
    }


@router.get("/auth/keys")
def list_api_keys(authorization: str | None = Header(None)):
    username = require_user(authorization)
    keys = db_list_api_keys(username)
    return {"keys": keys, "total": len(keys)}


@router.delete("/auth/keys/{key_id}")
def revoke_api_key(key_id: int, authorization: str | None = Header(None)):
    username = require_user(authorization)
    ok = db_revoke_api_key(username, key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"message": "Key revoked"}


@router.get("/auth/account")
def get_account(
    authorization: str | None = Header(None),
    lang: str = "es",
):
    """Customer dashboard payload: tier, usage, limits, upgrade CTA."""
    username = require_user(authorization)
    lang = (lang or "es").strip().lower()[:2]
    from account_service import build_account_summary

    return build_account_summary(username, lang=lang)


@router.get("/auth/subscription")
def get_subscription(authorization: str | None = Header(None)):
    """Preserves the original shape — returns the full subscription record
    plus a count of the user's API keys."""
    username = require_user(authorization)
    sub = db_get_subscription(username)
    keys = db_list_api_keys(username)
    return {"username": username, "subscription": sub, "api_keys": len(keys)}


class ReferralRegisterRequest(BaseModel):
    ref_code: str


@router.post("/auth/referral")
def referral_register(body: ReferralRegisterRequest, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    """Register or refresh a user's referral code.

    Idempotent: calling again with the same code is a no-op;
    calling with a new code replaces the old one.
    """
    username = ""
    try:
        username = require_user(authorization)
    except HTTPException:
        pass  # anonymous installs are tracked without a username

    code = (body.ref_code or "").strip()[:16]
    if not code:
        raise HTTPException(status_code=422, detail="ref_code is required")

    db.execute(
        """
        INSERT INTO referral_codes (ref_code, username, install_count, activated_count)
        VALUES (?, ?, 1, 0)
        ON CONFLICT(ref_code) DO UPDATE SET install_count = referral_codes.install_count + 1
        """,
        [code, username],
    )
    db.commit()
    row = db.execute(
        "SELECT ref_code, username, install_count, activated_count, created_at FROM referral_codes WHERE ref_code = ?",
        [code],
    ).fetchone()
    if row:
        return dict(row)
    return {"ref_code": code, "username": username, "install_count": 1}


@router.get("/auth/referral/stats")
def referral_stats(authorization: str | None = Header(None), db = Depends(get_db_dep)):
    """Return referral stats for the authenticated user."""
    username = require_user(authorization)
    rows = db.execute(
        "SELECT ref_code, install_count, activated_count, created_at FROM referral_codes WHERE username = ? ORDER BY install_count DESC",
        [username],
    ).fetchall()
    total_installs = sum(r["install_count"] for r in rows)
    total_activated = sum(r["activated_count"] for r in rows)
    return {
        "username": username,
        "total_installs": total_installs,
        "total_activated": total_activated,
        "codes": [dict(r) for r in rows],
    }


@router.post("/auth/procure-magic-exchange")
def procure_magic_exchange(body: ProcureMagicExchangeRequest):
    """Exchange a one-time Procure onboarding token for API credentials."""
    from procure_magic import exchange_procure_magic_token, procure_magic_enabled

    if not procure_magic_enabled():
        raise HTTPException(status_code=501, detail="Procure magic link not configured")
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=422, detail="token is required")
    try:
        result = exchange_procure_magic_token(token)
        try:
            from market_funnel import record_funnel_event

            record_funnel_event(
                "procure_magic_exchange_ok",
                username=result.get("username"),
                meta={"tier": result.get("tier")},
            )
        except Exception:
            pass
        return result
    except ValueError as exc:
        try:
            from market_funnel import record_funnel_event

            detail = str(exc)
            reason = "expired" if "expir" in detail else "used" if "used" in detail else "invalid"
            record_funnel_event("procure_magic_exchange_fail", meta={"reason": reason})
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc
