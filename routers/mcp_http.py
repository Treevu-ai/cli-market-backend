"""HTTP MCP transport endpoint — enables CLI Market to be added as a remote
MCP server in claude.ai, Claude Desktop (HTTP mode), Cursor, VS Code, Kiro,
Codex, Gemini, and any other MCP-compatible client that supports the
Streamable HTTP transport (MCP 2025-03-26).

Endpoint:
  POST /mcp   JSON-RPC 2.0 — handles initialize, tools/list, tools/call

Usage in claude.ai (Add MCP server):
  URL: https://cli-market-production.up.railway.app/mcp?token=<your-market-api-token>
  (claude.ai connectors don't support Bearer auth — use the token query param instead)

Supported tools (maps to existing REST endpoints):
  market_search        → POST /products/search
  market_compare       → POST /products/compare
  market_inflation     → GET  /v1/intel/inflation?country=XX
  market_scores        → GET  /v1/intel/scores?country=XX
  market_intel_brief   → GET  /v1/intel/brief?country=XX&days=N
  market_trending      → GET  /analytics/trending?country=XX&limit=N
  market_stores        → GET  /stores?country=XX
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from market_funnel import record_funnel_event
from market_stats import MCP_TOOLS, PACKAGE_VERSION, RETAILERS_VERIFIED
from server_deps import require_api_key

router = APIRouter(tags=["mcp-http"])

_API_BASE = "https://cli-market-production.up.railway.app"
# 2025-03-26 = Streamable HTTP (POST-only).  The older 2024-11-05 protocol
# uses HTTP+SSE transport, which would require a GET /mcp SSE endpoint.
_MCP_VERSION = "2025-03-26"

# Canonical client slugs — order matters (first match wins).
# Each entry: (canonical_slug, [substrings to match in lowercased name/UA])
_CLIENT_MAP: list[tuple[str, list[str]]] = [
    ("claude",    ["claude", "anthropic"]),
    ("cursor",    ["cursor"]),
    ("kiro",      ["kiro", "amazon kiro"]),
    ("codex",     ["codex", "openai-codex", "openai codex"]),
    ("gemini",    ["gemini", "google gemini"]),
    ("windsurf",  ["windsurf"]),
    ("zed",       ["zed"]),
    # VS Code last — "code" is a very common substring
    ("vscode",    ["vscode", "visual studio code", "vs code", "github.copilot"]),
]


def _detect_client(
    client_info: dict | None,
    user_agent: str | None,
) -> tuple[str, str, str]:
    """Return (canonical_slug, raw_name, raw_version)."""
    info = client_info or {}
    raw_name = str(info.get("name") or "").strip()
    raw_version = str(info.get("version") or "").strip()

    # Prefer clientInfo.name; fall back to User-Agent
    candidates = [raw_name.lower(), (user_agent or "").lower()]
    for text in candidates:
        if not text:
            continue
        for slug, patterns in _CLIENT_MAP:
            if any(p in text for p in patterns):
                return slug, raw_name or text, raw_version

    return "unknown", raw_name or (user_agent or "")[:80], raw_version


def _log_mcp_event(
    event: str,
    token: str | None,
    meta: dict,
) -> None:
    """Fire-and-forget funnel event. Never raises."""
    try:
        record_funnel_event(
            event,
            username=token or None,
            meta=meta,
        )
    except Exception:
        pass


# ── Tool definitions (MCP schema format) ─────────────────────────────────────

_TOOLS = [
    {
        "name": "market_search",
        "description": (
            f"Search for products across {RETAILERS_VERIFIED} LATAM retailers. "
            "Returns prices, brands, stores, and normalized unit prices (price_per_kg/L). "
            "Countries: PE=Peru, AR=Argentina, BR=Brazil, MX=Mexico, CO=Colombia, CL=Chile."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Product name, e.g. 'arroz', 'leche entera', 'aceite vegetal'"},
                "country": {"type": "string", "description": "ISO country code: PE, AR, BR, MX, CO, CL, IT, FR"},
                "store": {"type": "string", "description": "Specific store key, e.g. 'wong_pe', 'carrefour_ar'"},
                "limit": {"type": "integer", "default": 20, "description": "Max results (1-50)"},
            },
        },
    },
    {
        "name": "market_compare",
        "description": (
            "Compare prices for the same product across all retailers in a country. "
            "Returns price spread %, cheapest store, most expensive store, and per-unit price."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "country": {"type": "string", "description": "ISO country code"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "market_inflation",
        "description": (
            "Get real-time inflation and basket stress data for a LATAM country. "
            "Returns basket stress index (ratio vs baseline), inflation signals, "
            "and macroeconomic alignment score."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["country"],
            "properties": {
                "country": {"type": "string", "description": "ISO country code: PE, AR, BR, MX, CO, CL"},
            },
        },
    },
    {
        "name": "market_scores",
        "description": (
            "Get market intelligence scores for a LATAM country (0-100). "
            "Includes retail aggression, labor stress, logistics risk, and macro alignment."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["country"],
            "properties": {
                "country": {"type": "string", "description": "ISO country code"},
            },
        },
    },
    {
        "name": "market_intel_brief",
        "description": (
            "Get an aggregated market intelligence brief for a LATAM country. "
            "Returns composite scores, basket stress index, enrichment indicators "
            "(Open Food Facts, Wikimedia, weather, World Bank), and per-subcategory "
            "price/demand signals — all in a single call. "
            "Set include_catalog=true to also receive the full indicator catalog."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "ISO country code: PE, AR, BR, MX, CO, CL"},
                "line": {"type": "string", "description": "Product line filter (optional)"},
                "days": {"type": "integer", "default": 7, "description": "Lookback window in days"},
                "include_catalog": {"type": "boolean", "default": False, "description": "Include full indicator catalog"},
            },
        },
    },
    {
        "name": "market_trending",
        "description": "Get the most searched and purchased products in the last 7 days for a country.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "ISO country code"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "market_stores",
        "description": f"List all {RETAILERS_VERIFIED} indexed retailers. Filter by country to see which stores are available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "ISO country code (optional)"},
            },
        },
    },
]

# ── Tool execution — proxies to existing REST endpoints ───────────────────────

async def _call_tool(name: str, args: dict, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        if name == "market_search":
            r = await client.post(f"{_API_BASE}/products/search", json=args, headers=headers)
        elif name == "market_compare":
            r = await client.post(f"{_API_BASE}/products/compare", json=args, headers=headers)
        elif name == "market_inflation":
            r = await client.get(f"{_API_BASE}/v1/intel/inflation", params={"country": args.get("country")}, headers=headers)
        elif name == "market_scores":
            r = await client.get(f"{_API_BASE}/v1/intel/scores", params={"country": args.get("country")}, headers=headers)
        elif name == "market_intel_brief":
            params = {k: v for k, v in args.items() if v is not None}
            r = await client.get(f"{_API_BASE}/v1/intel/brief", params=params, headers=headers)
        elif name == "market_trending":
            params = {k: v for k, v in args.items() if v is not None}
            r = await client.get(f"{_API_BASE}/analytics/trending", params=params, headers=headers)
        elif name == "market_stores":
            params = {k: v for k, v in args.items() if v is not None}
            r = await client.get(f"{_API_BASE}/stores", params=params, headers=headers)
        else:
            return {"error": f"Unknown tool: {name}"}

        if r.status_code >= 400:
            return {"error": f"HTTP {r.status_code}", "detail": r.text[:200]}
        return r.json()


# ── JSON-RPC dispatcher ───────────────────────────────────────────────────────

def _rpc_ok(result: dict, req_id) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def _rpc_err(code: int, message: str, req_id) -> dict:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}


@router.get("/.well-known/mcp/server-card.json")
async def mcp_server_card():
    """Static server card for Smithery and MCP directory scanners.

    Bypasses the need for SmitheryBot to do a full MCP scan — per
    https://smithery.ai/docs/build/publish#server-scanning
    """
    return JSONResponse({
        "name": "CLI Market",
        "version": PACKAGE_VERSION,
        "description": (
            f"Commerce infrastructure for AI agents — {RETAILERS_VERIFIED} verified LATAM retailers, "
            f"{MCP_TOOLS} MCP tools, 8 countries (PE, AR, BR, MX, CO, CL, IT, FR). "
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
                    "description": "CLI Market API key (sk-...). Get one free at https://cli-market.dev or via POST /auth/register.",
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

    Add to claude.ai / Cursor / VS Code / Kiro / Codex / Gemini as:
      URL: https://cli-market-production.up.railway.app/mcp?token=<your-market-api-token>
      (if the client supports Bearer auth, use Authorization: Bearer <token> instead)
    """
    # Accept token from Authorization header OR ?token= query param (for claude.ai connectors)
    effective_auth = authorization or (f"Bearer {token}" if token else None)
    raw_token = effective_auth.replace("Bearer ", "").strip() if effective_auth else None

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_err(-32700, "Parse error", None), status_code=400)

    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params", {})

    # ── initialize ────────────────────────────────────────────────────────────
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
                    f"Commerce infrastructure for AI agents — {RETAILERS_VERIFIED} retailers, "
                    f"{MCP_TOOLS} tools, 8 LATAM countries."
                ),
            },
        }, req_id))

    # ── notifications/initialized (no response required) ─────────────────────
    if method == "notifications/initialized":
        return JSONResponse({})

    # ── tools/list ────────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse(_rpc_ok({"tools": _TOOLS}, req_id))

    # ── tools/call ────────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        # Validate auth (Bearer header or ?token= query param)
        if not effective_auth:
            return JSONResponse(_rpc_err(-32001, "Auth required: Authorization header or ?token= query param", req_id), status_code=401)
        try:
            require_api_key(effective_auth)  # validate only — raises on invalid/expired
        except Exception:
            return JSONResponse(_rpc_err(-32001, "Invalid or expired API token", req_id), status_code=401)

        # Use clientInfo if the client includes it in this request; fall back to User-Agent
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
                "content": [{"type": "text", "text": f"Error: {result['error']}"}],
                "isError": True,
            }, req_id))

        import json
        return JSONResponse(_rpc_ok({
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
        }, req_id))

    # ── unknown method ────────────────────────────────────────────────────────
    return JSONResponse(_rpc_err(-32601, f"Method not found: {method}", req_id), status_code=404)
