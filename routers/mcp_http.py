"""HTTP MCP transport endpoint — enables CLI Market to be added as a remote
MCP server in claude.ai, Claude Desktop (HTTP mode), Cursor, and any other
MCP-compatible client that supports the streamable HTTP transport.

Endpoint:
  POST /mcp   JSON-RPC 2.0 — handles initialize, tools/list, tools/call

Usage in claude.ai (Add MCP server):
  URL: https://cli-market-production.up.railway.app/mcp
  Auth: Bearer <your-market-api-token>

Supported tools (maps to existing REST endpoints):
  market_search      → POST /products/search
  market_compare     → POST /products/compare
  market_inflation   → GET  /intel/inflation?country=XX
  market_scores      → GET  /intel/scores?country=XX
  market_trending    → GET  /products/trending?country=XX&limit=N
  market_stores      → GET  /stores?country=XX
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from market_stats import MCP_TOOLS, PACKAGE_VERSION, RETAILERS_VERIFIED
from server_deps import require_api_key

router = APIRouter(tags=["mcp-http"])

_API_BASE = "https://cli-market-production.up.railway.app"
_MCP_VERSION = "2024-11-05"

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


@router.post("/mcp")
async def mcp_http(request: Request, authorization: str | None = Header(None)):
    """HTTP MCP endpoint — JSON-RPC 2.0 over POST.

    Add to claude.ai as:
      URL: https://cli-market-production.up.railway.app/mcp
      Auth: Bearer <your-market-api-token>
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_err(-32700, "Parse error", None), status_code=400)

    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params", {})

    # ── initialize ────────────────────────────────────────────────────────────
    if method == "initialize":
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

        # Validate auth
        if not authorization:
            return JSONResponse(_rpc_err(-32001, "Authorization header required (Bearer token)", req_id), status_code=401)
        try:
            token = require_api_key(authorization)
        except Exception:
            return JSONResponse(_rpc_err(-32001, "Invalid or expired API token", req_id), status_code=401)

        result = await _call_tool(tool_name, tool_args, token)

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
