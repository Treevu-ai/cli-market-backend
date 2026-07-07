"""HTTP MCP transport endpoint — enables CLI Market to be added as a remote
MCP server in claude.ai, Claude Desktop (HTTP mode), Cursor, VS Code, Kiro,
Codex, Gemini, and any other MCP-compatible client that supports the
Streamable HTTP transport (MCP 2025-03-26).

Endpoint:
  POST /mcp   JSON-RPC 2.0 — handles initialize, tools/list, tools/call

Usage in claude.ai (Add MCP server):
  URL: https://cli-market-api.fly.dev/mcp?token=<your-market-api-token>
  (claude.ai connectors don't support Bearer auth — use the token query param instead)

Tool tiers (default profile, 32 tools):
  Free  — search, compare, trending, discover, barcode, inflation, inflation_report,
           scores, intel_brief, stats, whoami, subscription, preferences,
           household_get, affordability, substitutes, login
  Pro   — basket, optimize_purchase, price_risk, procurement_signal, favorites,
           price_alerts, export, ask, add, cart, cart_update, checkout, orders,
           household_update, ticket
           (returns upgrade prompt if tier is free)
"""

from __future__ import annotations

import os
import time as _time

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from market_core import get_db
from market_core.market_mcp_registry import list_tools as _registry_list_tools
from market_funnel import record_funnel_event
from market_stats import COUNTRIES, MCP_TOOLS, PACKAGE_VERSION, RETAILERS_VERIFIED
from server_deps import require_api_key


def _live_store_count() -> int:
    """Return the actual number of stores with price snapshots (live from DB)."""
    try:
        db = get_db()
        row = db.execute("SELECT COUNT(DISTINCT store) FROM price_snapshots").fetchone()
        db.close()
        return int(row[0]) if row and row[0] else RETAILERS_VERIFIED
    except Exception:
        return RETAILERS_VERIFIED

router = APIRouter(tags=["mcp-http"])

_API_BASE = os.getenv("MARKET_API_URL", "https://cli-market-api.fly.dev").rstrip("/")
_MCP_VERSION = "2025-03-26"

_PRO_TOOLS = frozenset({
    "market_basket",
    "market_optimize_purchase",
    "market_procurement_signal",
    "market_price_risk",
    "market_favorites",
    "market_price_alerts",
    "market_export",
    "market_ask",
    "market_add",
    "market_cart",
    "market_cart_update",
    "market_checkout",
    "market_orders",
    "market_household_update",
    "market_ticket",
})

_UPGRADE_MSG = (
    "This tool requires CLI Market Pro ($49/mo). "
    "Start with Starter ($9/mo) for search and compare, or upgrade to Pro "
    "to unlock basket, cart, checkout, orders, alerts, export, and AI ask. "
    "Plans at https://cli-market.dev."
)

# Canonical client slugs — order matters (first match wins).
_CLIENT_MAP: list[tuple[str, list[str]]] = [
    ("claude",    ["claude", "anthropic"]),
    ("cursor",    ["cursor"]),
    ("kiro",      ["kiro", "amazon kiro"]),
    ("codex",     ["codex", "openai-codex", "openai codex"]),
    ("gemini",    ["gemini", "google gemini"]),
    ("windsurf",  ["windsurf"]),
    ("zed",       ["zed"]),
    ("vscode",    ["vscode", "visual studio code", "vs code", "github.copilot"]),
]


def _detect_client(
    client_info: dict | None,
    user_agent: str | None,
) -> tuple[str, str, str]:
    info = client_info or {}
    raw_name = str(info.get("name") or "").strip()
    raw_version = str(info.get("version") or "").strip()
    candidates = [raw_name.lower(), (user_agent or "").lower()]
    for text in candidates:
        if not text:
            continue
        for slug, patterns in _CLIENT_MAP:
            if any(p in text for p in patterns):
                return slug, raw_name or text, raw_version
    return "unknown", raw_name or (user_agent or "")[:80], raw_version


def _log_mcp_event(event: str, token: str | None, meta: dict) -> None:
    try:
        record_funnel_event(event, username=token or None, meta=meta)
    except Exception:
        pass


