"""Intel / inflation tracking — the data-moat-as-product angle.

Endpoints:
  GET /v1/intel/inflation       Per-product price delta over the last N days
  GET /v1/intel/alerts          Price movers vs threshold (from price_history)
  GET /v1/intel/indicators      Indicator catalog (public API + internal moat)
  GET /v1/intel/indicators/{key} Latest values for one indicator
  GET /v1/intel/scores          Composite moat scores
  GET /v1/intel/basket-stress   Canasta affordability signal
  GET /v1/intel/brief           Aggregated intel brief (scores + enrichment + subcategories)
  GET /v1/intel/pulse           Agentic Commerce Pulse — weekly research report (JSON/markdown)
  GET /v1/intel/forecast        Price forecast + procurement signal from price_history
  GET /v1/intel/arbitrage       Cross-border arbitrage detection (LatAm)
  POST /v1/intel/refresh              Recompute and fetch external indicators
  GET  /v1/intel/enrichment           Latest enrichment indicators
  GET  /v1/intel/enrichment/subcategories  Per-staple enrichment (leche, arroz, …)
  POST /v1/intel/enrichment/refresh   Refresh enrichment indicators only
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, field_validator

from market_core import STORES
from market_enrich_subcategory import ENRICH_SUBCATEGORIES, get_subcategory_enrichment
from market_units import price_per_base_unit
from market_indicators import (
    ENRICHMENT_INDICATOR_KEYS,
    TIER2_INDICATOR_KEYS,
    build_intel_brief,
    compute_basket_stress,
    compute_composite_scores,
    get_indicator_catalog,
    get_latest_values,
    refresh_enrichment_only,
    refresh_indicators,
)

from server_deps import get_db_dep, require_api_key, require_pro

router = APIRouter(tags=["intel"])

# ── In-process TTL cache (no Redis required) ──────────────────────────────────
# Keyed by (endpoint, *args). Entries expire after TTL_SECS.
# Acceptable for read-heavy intel endpoints where data changes every ~4h.

_TTL_SECS = 300  # 5 minutes
_cache: dict[tuple, tuple[float, Any]] = {}


def _cache_get(key: tuple) -> Any | None:
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < _TTL_SECS:
        return entry[1]
    return None


def _cache_set(key: tuple, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/v1/intel/inflation")
def inflation_tracker(
    country: str | None = None,
    line: str | None = None,
    days: int = 30,
    limit: int = 100,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    require_api_key(authorization)
    _ck = ("inflation", country, line, days, limit)
    if (cached := _cache_get(_ck)) is not None:
        return cached
    """Compute per-product price deltas within the last `days` window.

    Compares earliest vs latest recorded point per product in the window.
    Reads price_history (append-only, one row per price *change*) joined
    with price_snapshots for display metadata — not price_snapshots alone,
    which is upserted one-row-per-(product_id, store) and can therefore
    never hold two distinct points in time for the same product (see
    cli-market-backend#127).

    Intended for agent-facing inflation signals — not official CPI indices.
    """
    since = _since_iso(days)
    q = """
        SELECT ph.product_id, ph.store, ph.price, ph.recorded_at,
               ps.name, ps.store_name, ps.currency, ps.line
        FROM price_history ph
        LEFT JOIN price_snapshots ps ON ps.product_id = ph.product_id AND ps.store = ph.store
        WHERE ph.price > 0 AND ph.recorded_at >= ?
    """
    params: list = [since]
    if country:
        cc_stores = [k for k, v in STORES.items() if v["country"] == country.upper() and not v.get("disabled")]
        if cc_stores:
            q += f" AND ph.store IN ({','.join('?' * len(cc_stores))})"
            params.extend(cc_stores)
    if line:
        q += " AND ps.line = ?"
        params.append(line)
    # No inner LIMIT — we need the full window to find earliest+latest per product.
    # The outer items[:limit] caps the response size.
    rows = db.execute(q, params).fetchall()

    prods: dict[str, list[dict]] = {}
    for r in rows:
        name = r["name"] or ""
        pid = r["product_id"]
        # Normalize to price-per-base-unit so pack-size changes don't appear as inflation.
        ppu = price_per_base_unit(r["price"], name)
        normalized_price = ppu["price_per"] if ppu else r["price"]
        basis = ppu["basis"] if ppu else "unit"
        # Use canonical product_id when available — collapses "Arroz Extra Costeño 750g"
        # and "Arroz Extra COSTEÑO Bolsa 750g" into the same bucket per store.
        k = f"{r['store']}|{pid}" if pid else f"{r['store']}|{name.lower()[:40]}"
        prods.setdefault(k, []).append(
            {
                "price": normalized_price,
                "raw_price": r["price"],
                "basis": basis,
                "date": r["recorded_at"],
                "store": r["store_name"],
                "currency": r["currency"],
                "name": name,
                "product_id": pid,
            }
        )

    _MAX_DELTA_PCT = 300.0  # cap: changes beyond this are likely SKU/pack switches, not real inflation

    items: list[dict] = []
    for _key, snaps in prods.items():
        snaps.sort(key=lambda s: s["date"])
        if len(snaps) >= 2:
            first = snaps[0]
            last = snaps[-1]
            if first["price"] > 0:
                d = round(last["price"] - first["price"], 4)
                dp = round((d / first["price"]) * 100, 1)
                if abs(dp) > _MAX_DELTA_PCT:
                    # Likely a presentation/SKU switch — skip to avoid polluting avg
                    continue
                items.append(
                    {
                        "product": last["name"],
                        "product_id": last["product_id"],
                        "first_price": first["raw_price"],
                        "last_price": last["raw_price"],
                        "first_price_per_unit": first["price"],
                        "last_price_per_unit": last["price"],
                        "price_basis": first["basis"],
                        "first_date": first["date"],
                        "last_date": last["date"],
                        "delta": d,
                        "delta_pct": dp,
                        "currency": first["currency"],
                        "store": first["store"],
                    }
                )
    items.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)
    items = items[:limit]
    avg = round(sum(i["delta_pct"] for i in items) / len(items), 1) if items else 0
    result = {
        "country": country,
        "line": line,
        "days": days,
        "since": since,
        "products_tracked": len(items),
        "avg_inflation_pct": avg,
        "items": items,
        "disclaimer": "Internal collector signal — not an official inflation index. Prices normalized per base unit (per_kg/L) to filter pack-size changes.",
    }
    _cache_set(_ck, result)
    return result


@router.get("/v1/intel/alerts")
def intel_alerts(
    product: str,
    store: str | None = None,
    threshold_pct: float = 5.0,
    limit: int = 10,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    require_api_key(authorization)
    """Price alerts from price_history when delta exceeds threshold_pct."""
    since = _since_iso(30)
    q = """
        SELECT ph.product_id, ph.store, ph.price, ph.recorded_at, ps.name, ps.store_name, ps.currency
        FROM price_history ph
        LEFT JOIN price_snapshots ps ON ps.product_id = ph.product_id AND ps.store = ph.store
        WHERE ph.recorded_at >= ? AND ph.price > 0
          AND LOWER(COALESCE(ps.name, '')) LIKE ?
    """
    params: list = [since, f"%{product.lower()}%"]
    if store:
        q += " AND ph.store = ?"
        params.append(store)
    q += " ORDER BY ph.recorded_at DESC LIMIT ?"
    params.append(limit * 20)
    rows = db.execute(q, params).fetchall()

    series: dict[str, list] = {}
    for r in rows:
        k = f"{r['store']}|{r['product_id']}"
        series.setdefault(k, []).append(r)

    alerts: list[dict] = []
    for _key, pts in series.items():
        if len(pts) < 2:
            continue
        pts.sort(key=lambda x: x["recorded_at"])
        first, last = pts[0], pts[-1]
        if not first["price"] or first["price"] <= 0:
            continue
        dp = round((float(last["price"]) - float(first["price"])) / float(first["price"]) * 100, 1)
        if abs(dp) >= threshold_pct:
            alerts.append(
                {
                    "product_id": last["product_id"],
                    "product": last["name"] or product,
                    "store": last["store"],
                    "store_name": last["store_name"],
                    "currency": last["currency"],
                    "first_price": first["price"],
                    "last_price": last["price"],
                    "delta_pct": dp,
                    "direction": "up" if dp > 0 else "down",
                }
            )
    alerts.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)
    return {
        "product": product,
        "store": store,
        "threshold_pct": threshold_pct,
        "alerts": alerts[:limit],
        "message": f"{len(alerts[:limit])} alert(s) above {threshold_pct}% threshold.",
    }


@router.get("/v1/intel/indicators")
def list_indicators(authorization: str | None = Header(None)):
    """Catalog of moat indicators (internal, external public APIs, composite)."""
    require_api_key(authorization)
    return {
        "count": len(get_indicator_catalog()),
        "indicators": get_indicator_catalog(),
    }


@router.get("/v1/intel/indicators/{indicator_key}")
def get_indicator(
    indicator_key: str,
    country: str | None = None,
    line: str | None = None,
    limit: int = 30,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    require_api_key(authorization)
    """Latest time-series points for one indicator."""
    values = get_latest_values(db, indicator_key=indicator_key, country=country, line=line, limit=limit)
    meta = next((i for i in get_indicator_catalog() if i["key"] == indicator_key), None)
    return {
        "key": indicator_key,
        "definition": meta,
        "country": country,
        "line": line,
        "values": values,
    }


@router.get("/v1/intel/scores")
def intel_scores(
    country: str | None = None,
    line: str | None = None,
    subcategory: str | None = None,
    authorization: str | None = Header(None),
):
    require_api_key(authorization)
    """Composite scores blending moat signals and public macro data.

    subcategory narrows below the line level (e.g. "bebidas" within
    "supermercados") — see market_spread.infer_subcategory for the bucket
    list. Omit for the existing whole-line blend.
    """
    _ck = ("scores", country, line, subcategory)
    if (cached := _cache_get(_ck)) is not None:
        return cached
    result = compute_composite_scores(country=country, line=line, subcategory=subcategory)
    _cache_set(_ck, result)
    return result


@router.get("/v1/intel/basket-stress")
def basket_stress(country: str | None = None, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    require_api_key(authorization)
    """Minimum canasta básica stress index for a country."""
    value = compute_basket_stress(db, country)
    return {
        "country": country,
        "basket_stress_index": value,
        "interpretation": (
            "elevated (>105)" if value and value > 105
            else "eased (<95)" if value and value < 95
            else "normal"
        ),
        "disclaimer": "Based on cheapest indexed staple per item — not official CPI basket.",
    }


@router.get("/v1/intel/brief")
def intel_brief(
    country: str | None = None,
    line: str | None = None,
    days: int = 7,
    include_catalog: bool = False,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    """Aggregated intel brief: headline, shelf signals, macro gap, confidence,
    composite scores, enrichment, subcategory signals, and optionally the catalog.

    Delegates to market_core.market_indicators.build_intel_brief(), which also
    live-recomputes moat_freshness/basket_stress_index and per-country store
    coverage instead of trusting the cached indicator values this endpoint used
    to read directly (cli-market-backend#127 S6/S7) — see that function's
    docstring/comments for the full rationale.

    Response shape is designed so market_core._slice_intel_brief() can extract
    'analytics', 'enrichment', 'subcategories', or 'catalog' slices without
    a separate round-trip per section.
    """
    require_api_key(authorization)
    _ck = ("brief", country, line, days, include_catalog)
    if (cached := _cache_get(_ck)) is not None:
        return cached
    result = build_intel_brief(db, country=country, line=line, days=days, include_catalog=include_catalog)
    _cache_set(_ck, result)
    return result


@router.get("/v1/intel/pulse")
def commerce_pulse(
    country: str = Query(default="PE", description="ISO country code"),
    days: int = Query(default=7, ge=1, le=90),
    lang: str = Query(default="es", description="es or en"),
    fmt: str = Query(default="json", alias="format", description="json or markdown"),
    include_brief: bool = Query(default=False),
    authorization: str | None = Header(None),
):
    """Agentic Commerce Pulse — BBVA-style weekly report from moat signals."""
    require_api_key(authorization)
    from market_pulse import generate_commerce_pulse

    from routers.dashboard import get_cached_dashboard_data

    cc = (country or "PE").strip().upper()[:2]
    language = "en" if (lang or "").lower().startswith("en") else "es"
    pulse = generate_commerce_pulse(
        country=cc,
        days=days,
        lang=language,
        dashboard=get_cached_dashboard_data(),
    )
    if not include_brief:
        pulse = {k: v for k, v in pulse.items() if k != "brief"}
    if fmt == "markdown":
        return PlainTextResponse(pulse.get("markdown", ""), media_type="text/markdown; charset=utf-8")
    return pulse


@router.get("/v1/intel/forecast")
def intel_forecast(
    product: str = Query(..., min_length=2, description="Product name, e.g. leche, arroz"),
    country: str = Query(default="PE"),
    horizon_days: int = Query(default=21, ge=1, le=90),
    lookback_days: int = Query(default=90, ge=7, le=365),
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    """Price forecast from price_history — Walmart Labs procurement signal."""
    require_api_key(authorization)
    from market_predict import forecast_product_price

    return forecast_product_price(
        db,
        product,
        country=(country or "PE").upper()[:2],
        horizon_days=horizon_days,
        lookback_days=lookback_days,
    )


@router.get("/v1/intel/arbitrage")
def intel_arbitrage(
    product: str | None = Query(default=None, description="Product query across countries"),
    canonical_product_id: str | None = Query(default=None, alias="canonical_id"),
    countries: str | None = Query(default=None, description="Comma-separated ISO codes, e.g. PE,MX,CL"),
    min_spread_pct: float = Query(default=10.0, ge=0),
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    """Cross-border shelf-price arbitrage — buy country vs sell country in USD."""
    require_api_key(authorization)
    from market_predict import detect_arbitrage, detect_arbitrage_canonical

    scope = [c.strip().upper()[:2] for c in (countries or "").split(",") if c.strip()] or None
    if not canonical_product_id and not product:
        raise HTTPException(status_code=400, detail="product or canonical_id required")
    if canonical_product_id:
        return detect_arbitrage_canonical(
            db,
            canonical_product_id,
            countries=scope,
            min_spread_pct=min_spread_pct,
        )
    return detect_arbitrage(
        db,
        product,
        countries=scope,
        min_spread_pct=min_spread_pct,
    )


@router.post("/v1/intel/refresh")
def intel_refresh(country: str | None = None, line: str | None = None, authorization: str | None = Header(None)):
    require_api_key(authorization)
    """Refresh internal computed indicators and fetch public API macro signals."""
    result = refresh_indicators(country=country, line=line)
    return {"status": "ok", **result}


@router.get("/v1/intel/enrichment")
def intel_enrichment(country: str | None = None, limit: int = 20, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    require_api_key(authorization)
    """Latest enrichment indicators (OFF, Wikimedia, weather, food CPI) for a country."""
    values = get_latest_values(db, country=country, limit=limit * 3)
    keys = ENRICHMENT_INDICATOR_KEYS
    enriched = [v for v in values if v.get("key") in keys]
    return {
        "country": country,
        "count": len(enriched),
        "indicators": enriched,
        "sources": [
            "openfoodfacts",
            "wikimedia",
            "open-meteo.com",
            "worldbank",
            "imf.org",
            "eurostat",
            "bcb.gov.br",
        ],
        "tier2_keys": list(TIER2_INDICATOR_KEYS),
    }


@router.get("/v1/intel/enrichment/subcategories")
def intel_enrichment_subcategories(country: str = "PE", authorization: str | None = Header(None), db = Depends(get_db_dep)):
    require_api_key(authorization)
    """Per-subcategory signals: price momentum, wiki demand, min shelf price."""
    items = get_subcategory_enrichment(db, country)
    return {
        "country": country.upper(),
        "subcategories": ENRICH_SUBCATEGORIES,
        "count": len(items),
        "items": items,
    }


@router.post("/v1/intel/enrichment/refresh")
def intel_enrichment_refresh(country: str | None = None, authorization: str | None = Header(None)):
    require_api_key(authorization)
    """Refresh only enrichment indicators (OFF sample, Wiki, weather, food CPI)."""
    return refresh_enrichment_only(country=country)


class PricePulseSubmit(BaseModel):
    country: str = "PE"
    callback_url: str = ""

    @field_validator("country")
    @classmethod
    def _country_code(cls, v: str) -> str:
        c = (v or "PE").strip().upper()[:2]
        if len(c) != 2:
            raise ValueError("country must be 2-letter ISO code")
        return c


@router.post("/v1/intel/price-pulse")
def submit_price_pulse(body: PricePulseSubmit, authorization: str | None = Header(None)):
    username = require_pro(authorization)
    from market_core.intel_jobs import db_create_intel_job

    job = db_create_intel_job(
        username,
        job_type="price_pulse",
        country=body.country,
        callback_url=(body.callback_url or "").strip(),
    )
    return {"ok": True, **job}


@router.get("/v1/intel/price-pulse/{job_id}")
def get_price_pulse_status(job_id: str, authorization: str | None = Header(None)):
    username = require_pro(authorization)
    from market_core.intel_jobs import db_get_intel_job

    job = db_get_intel_job(job_id)
    if not job or job.get("username") != username:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/v1/intel/price-pulse/{job_id}/download")
def download_price_pulse_report(job_id: str, authorization: str | None = Header(None)):
    from pathlib import Path

    username = require_pro(authorization)
    from market_core.intel_jobs import db_get_intel_job

    job = db_get_intel_job(job_id)
    if not job or job.get("username") != username:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"Job status is {job.get('status')}")
    path = (job.get("output_path") or "").strip()
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Report file missing")
    return FileResponse(path, media_type="text/markdown", filename=Path(path).name)


# ── Previously "Unknown tool" in /mcp — registered in cli-market-core's MCP
# schema and dispatcher, but this REST layer never implemented them. Core
# already ships the compute_* functions (used by its own optional
# market_core.api_routes.router); wired directly here rather than mounting
# that whole router, which would collide with several paths this file
# already implements independently (e.g. /v1/intel/alerts, /v1/household). ──

@router.get("/v1/intel/price-risk")
def intel_price_risk(
    country: str | None = None,
    line: str | None = None,
    days: int = 7,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    """Price Risk Intelligence — which categories are becoming volatile?"""
    require_api_key(authorization)
    from market_core.market_intel_products import compute_price_risk

    return compute_price_risk(db, country=country, line=line, days=days)


@router.get("/v1/intel/informal-signal")
def intel_informal_signal(
    country: str,
    line: str = "supermercados",
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    """Coverage-honesty signal — formal channels only, no informal-economy estimate."""
    require_api_key(authorization)
    from market_core.market_informal_signal import compute_informal_signal

    return compute_informal_signal(db, country=country, line=line)


@router.get("/v1/intel/promo-detector")
def intel_promo_detector(
    product: str,
    store: str | None = None,
    days: int = 30,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    """Promo authenticity — flags discounts staged via recent list-price inflation."""
    require_api_key(authorization)
    from market_core.market_promo_detector import compute_promo_authenticity

    return compute_promo_authenticity(db, product=product, store=store, days=days)


@router.get("/v1/intel/retailer-scorecard")
def intel_retailer_scorecard(
    store: str,
    days: int = 30,
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    """Retailer scorecard — coverage/freshness, catalog quality, and price volatility for one store."""
    require_api_key(authorization)
    from market_core.market_retailer_scorecard import compute_retailer_scorecard

    try:
        return compute_retailer_scorecard(db, store=store, days=days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
