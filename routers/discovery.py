"""Agent discovery endpoints — makes CLI Market findable by ChatGPT, Perplexity, Copilot, and MCP-compatible agents.

Endpoints:
  GET /.well-known/ai-plugin.json   ChatGPT / OpenAI Actions plugin manifest
  GET /.well-known/mcp.json         MCP registry discovery (stdio + HTTP)
  GET /tools/openapi.json           Curated OpenAPI spec for the 6 core agent tools

How discoverability works:
  - ChatGPT Actions: reads /.well-known/ai-plugin.json, then fetches the OpenAPI spec
    at the url listed in the "api" field. Use this URL when creating a Custom GPT.
  - MCP-compatible agents (Claude Desktop, Cursor, etc.): read /.well-known/mcp.json
    to discover the stdio transport (pip install cli-market-world).
  - Perplexity / Copilot: parse /.well-known/ai-plugin.json for compatible agents.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from market_stats import MCP_TOOLS, PACKAGE_VERSION, RETAILERS_VERIFIED

router = APIRouter(tags=["discovery"])

_API_BASE = "https://cli-market-production.up.railway.app"
_WEBSITE = "https://cli-market.dev"

# ── OpenAI / ChatGPT Plugin manifest ─────────────────────────────────────────

_AI_PLUGIN = {
    "schema_version": "v1",
    "name_for_human": "CLI Market",
    "name_for_model": "cli_market",
    "description_for_human": (
        f"Search and compare prices across {RETAILERS_VERIFIED} supermarkets and retailers "
        "in Latin America (Peru, Argentina, Brazil, Mexico, Colombia, Chile). "
        "Real-time prices updated every 4 hours. Free tier available."
    ),
    "description_for_model": (
        "CLI Market gives you real-time retail price data across Latin America. "
        f"Use it to: search products across {RETAILERS_VERIFIED} retailers in 8 countries "
        "(market_search), compare prices for the same product across stores (market_compare), "
        "discover trending products (market_trending), get inflation and basket stress data "
        "(market_inflation), or get a full market intelligence brief (market_intel_brief). "
        "Always pass country codes in ISO 3166-1 alpha-2: PE=Peru, AR=Argentina, BR=Brazil, "
        "MX=Mexico, CO=Colombia, CL=Chile, IT=Italy, FR=France. "
        "Prices are in local currency: PEN (Peru), ARS (Argentina), BRL (Brazil), MXN (Mexico), COP (Colombia), CLP (Chile). "
        "Requires Bearer token from cli-market.dev/login (free tier: 1000 req/day)."
    ),
    "auth": {
        "type": "user_http",
        "authorization_type": "bearer",
    },
    "api": {
        "type": "openapi",
        "url": f"{_API_BASE}/tools/openapi.json",
    },
    "logo_url": f"{_WEBSITE}/logo.png",
    "contact_email": "acuba0103@gmail.com",
    "legal_info_url": f"{_WEBSITE}/legal",
}


@router.get("/.well-known/ai-plugin.json", include_in_schema=False)
def ai_plugin_manifest():
    """ChatGPT / OpenAI Actions plugin manifest."""
    return JSONResponse(content=_AI_PLUGIN)


# ── MCP discovery ─────────────────────────────────────────────────────────────

@router.get("/.well-known/mcp.json", include_in_schema=False)
def mcp_discovery():
    """MCP registry discovery file — served from the API for HTTP discoverability.
    Agents that support MCP can also install via: pip install cli-market-world && market-mcp
    """
    # Serve the canonical mcp.json from the backend root
    mcp_path = Path(__file__).resolve().parent.parent / "mcp.json"
    if mcp_path.exists():
        return JSONResponse(content=json.loads(mcp_path.read_text()))

    # Fallback inline if file not found
    return JSONResponse(content={
        "$schema": "https://registry.modelcontextprotocol.io/schema.json",
        "name": "CLI Market",
        "description": (
            f"Commerce infrastructure for AI agents — {MCP_TOOLS} MCP tools to search, compare, "
            f"and analyze prices across {RETAILERS_VERIFIED} retailers in 8 countries. "
            "51,000+ real prices, refreshed every 4 hours. MIT."
        ),
        "type": "stdio",
        "command": "market-mcp",
        "args": [],
        "env": {},
        "repository": "https://pypi.org/project/cli-market-world/",
        "website": _WEBSITE,
        "license": "MIT",
    })


# ── Curated OpenAPI spec for agent tools ─────────────────────────────────────

_TOOLS_OPENAPI = {
    "openapi": "3.1.0",
    "info": {
        "title": "CLI Market — Agent Tools",
        "description": (
            f"Curated API for AI agents. {RETAILERS_VERIFIED} retailers across 8 LATAM countries. "
            "51,000+ prices updated every 4 hours."
        ),
        "version": PACKAGE_VERSION,
        "contact": {"email": "acuba0103@gmail.com", "url": _WEBSITE},
        "license": {"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
    },
    "servers": [{"url": _API_BASE, "description": "Production"}],
    "security": [{"bearerAuth": []}],
    "components": {
        "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
        }
    },
    "paths": {
        "/products/search": {
            "post": {
                "operationId": "market_search",
                "summary": "Search products across LATAM retailers",
                "description": (
                    "Search for products by name across retailers. "
                    "Optionally filter by country or store. "
                    "Returns normalized prices (price_per_kg/L where applicable), brand, store, and availability."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["query"],
                                "properties": {
                                    "query": {"type": "string", "description": "Product name or description, e.g. 'arroz', 'leche', 'aceite vegetal'"},
                                    "country": {"type": "string", "description": "ISO country code: PE, AR, BR, MX, CO, CL, IT, FR"},
                                    "store": {"type": "string", "description": "Specific store key, e.g. 'wong_pe', 'carrefour_ar'"},
                                    "limit": {"type": "integer", "default": 20, "description": "Max results (1-50)"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "List of matching products with prices",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                        "total": {"type": "integer"},
                                        "results": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "name": {"type": "string"},
                                                    "price": {"type": "number"},
                                                    "currency": {"type": "string"},
                                                    "store": {"type": "string"},
                                                    "brand": {"type": "string"},
                                                    "url": {"type": "string"},
                                                },
                                            },
                                        },
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/products/compare": {
            "post": {
                "operationId": "market_compare",
                "summary": "Compare prices for the same product across stores",
                "description": (
                    "Find the best and worst price for a product across all retailers in a country. "
                    "Returns price spread, cheapest/most expensive store, and per-unit price."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["query"],
                                "properties": {
                                    "query": {"type": "string"},
                                    "country": {"type": "string", "description": "ISO country code"},
                                    "limit": {"type": "integer", "default": 10},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "Cross-store price comparison"}},
            }
        },
        "/v1/intel/inflation": {
            "get": {
                "operationId": "market_inflation",
                "summary": "Get real-time inflation and basket stress data",
                "description": "Returns basket stress index, inflation signals, and macroeconomic alignment for a country.",
                "parameters": [
                    {"name": "country", "in": "query", "required": True, "schema": {"type": "string"}, "description": "ISO country code"},
                ],
                "responses": {"200": {"description": "Inflation and basket stress data"}},
            }
        },
        "/v1/intel/scores": {
            "get": {
                "operationId": "market_scores",
                "summary": "Get market intelligence scores for a country",
                "description": "Returns retail aggression, labor stress, logistics risk, and other market health indicators.",
                "parameters": [
                    {"name": "country", "in": "query", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "Market intelligence scores (0-100)"}},
            }
        },
        "/analytics/trending": {
            "get": {
                "operationId": "market_trending",
                "summary": "Get trending products by country",
                "description": "Returns the most searched and purchased products in the last 7 days.",
                "parameters": [
                    {"name": "country", "in": "query", "schema": {"type": "string"}},
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10}},
                ],
                "responses": {"200": {"description": "Trending products list"}},
            }
        },
        "/stores": {
            "get": {
                "operationId": "market_stores",
                "summary": "List all available retailers",
                "description": "Returns all indexed retailers with country, platform, and status.",
                "parameters": [
                    {"name": "country", "in": "query", "schema": {"type": "string"}, "description": "Filter by country code"},
                ],
                "responses": {"200": {"description": "List of retailers"}},
            }
        },
    },
}


def _make_spec(title: str, description: str, paths: dict) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "description": description,
            "version": PACKAGE_VERSION,
            "contact": {"email": "acuba0103@gmail.com", "url": _WEBSITE},
            "license": {"name": "MIT"},
        },
        "servers": [{"url": _API_BASE}],
        "security": [{"bearerAuth": []}],
        "components": {
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        "schemas": {},
    },
        "paths": paths,
    }


@router.get("/tools/openapi.json", include_in_schema=False)
def tools_openapi():
    """Full curated OpenAPI spec — all 6 core agent tools."""
    return JSONResponse(content=_TOOLS_OPENAPI)


@router.get("/tools/openapi-shop.json", include_in_schema=False)
def tools_openapi_shop():
    """OpenAPI spec for the Shop bundle — search, compare, cart, checkout."""
    shop_paths = {k: v for k, v in _TOOLS_OPENAPI["paths"].items()
                  if k in ("/products/search", "/products/compare", "/analytics/trending")}
    return JSONResponse(content=_make_spec(
        "CLI Market — Shopper Agent",
        (f"Search and compare prices across {RETAILERS_VERIFIED} LATAM retailers. "
         "Find the best price for any product across Peru, Argentina, Brazil, Mexico, Colombia, Chile. "
         "Real-time data, updated every 4 hours."),
        shop_paths,
    ))


@router.get("/tools/openapi-intel.json", include_in_schema=False)
def tools_openapi_intel():
    """OpenAPI spec for the Intel bundle — inflation, scores, stats, export."""
    intel_paths = {k: v for k, v in _TOOLS_OPENAPI["paths"].items()
                   if k in ("/v1/intel/inflation", "/v1/intel/scores", "/analytics/trending", "/stores")}
    return JSONResponse(content=_make_spec(
        "CLI Market — Market Intel Agent",
        ("LATAM retail market intelligence for analysts and fintechs. "
         "Real-time inflation signals, basket stress index, retail aggression scores, "
         "and commodity price trends across 8 countries."),
        intel_paths,
    ))


@router.get("/tools/openapi-account.json", include_in_schema=False)
def tools_openapi_account():
    """OpenAPI spec for the Account bundle — stores, countries, product search."""
    account_paths = {k: v for k, v in _TOOLS_OPENAPI["paths"].items()
                     if k in ("/stores", "/products/search")}
    return JSONResponse(content=_make_spec(
        "CLI Market — Retailer Explorer",
        (f"Explore CLI Market's {RETAILERS_VERIFIED} indexed retailers across 8 countries. "
         "Find which retailers operate in a country, search their catalogs, and understand coverage."),
        account_paths,
    ))


@router.get("/tools", include_in_schema=False)
def tools_redirect():
    """Redirect to cli-market.dev/tools — the human-readable tool directory."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"{_WEBSITE}/tools", status_code=301)


