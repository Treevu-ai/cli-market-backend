"""Server-only dependencies shared across FastAPI routers.

These belong neither in market_core (which is data-layer-only, no HTTP concerns)
nor in any single router file. Anything that's both HTTP-related and used by
more than one router lives here.

Contents:
    - Auth: auth_user(), hash_password(), verify_password(), check_auth_brute_force()
    - Rate limit: check_rate_limit() (delegates to market_core.check_rate_limit_sqlite)
    - Constants: DEFAULT_TOKEN, RATE_LIMIT_*, AUTH_*
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Annotated, Generator

logger = logging.getLogger("market.server_deps")

from fastapi import Depends, HTTPException

from market_core import (
    check_rate_limit_sqlite,
    db_check_auth_brute_force,
    db_get_users,
    db_record_auth_failure,
    db_validate_api_key,
    get_db,
)
from market_core.market_db import _DB


def get_db_dep() -> Generator[_DB, None, None]:
    """FastAPI dependency: open one DB connection per request, close on exit."""
    db = get_db()
    try:
        yield db
    finally:
        db.close()


DbDep = Annotated[_DB, Depends(get_db_dep)]


# ── Auth tokens ───────────────────────────────────────────────────────────────

DEFAULT_TOKEN = os.getenv("MARKET_API_TOKEN", "")


def auth_user(token: str) -> str:
    """Resolve a bearer token (or legacy session token, or sk- API key) to a username.

    Raises 401 on invalid credentials.
    """
    if token.startswith("demo-"):
        from market_core.demo_tokens import validate_demo_token

        sess = validate_demo_token(token)
        if not sess:
            raise HTTPException(status_code=401, detail="Demo token expired or invalid. Run: market demo")
        return f"demo:{sess['session_id']}"
    if DEFAULT_TOKEN and token == DEFAULT_TOKEN:
        return "admin"
    if token.startswith("sk-"):
        from market_core.platform_admin import is_platform_admin_api_key

        if is_platform_admin_api_key(token):
            return "admin"
        key_data = db_validate_api_key(token)
        if key_data:
            return key_data["username"]
    from market_core.auth_tokens import lookup_session_token

    session = lookup_session_token(token)
    if session:
        if session.get("expired"):
            raise HTTPException(
                status_code=401,
                detail="Session token expired. Run: market login or refresh.",
                headers={"X-Token-Expired": "true"},
            )
        return session["username"]
    users = db_get_users()
    for username, data in users.items():
        if data.get("token") == token:
            return username
    if token.startswith("sk-"):
        raise HTTPException(
            status_code=401,
            detail="API key inválida o revocada. Generá una nueva con 'market register' o revisá tu dashboard.",
        )
    raise HTTPException(status_code=401, detail="Token inválido. Usá 'market login'.")


# ── Password hashing ──────────────────────────────────────────────────────────

PASSWORD_SCHEME = "pbkdf2_sha256"
_LEGACY_PASSWORD_ITERATIONS = 100_000


def _password_iterations() -> int:
    """Return a safe, configurable PBKDF2 cost for new password hashes."""
    try:
        return max(_LEGACY_PASSWORD_ITERATIONS, int(os.getenv("MARKET_PASSWORD_HASH_ITERATIONS", "600000")))
    except ValueError:
        return 600_000


def hash_password(password: str) -> str:
    iterations = _password_iterations()
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"{PASSWORD_SCHEME}${iterations}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """Verify versioned hashes while retaining the prior format for existing users."""
    if stored.startswith(f"{PASSWORD_SCHEME}$"):
        try:
            _scheme, iterations_raw, salt, expected = stored.split("$", 3)
            iterations = int(iterations_raw)
        except ValueError:
            return False
        if iterations < _LEGACY_PASSWORD_ITERATIONS or not salt or not expected:
            return False
    elif ":" in stored:
        salt, expected = stored.split(":", 1)
        iterations = _LEGACY_PASSWORD_ITERATIONS
    else:
        raise HTTPException(status_code=500, detail="Legacy plaintext password detected. Contact admin.")

    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return hmac.compare_digest(actual, expected)


# ── Brute-force protection ────────────────────────────────────────────────────

AUTH_MAX_ATTEMPTS = 5
AUTH_WINDOW = 300  # 5 minutes

# Kept for backwards-compat imports from market_server (tests reference it)
_auth_attempts: dict[str, list[float]] = {}


def check_auth_brute_force(username: str) -> None:
    db_check_auth_brute_force(username, max_attempts=AUTH_MAX_ATTEMPTS, window_secs=AUTH_WINDOW)


def record_auth_failure(username: str) -> None:
    """Record a failed auth attempt — persisted in DB so it survives restarts."""
    db_record_auth_failure(username)


# ── Rate limiting ─────────────────────────────────────────────────────────────

RATE_LIMIT_MIN = int(os.getenv("RATE_LIMIT_MIN", "60"))
RATE_LIMIT_DAY = int(os.getenv("RATE_LIMIT_DAY", "1000"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))


def check_rate_limit(ip: str) -> None:
    try:
        check_rate_limit_sqlite(
            ip,
            window_secs=RATE_LIMIT_WINDOW,
            max_req=RATE_LIMIT_MIN,
            daily_max=RATE_LIMIT_DAY,
        )
    except Exception as exc:
        if getattr(exc, "status_code", 0) == 429:
            logger.warning("rate_limit.ip ip=%s", ip)
        raise


# ── Auth header helper ───────────────────────────────────────────────────────

def require_user(authorization: str | None) -> str:
    """Common pattern: Authorization header → username. Raises 401 if absent.

    Also applies per-user rate limiting so account-management endpoints
    (e.g. /auth/keys, /auth/revoke) can't be hammered by an authenticated
    user rotating IPs to bypass the IP-only limit.
    """
    if not authorization:
        logger.warning("auth.require_user: missing token")
        raise HTTPException(status_code=401, detail="Sin token")
    username = auth_user(authorization.replace("Bearer ", ""))
    check_user_rate_limit(username)
    return username


def require_admin(authorization: str | None) -> str:
    """Protect ops/admin routes with MARKET_API_TOKEN."""
    if not DEFAULT_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Admin API disabled — set MARKET_API_TOKEN on the server.",
        )
    if not authorization:
        raise HTTPException(status_code=401, detail="Admin token required")
    token = authorization.replace("Bearer ", "").strip()
    if token != DEFAULT_TOKEN:
        raise HTTPException(status_code=401, detail="Admin token invalid")
    return "admin"


def require_checkout_access(username: str) -> None:
    """Raise 403 if user's tier cannot use checkout (unless legacy bypass)."""
    from market_core import user_can_checkout
    from market_billing import checkout_upgrade_detail
    from market_core.demo_tokens import is_demo_username

    if is_demo_username(username):
        raise HTTPException(
            status_code=403,
            detail="Demo tokens cannot checkout. Run: market init",
        )
    if user_can_checkout(username):
        return
    raise HTTPException(
        status_code=403,
        detail=checkout_upgrade_detail(),
    )


