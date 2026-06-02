"""Canonical marketing stats — single source of truth for README, PyPI, landing.

Always phrase retailers as: "60 retailers, 30 verified active" (defined vs live).
Auto-derived from market_stores.py, market_mcp.py, store_credentials.py.
Run: python3 ops/sync_market_stats.py
"""

from __future__ import annotations

# ── Derived from codebase (never stale) ──────────────────────────────────────

def _stores():
    from market_stores import STORES
    return STORES

def _default_store_keys():
    from store_credentials import get_default_stores
    return get_default_stores()

def _mcp_tools_count():
    from market_mcp import TOOLS
    return len(TOOLS)

def _indicators_count():
    from market_indicators import INDICATOR_DEFINITIONS
    return len(INDICATOR_DEFINITIONS)


# ── Canonical figures (computed at import time) ─────────────────────────────

_stores = _stores()
_defaults = frozenset(_default_store_keys())

RETAILERS_DEFINED = len(_stores)
RETAILERS_VERIFIED = len(_defaults)  # active = stores with credentials configured
PLATFORMS = 3
PLATFORM_VTEX = sum(1 for s in _stores.values() if s.get("platform") == "vtex")
PLATFORM_SHOPIFY = sum(1 for s in _stores.values() if s.get("platform") == "shopify")
PLATFORM_MAGENTO = sum(1 for s in _stores.values() if s.get("platform") == "magento")
COUNTRIES = len({s["country"] for s in _stores.values()})
COUNTRY_CODES = tuple(sorted({s["country"] for s in _stores.values()}))
MCP_TOOLS = _mcp_tools_count()
INDICATORS_COUNT = _indicators_count()
ENRICHMENT_SOURCES_LABEL = "OFF · Wikimedia · Open-Meteo · World Bank · IMF · Eurostat · BCB"
PRICES_REFRESH_HOURS = 4

def _live_price_label(fallback: str = "45,000+") -> str:
    """Fetch total_indexed from live dashboard and round to nearest thousand."""
    import os
    try:
        import httpx
        api = os.getenv("MARKET_API_URL", "https://cli-market-production.up.railway.app")
        r = httpx.get(f"{api}/dashboard/data", timeout=10)
        r.raise_for_status()
        n = r.json().get("kpis", {}).get("total_indexed", 0)
        if n and n > 0:
            return f"{round(n / 1000) * 1000:,}+"
    except Exception:
        pass
    return fallback

PRICES_VERIFIED_LABEL = _live_price_label()
PACKAGE_VERSION = "1.7.0"
LICENSE = "MIT"
PAYMENTS_LABEL = "PayPal + QR (Yape/Plin)"
BUSINESS_LINES = 6

SHOPIFY_BRANDS = tuple(
    _stores[k]["name"] for k in sorted(_stores)
    if _stores[k].get("platform") == "shopify"
)

RETAILERS_PHRASE_EN = f"{RETAILERS_DEFINED} retailers, {RETAILERS_VERIFIED} verified active"
RETAILERS_PHRASE_ES = f"{RETAILERS_DEFINED} retailers, {RETAILERS_VERIFIED} verificados activos"
PLATFORMS_PHRASE_EN = f"{PLATFORMS} platforms (VTEX · Shopify · Magento)"
PLATFORMS_PHRASE_ES = f"{PLATFORMS} plataformas (VTEX · Shopify · Magento)"


def header_en() -> str:
    return (
        "CLI Market — Commerce infrastructure for AI agents.\n"
        f"{RETAILERS_DEFINED} retailers across {PLATFORMS} platforms "
        f"(VTEX · Shopify · Magento), {RETAILERS_VERIFIED} verified live.\n"
        f"{COUNTRIES} countries. {MCP_TOOLS} MCP tools. {PRICES_VERIFIED_LABEL} verified shelf prices, "
        f"normalized per kg/L, refreshed every {PRICES_REFRESH_HOURS}h.\n"
        "One pip install. One API. Zero scraping. MIT."
    )


def header_es() -> str:
    return (
        "CLI Market — Infraestructura de comercio para agentes de IA.\n"
        f"{RETAILERS_DEFINED} retailers en {PLATFORMS} plataformas "
        f"(VTEX · Shopify · Magento), {RETAILERS_VERIFIED} verificados y activos.\n"
        f"{COUNTRIES} países. {MCP_TOOLS} herramientas MCP. {PRICES_VERIFIED_LABEL} precios reales de góndola, "
        f"normalizados por kg/L, actualizados cada {PRICES_REFRESH_HOURS}h.\n"
        "Un pip install. Una API. Cero scraping. MIT."
    )


def pypi_summary() -> str:
    return (
        "mcp-name: io.github.Treevu-ai/cli-market-world - "
        "CLI Market: commerce API for AI agents. "
        f"{MCP_TOOLS} MCP tools, {INDICATORS_COUNT} indicators, "
        f"{RETAILERS_VERIFIED} verified retailers in {COUNTRIES} countries. MIT."
    )


def readme_tagline_html() -> str:
    return (
        f"<b>Commerce infrastructure for AI agents.</b><br>"
        f"{RETAILERS_DEFINED} retailers ({RETAILERS_VERIFIED} verified). {COUNTRIES} countries. "
        f"{PLATFORMS} platforms. {MCP_TOOLS} MCP tools. {PAYMENTS_LABEL}.<br>"
        f"{PRICES_VERIFIED_LABEL} verified shelf prices, normalized per kg/L, refreshed every {PRICES_REFRESH_HOURS} hours.<br>"
        f"One <code>pip install</code>. One API. Zero scraping."
    )


def server_json_description() -> str:
    return (
        f"Commerce for AI agents. {MCP_TOOLS} MCP tools. "
        f"{RETAILERS_DEFINED} retailers ({RETAILERS_VERIFIED} verified), {COUNTRIES} countries, {PLATFORMS} platforms."
    )


def seo_description() -> str:
    return (
        f"Commerce API for AI agents. {MCP_TOOLS} MCP tools, {RETAILERS_PHRASE_EN}. "
        f"{COUNTRIES} countries. {PRICES_VERIFIED_LABEL} verified shelf prices refreshed every {PRICES_REFRESH_HOURS} hours. "
        "Normalized per kg/L, quality-filtered. pip install cli-market."
    )
