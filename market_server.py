#!/usr/bin/env python3
"""
market-server — Agentic Market backend.

Thin FastAPI app factory. ALL endpoint code lives in routers/*.py and
business logic in market_core.py / server_deps.py.

To run:
    python market_server.py
    → http://localhost:8765
    → http://localhost:8765/docs

Adding a new endpoint:
    1. Pick the router that fits the domain (or create routers/<domain>.py).
    2. Define the endpoint there with `@router.<method>(path)`.
    3. If you're creating a new router, register it below with
       `app.include_router(<new>_router)`.

There are NO inline @app.<method> endpoints here. If you find yourself
about to add one — instead, find or create the right router.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from market_core import (
    COUNTRIES,
    LINES,
    STORES,
    db_migrate_from_json,
    ensure_db_initialized,
    logger as log,
)
from market_security import production_payment_config_warnings

# Server-only helpers (auth, rate limit, hashing) live in server_deps.py.
# Re-exported below — tests and external code import these from market_server.
from server_deps import (  # noqa: F401
    auth_user,
    hash_password,
    verify_password,
    check_auth_brute_force,
    record_auth_failure,
    require_user,
    require_api_key,
    check_rate_limit,
    DEFAULT_TOKEN,
    RATE_LIMIT_MIN,
    RATE_LIMIT_DAY,
    RATE_LIMIT_WINDOW,
    AUTH_MAX_ATTEMPTS,
    AUTH_WINDOW,
    _auth_attempts,
)

logger = log.getChild("server")
_access_log = log.getChild("access")

_SKIP_ACCESS_LOG = {"/health", "/"}


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request: method, path, status, duration."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        if request.url.path not in _SKIP_ACCESS_LOG:
            _access_log.info(
                "%s %s → %d  %.0fms  %s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                request.client.host if request.client else "-",
            )
        return response


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize DB schema and migrate legacy JSON data on startup.

    Replaces the previous side-effect-at-import pattern. Idempotent.
    """
    ensure_db_initialized()
    try:
        from market_funnel import ensure_funnel_schema
        ensure_funnel_schema()
    except Exception as e:
        logger.warning("Funnel schema skipped: %s", e)
    try:
        from market_observatory import ensure_observatory_schema
        ensure_observatory_schema()
    except Exception as e:
        logger.warning("Observatory schema skipped: %s", e)
    try:
        # P3-9: add session_id column for funnel tracking (cli-market-core PR #35)
        from market_core import get_db, USE_PG
        db = get_db()
        if USE_PG:
            db.execute("ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS session_id TEXT")
        else:
            db.execute("ALTER TABLE agent_events ADD COLUMN session_id TEXT")
        db.commit()
        db.close()
    except Exception:
        pass  # column already exists or table not yet created
    try:
        from price_snapshots_schema import ensure_canonical_product_id_column

        ensure_canonical_product_id_column()
    except Exception as e:
        logger.warning("canonical_product_id migration skipped: %s", e)
    try:
        from collector_schema import ensure_collector_runs_columns

        ensure_collector_runs_columns()
    except Exception as e:
        logger.warning("collector_runs migration skipped: %s", e)
    try:
        db_migrate_from_json()
    except Exception as e:
        logger.warning("JSON migration skipped: %s", e)
    for warning in production_payment_config_warnings():
        logger.warning("Payment security: %s", warning)
    yield


# ── App ──────────────────────────────────────────────────────────────────────

from market_stats import MCP_TOOLS, PACKAGE_VERSION, RETAILERS_VERIFIED, COUNTRIES as MS_COUNTRIES

app = FastAPI(
    title="CLI Market API",
    description=(
        f"Commerce infrastructure for AI agents — {RETAILERS_VERIFIED} verified retailers, "
        f"{MCP_TOOLS} MCP tools, {MS_COUNTRIES} countries. Agent-ready."
    ),
    version=PACKAGE_VERSION,
    lifespan=lifespan,
)

app.add_middleware(AccessLogMiddleware)
from market_core import db_validate_api_key
from market_observatory import ObservatoryMiddleware

app.add_middleware(
    ObservatoryMiddleware,
    auth_user_fn=auth_user,
    api_key_fn=db_validate_api_key,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ORIGINS",
        "https://cli-market.dev,http://localhost:3000,https://claude.ai,https://api.anthropic.com,https://smithery.ai,https://mcp.smithery.run,https://chat.smithery.ai",
    ).split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Agent-ID", "X-Session-ID", "X-Country"],
)


# ── Routers ──────────────────────────────────────────────────────────────────

from routers.admin import router as admin_router
from routers.agent import router as agent_router
from routers.alerts import router as alerts_router
from routers.analytics import router as analytics_router
from routers.auth import router as auth_router
from routers.cart import router as cart_router
from market_core.api_routes import router as v1_router
from routers.dashboard import router as dashboard_router
from routers.data_export import router as data_export_router
from routers.funnel import router as funnel_router
from routers.observatory import router as observatory_router
from routers.health import router as health_router
from routers.index_api import router as index_router
from routers.intelligence_web import router as intelligence_web_router
from routers.intel import router as intel_router
from routers.media import router as media_router
from routers.misc import router as misc_router
from routers.orders import router as orders_router
from routers.mercadopago import router as mercadopago_router
from routers.payments import router as payments_router
from routers.public_demo import router as public_demo_router
from routers.retailers import router as retailers_router
from routers.retailer_admin import router as retailer_admin_router
from routers.discovery import router as discovery_router
from routers.mcp_http import router as mcp_http_router
from routers.search import router as search_router

# Order doesn't matter functionally — each router declares its own paths.
# Listed alphabetically by router file for easy navigation.
for r in (
    admin_router,
    agent_router,
    alerts_router,
    analytics_router,
    auth_router,
    cart_router,
    dashboard_router,

    data_export_router,
    discovery_router,
    mcp_http_router,
    funnel_router,
    observatory_router,
    health_router,
    index_router,
    intel_router,
    intelligence_web_router,
    media_router,
    misc_router,
    orders_router,
    payments_router,
    public_demo_router,
    mercadopago_router,
    retailers_router,
    retailer_admin_router,
    search_router,
):
    app.include_router(r)


# ── v1 routes from shared core (auth wired here) ──────────────────────────
import market_core.api_routes as _v1_mod

_v1_mod._auth_fn = require_api_key
app.include_router(v1_router, prefix="/v1")


# ── Entrypoint ───────────────────────────────────────────────────────────────

def main():
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))

    logger.info(f"CLI Market API starting on http://{host}:{port}")
    logger.info(f"  {len(STORES)} stores, {len(LINES)} lines, {len(COUNTRIES)} countries")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