# ── Per-user rate limiting ────────────────────────────────────────────────────

TIER_LIMITS: dict[str, tuple[int, int]] = {
    # Keep in sync with market_billing.TIERS["free"] (cli-market-core) — this
    # is only the fallback when a subscription row lacks a stored
    # req_limit_day/min (see _get_user_tier_limits below); registration
    # (routers/auth.py) writes an explicit value at signup time, so this
    # constant drifting from TIERS silently stops mattering for most users
    # until it's the *only* thing checked, which is easy to miss.
    "free":       (15,      10),
    "starter":    (5_000,  120),
    "pro":       (10_000,  300),
    "data":      (100_000, 600),   # Tier 1 data/intel API — see market_billing.TIERS["data"]
    "enterprise": (-1,      -1),   # -1 = unlimited
}


def _get_user_tier_limits(username: str) -> tuple[int, int]:
    """Return (daily_max, per_min_max) from the user's subscription row.

    Delegates to market_billing.db_get_subscription so an expired temporary
    grant (e.g. a referral-earned free Pro month) falls back to free-tier
    limits here too, instead of duplicating that expiry logic in raw SQL.
    """
    from market_billing import db_get_subscription
    sub = db_get_subscription(username)
    tier = (sub.get("tier") or "free").lower()
    defaults = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    daily = int(sub.get("req_limit_day") or defaults[0])
    per_min = int(sub.get("req_limit_min") or defaults[1])
    return daily, per_min


def check_user_rate_limit(username: str) -> None:
    """Apply per-user rate limiting based on subscription tier. Admin bypasses."""
    from market_core.platform_admin import is_platform_admin

    if is_platform_admin(username):
        return
    daily_max, min_max = _get_user_tier_limits(username)
    if daily_max <= 0 or min_max <= 0:
        return  # enterprise / unlimited tier
    try:
        check_rate_limit_sqlite(
            f"u:{username}",
            window_secs=RATE_LIMIT_WINDOW,
            max_req=min_max,
            daily_max=daily_max,
        )
    except Exception as exc:
        if getattr(exc, "status_code", 0) == 429:
            logger.warning("rate_limit.user user=%s", username)
        raise


def require_api_key(authorization: str | None) -> str:
    """Validate auth token and apply per-user rate limits.

    Drop-in replacement for require_user on all data/search endpoints.
    Limits come from the user's subscription tier (free=1k/day, pro=10k/day).
    """
    token = (authorization or "").replace("Bearer ", "").strip()
    if token.startswith("demo-"):
        from market_core.demo_tokens import consume_demo_request

        sess = consume_demo_request(token)
        if not sess:
            raise HTTPException(
                status_code=401,
                detail="Demo token expired or quota exhausted. Run: market demo",
            )
        return f"demo:{sess['session_id']}"
    username = require_user(authorization)
    check_user_rate_limit(username)
    return username


def require_pro(authorization: str | None) -> str:
    """Require Pro (or higher) tier for premium data endpoints."""
    from market_billing import db_get_subscription, price_label_for_plan
    from market_core.platform_admin import is_platform_admin

    username = require_api_key(authorization)
    if is_platform_admin(username):
        return username
    sub = db_get_subscription(username)
    if sub.get("tier", "free") not in ("pro", "pro_founding", "pro_annual", "enterprise", "builder"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"This endpoint requires CLI Market Pro ({price_label_for_plan('pro')}). "
                "Run: market upgrade or visit /billing/pro-checkout"
            ),
        )
    return username
