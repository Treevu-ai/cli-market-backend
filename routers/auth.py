"""Authentication, API keys, and subscription tier endpoints.

Endpoints:
  POST   /auth/login                     Username/password → session token
  GET    /auth/whoami                    Token → username + tier
  POST   /auth/keys                      Create API key (sk-...) — scopes: read | read_write
  GET    /auth/keys                      List user's API keys (prefix only, no secret)
  DELETE /auth/keys/{key_id}             Revoke an API key
  GET    /auth/subscription              Current tier + limits
  POST   /auth/referral                  Register/refresh a referral code (install tracking)
  GET    /auth/referral/stats            Referral stats for the authenticated user
  POST   /auth/procure-magic-exchange    Exchange one-time Procure onboarding token for API credentials
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from market_core import (
    db_create_api_key,
    db_get_subscription,
    db_get_users,
    db_list_api_keys,
    db_revoke_api_key,
    db_save_user,
    db_set_subscription,
)
from server_deps import (
    check_auth_brute_force,
    check_rate_limit,
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


class ProcureMagicExchangeRequest(BaseModel):
    token: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    ref_code: str | None = None
    email: str | None = None


@router.post("/auth/register")
def register(body: RegisterRequest = Body(default=RegisterRequest())):
    """Create a new API key. Public endpoint — rate limited.

    Optional `ref_code` credits the referrer (see
    market_billing.apply_referral_activation) when this is a referred signup.
    Optional `email` is stored for outreach (never shown in API responses).
    """
    check_rate_limit("auth")
    username = f"user-{uuid.uuid4().hex[:12]}"
    email = (body.email or "").strip().lower() or None
    # Random password — access is via sk- API key, not password login.
    db_save_user(username, hash_password(uuid.uuid4().hex), None, email)
    db_set_subscription(username, "free")
    result = db_create_api_key(username, "read_write", "register")
    try:
        from market_funnel import record_funnel_event
        record_funnel_event("register", username=username, dedupe=True)
    except Exception:
        logger.debug("record_funnel_event(register) failed", exc_info=True)
    if body.ref_code:
        try:
            from market_billing import apply_referral_activation
            apply_referral_activation(body.ref_code, username)
        except Exception:
            logger.debug("apply_referral_activation failed", exc_info=True)
    try:
        import sys, os
        _ops = os.path.join(os.path.dirname(__file__), "..", "ops")
        if _ops not in sys.path:
            sys.path.insert(0, _ops)
        from billing_slack import notify_new_registration
        notify_new_registration(
            username=username,
            email=email or "",
            ref_code=body.ref_code or "",
            api_key_prefix=result.get("prefix", ""),
        )
    except Exception:
        logger.debug("notify_new_registration failed", exc_info=True)
    return {
        "username": username,
        "api_key": result["key"],
        "prefix": result["prefix"],
        "scopes": result["scopes"],
        "message": "API key generada. Guardala — no se vuelve a mostrar.",
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
def referral_register(body: ReferralRegisterRequest, authorization: str | None = Header(None)):
    """Register or refresh a user's referral code.

    Idempotent: calling again with the same code is a no-op;
    calling with a new code replaces the old one.
    """
    from market_core import get_db
    username = ""
    try:
        username = require_user(authorization)
    except HTTPException:
        pass  # anonymous installs are tracked without a username

    code = (body.ref_code or "").strip()[:16]
    if not code:
        raise HTTPException(status_code=422, detail="ref_code is required")

    db = get_db()
    try:
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
    finally:
        db.close()


@router.get("/auth/referral/stats")
def referral_stats(authorization: str | None = Header(None)):
    """Return referral stats for the authenticated user."""
    from market_core import get_db
    username = require_user(authorization)
    db = get_db()
    try:
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
    finally:
        db.close()


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
