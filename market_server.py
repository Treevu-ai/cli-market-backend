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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize DB schema and migrate legacy JSON data on startup.

    Replaces the previous side-effect-at-import pattern. Idempotent.
    """
    ensure_db_initialized()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ORIGINS", "https://cli-market.dev,http://localhost:3000"
    ).split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── Routers ──────────────────────────────────────────────────────────────────

from routers.admin import router as admin_router
from routers.agent import router as agent_router
from routers.analytics import router as analytics_router
from routers.auth import router as auth_router
from routers.cart import router as cart_router
from routers.data_v1 import router as data_v1_router
from routers.dashboard import router as dashboard_router
from routers.data_export import router as data_export_router
from routers.health import router as health_router
from routers.intel import router as intel_router
from routers.media import router as media_router
from routers.misc import router as misc_router
from routers.orders import router as orders_router
from routers.payments import router as payments_router
from routers.retailers import router as retailers_router
from routers.retailer_admin import router as retailer_admin_router
from routers.search import router as search_router

# Order doesn't matter functionally — each router declares its own paths.
# Listed alphabetically by router file for easy navigation.
for r in (
    admin_router,
    agent_router,
    analytics_router,
    auth_router,
    cart_router,
    dashboard_router,
    data_v1_router,
    data_export_router,
    health_router,
    intel_router,
    media_router,
    misc_router,
    orders_router,
    payments_router,
    retailers_router,
    retailer_admin_router,
    search_router,
):
    app.include_router(r)


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