# ── Tool definitions (sourced from market_core registry, default profile) ─────

# list_tools() returns [{name, description, inputSchema}, ...] for the profile.
# Using the registry as single source of truth avoids drift when new tools are
# published to cli-market-core without a matching mcp_http update.
_TOOLS: list[dict] = _registry_list_tools("default")

# ── Moat freshness cache — injected into search/basket responses ──────────────

_MOAT_CACHE: dict = {"ts": 0.0, "age_hours": None, "status": "unknown"}
_MOAT_TTL = 300.0  # seconds; one probe per 5 min per process

_FRESHNESS_TOOLS = frozenset({"market_search", "market_basket", "market_compare"})


async def _fetch_moat_cached(client: httpx.AsyncClient, headers: dict) -> dict:
    """Return cached moat freshness; refresh if older than _MOAT_TTL."""
    global _MOAT_CACHE
    now = _time.monotonic()
    if now - _MOAT_CACHE["ts"] < _MOAT_TTL:
        return _MOAT_CACHE
    try:
        r = await client.get(f"{_API_BASE}/health/collector", headers=headers, timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            _MOAT_CACHE = {
                "ts": now,
                "age_hours": data.get("age_hours"),
                "status": data.get("status", "unknown"),
            }
        else:
            _MOAT_CACHE["ts"] = now
    except Exception:
        _MOAT_CACHE["ts"] = now
    return _MOAT_CACHE




# ── Tool execution ────────────────────────────────────────────────────────────

async def _call_tool(name: str, args: dict, token: str) -> dict:
    import asyncio

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        # ── Free tools ────────────────────────────────────────────────────────
        if name == "market_search":
            r = await client.post(f"{_API_BASE}/products/search", json=args, headers=headers)
        elif name == "market_compare":
            r = await client.post(f"{_API_BASE}/products/compare", json=args, headers=headers)
        elif name == "market_stores":
            r = await client.get(f"{_API_BASE}/stores", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_trending":
            r = await client.get(f"{_API_BASE}/analytics/trending", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_discover":
            r = await client.get(f"{_API_BASE}/analytics/trending", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_barcode":
            code = args.get("code", "")
            r = await client.get(f"{_API_BASE}/products/barcode/{code}", headers=headers)
        elif name == "market_inflation":
            r = await client.get(f"{_API_BASE}/v1/intel/inflation", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_scores":
            r = await client.get(f"{_API_BASE}/v1/intel/scores", params={"country": args.get("country")}, headers=headers)
        elif name == "market_intel_brief":
            r = await client.get(f"{_API_BASE}/v1/intel/brief", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_intel_pulse":
            r = await client.get(f"{_API_BASE}/v1/intel/pulse", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_forecast":
            params = {k: v for k, v in args.items() if v is not None}
            r = await client.get(f"{_API_BASE}/v1/intel/forecast", params=params, headers=headers)
        elif name == "market_arbitrage":
            params = {}
            if args.get("canonical_id"):
                params["canonical_id"] = args["canonical_id"]
            if args.get("product"):
                params["product"] = args["product"]
            if args.get("countries"):
                params["countries"] = args["countries"]
            if args.get("min_spread_pct") is not None:
                params["min_spread_pct"] = args["min_spread_pct"]
            r = await client.get(f"{_API_BASE}/v1/intel/arbitrage", params=params, headers=headers)
        elif name == "market_stats":
            r = await client.get(f"{_API_BASE}/analytics/stats", headers=headers)
        elif name == "market_whoami":
            r = await client.get(f"{_API_BASE}/auth/whoami", headers=headers)
        # ── Pro tools ─────────────────────────────────────────────────────────
        elif name == "market_basket":
            r = await client.post(f"{_API_BASE}/v1/basket/compare", json=args, headers=headers)
        elif name == "market_procurement_signal":
            r = await client.get(f"{_API_BASE}/v1/intel/basket-stress", params={"country": args.get("country")}, headers=headers)
        elif name == "market_price_risk":
            r = await client.get(f"{_API_BASE}/v1/intel/alerts", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_favorites":
            r = await client.post(f"{_API_BASE}/favorites", json=args, headers=headers)
        elif name == "market_price_alerts":
            r = await client.get(f"{_API_BASE}/v1/alerts", headers=headers)
        elif name == "market_export":
            r = await client.post(f"{_API_BASE}/v1/data/export", json=args, headers=headers)
        elif name == "market_ask":
            r = await client.post(f"{_API_BASE}/agent/ask", json=args, headers=headers)
        elif name == "market_add":
            r = await client.post(f"{_API_BASE}/cart/add", json=args, headers=headers)
        elif name == "market_cart":
            r = await client.get(f"{_API_BASE}/cart", headers=headers)
        elif name == "market_cart_update":
            r = await client.put(f"{_API_BASE}/cart/update", json=args, headers=headers)
        elif name == "market_checkout":
            r = await client.post(f"{_API_BASE}/checkout", json=args, headers=headers)
        elif name == "market_orders":
            r = await client.get(f"{_API_BASE}/orders", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        # ── New tools (registry default profile, added 2026-06-29) ───────────
        elif name == "market_subscription":
            r = await client.get(f"{_API_BASE}/auth/subscription", headers=headers)
        elif name == "market_preferences":
            r = await client.get(f"{_API_BASE}/agent/preferences", headers=headers)
        elif name == "market_household_get":
            r = await client.get(f"{_API_BASE}/v1/household", headers=headers)
        elif name == "market_household_update":
            method = "patch" if args.get("patch") else "put"
            r = await getattr(client, method)(f"{_API_BASE}/v1/household", json=args.get("payload", args), headers=headers)
        elif name == "market_ticket":
            r = await client.post(f"{_API_BASE}/v1/receipts/submit", json=args, headers=headers)
        elif name == "market_affordability":
            r = await client.get(f"{_API_BASE}/v1/intel/affordability", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_inflation_report":
            r = await client.get(f"{_API_BASE}/v1/intel/inflation-report", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_login":
            r = await client.post(f"{_API_BASE}/auth/login", json=args, headers=headers)
        elif name == "market_substitutes":
            r = await client.get(f"{_API_BASE}/v1/products/substitutes", params={k: v for k, v in args.items() if v is not None}, headers=headers)
        elif name == "market_optimize_purchase":
            r = await client.post(f"{_API_BASE}/v1/missions/optimize-purchase", json=args, headers=headers)
        else:
            return {"error": f"Unknown tool: {name}"}

        if r.status_code in (402, 403) and name in _PRO_TOOLS:
            return {"error": "pro_required", "message": _UPGRADE_MSG}
        if r.status_code == 429:
            retry_after = r.headers.get("retry-after", "60")
            try:
                detail = r.json().get("detail", r.text[:300])
            except Exception:
                detail = r.text[:300]
            return {
                "error": "rate_limited",
                "message": detail,
                "retry_after_seconds": int(retry_after),
            }
        if r.status_code >= 400:
            return {"error": f"HTTP {r.status_code}", "detail": r.text[:200]}

        result = r.json()

        # Inject moat freshness into search/basket/compare concurrently (cached, ~0 latency after warmup).
        if name in _FRESHNESS_TOOLS:
            moat = await _fetch_moat_cached(client, headers)
            age_h = moat.get("age_hours")
            status = moat.get("status", "unknown")
            if age_h is not None:
                result["_moat_age_hours"] = age_h
            if status in ("stale", "dead", "empty"):
                result["_data_warning"] = (
                    f"Price data may be outdated — last collector run {age_h:.1f}h ago (status: {status}). "
                    "Verify critical prices before recommending a purchase."
                )

        return result


# ── JSON-RPC helpers ──────────────────────────────────────────────────────────

def _rpc_ok(result: dict, req_id) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def _rpc_err(code: int, message: str, req_id) -> dict:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/.well-known/mcp/server-card.json")
async def mcp_server_card():
    """Static server card for Smithery and MCP directory scanners."""
    live_stores = _live_store_count()
    return JSONResponse({
        "name": "CLI Market",
        "version": PACKAGE_VERSION,
        "description": (
            f"Commerce infrastructure for AI agents — {live_stores} verified LATAM retailers, "
            f"{len(_TOOLS)} MCP tools, 8 countries (PE, AR, BR, MX, CO, CL, IT, FR). "
            "61,000+ real prices refreshed every 4h."
        ),
        "homepage": "https://cli-market.dev",
        "repository": "https://pypi.org/project/cli-market-world/",
        "license": "MIT",
        "categories": ["commerce", "data", "retail"],
        "keywords": ["latam", "retail", "prices", "ecommerce", "vtex", "agents", "mcp", "procurement"],
        "capabilities": {"tools": {}},
        "authentication": {
            "type": "bearer",
            "required": True,
            "description": "Free API key via POST /auth/register or https://cli-market.dev",
        },
        "tools": [t["name"] for t in _TOOLS],
        "configSchema": {
            "type": "object",
            "required": ["apiKey"],
            "properties": {
                "apiKey": {
                    "type": "string",
                    "title": "API Key",
                    "description": "CLI Market API key (sk-...). Get one free at https://cli-market.dev",
                    "format": "password",
                },
            },
        },
    })


@router.get("/mcp")
async def mcp_http_get():
    """Inform SSE-transport clients that this server uses Streamable HTTP (POST only)."""
    return JSONResponse(
        {"error": "This MCP server uses Streamable HTTP transport (MCP 2025-03-26). Send POST requests to this endpoint."},
        status_code=405,
        headers={"Allow": "POST"},
    )


@router.post("/mcp")
async def mcp_http(
    request: Request,
    authorization: str | None = Header(None),
    token: str | None = None,
    user_agent: str | None = Header(None, alias="user-agent"),
):
    """HTTP MCP endpoint — JSON-RPC 2.0 over POST (Streamable HTTP, MCP 2025-03-26).

    Add to Claude / Cursor / VS Code / Kiro / Codex / Gemini:
      URL: https://cli-market-api.fly.dev/mcp?token=<your-api-token>
    """
    effective_auth = authorization or (f"Bearer {token}" if token else None)
    raw_token = effective_auth.replace("Bearer ", "").strip() if effective_auth else None

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_err(-32700, "Parse error", None), status_code=400)

    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        client_info = params.get("clientInfo") or {}
        client_slug, client_raw, client_version = _detect_client(client_info, user_agent)
        _log_mcp_event("mcp_connect", raw_token, {
            "client": client_slug,
            "client_raw": client_raw,
            "client_version": client_version,
            "protocol_version": params.get("protocolVersion", ""),
        })
        return JSONResponse(_rpc_ok({
            "protocolVersion": _MCP_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "cli-market",
                "version": PACKAGE_VERSION,
                "description": (
                    f"Commerce infrastructure for AI agents — {_live_store_count()} retailers, "
                    f"{len(_TOOLS)} tools, {COUNTRIES} LATAM countries."
                ),
            },
        }, req_id))

    if method == "notifications/initialized":
        return JSONResponse({})

    if method == "tools/list":
        return JSONResponse(_rpc_ok({"tools": _TOOLS}, req_id))

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if not effective_auth:
            return JSONResponse(_rpc_err(-32001, "Auth required: Authorization header or ?token= query param", req_id), status_code=401)
        try:
            require_api_key(effective_auth)
        except Exception:
            return JSONResponse(_rpc_err(-32001, "Invalid or expired API token", req_id), status_code=401)

        client_info = params.get("clientInfo") or {}
        client_slug, client_raw, _ = _detect_client(client_info, user_agent)
        _log_mcp_event("mcp_tool_call", raw_token, {
            "client": client_slug,
            "client_raw": client_raw,
            "tool": tool_name,
            "country": tool_args.get("country") or None,
        })

        result = await _call_tool(tool_name, tool_args, raw_token)

        if "error" in result:
            return JSONResponse(_rpc_ok({
                "content": [{"type": "text", "text": result.get("message") or f"Error: {result['error']}"}],
                "isError": True,
            }, req_id))

        import json
        return JSONResponse(_rpc_ok({
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
        }, req_id))

    return JSONResponse(_rpc_err(-32601, f"Method not found: {method}", req_id), status_code=404)