@router.get("/privacy", include_in_schema=False)
def privacy_policy():
    """Privacy policy — required for ChatGPT plugin/GPT Action registration."""
    from fastapi.responses import HTMLResponse
    html = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>CLI Market — Privacy Policy</title>
<style>body{font-family:sans-serif;max-width:720px;margin:40px auto;padding:0 20px;line-height:1.6;color:#333}
h1{color:#111}h2{margin-top:2em}a{color:#0066cc}</style></head>
<body>
<h1>CLI Market — Privacy Policy</h1>
<p><strong>Last updated:</strong> June 2026</p>

<h2>1. What we collect</h2>
<p>CLI Market collects the minimum data necessary to provide the service:</p>
<ul>
  <li><strong>Account data:</strong> email address and username at registration</li>
  <li><strong>API usage:</strong> search queries, timestamps, and request counts (for rate limiting and billing)</li>
  <li><strong>No personal shopping data is stored</strong> beyond what is needed to process your request</li>
</ul>

<h2>2. What we do NOT collect</h2>
<ul>
  <li>We do not collect payment card data (processed by PayPal/Stripe)</li>
  <li>We do not sell data to third parties</li>
  <li>We do not track users across third-party websites</li>
</ul>

<h2>3. Retail price data</h2>
<p>CLI Market indexes publicly available retail prices from e-commerce websites via their public APIs.
No personal consumer data from retailers is collected or stored.</p>

<h2>4. AI agent usage</h2>
<p>When CLI Market tools are called by AI agents (via MCP or ChatGPT Actions), the search query
and country parameter are logged for rate limiting purposes. These logs are retained for 30 days.</p>

<h2>5. Data retention</h2>
<ul>
  <li>Price data: retained indefinitely (product of the service)</li>
  <li>API logs: 30 days</li>
  <li>Account data: until account deletion</li>
</ul>

<h2>6. Your rights</h2>
<p>You may request deletion of your account and associated data by emailing
<a href="mailto:acuba0103@gmail.com">acuba0103@gmail.com</a>.</p>

<h2>7. Contact</h2>
<p>Ricardo Cuba — <a href="mailto:acuba0103@gmail.com">acuba0103@gmail.com</a><br>
<a href="https://cli-market.dev">cli-market.dev</a></p>
</body></html>"""
    return HTMLResponse(content=html)
