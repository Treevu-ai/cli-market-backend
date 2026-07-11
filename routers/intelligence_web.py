"""Public Intelligence Terminal web surfaces — homepage embed + landing page."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from intelligence_web import embed_snippet_for_homepage, pulse_view_model, render_commerce_pulse_page
from server_deps import check_rate_limit

router = APIRouter(tags=["intelligence-web"])

_API_BASE = os.getenv("MARKET_API_URL", "https://cli-market-api.fly.dev").rstrip("/")
_PULSE_TTL = int(os.getenv("PUBLIC_PULSE_CACHE_TTL", "3600"))
_pulse_cache: dict[str, dict] = {}


def _load_pulse(country: str, lang: str = "es") -> dict:
    """Serve the commerce pulse for country+lang.

    Three layers, fastest first:
    1. In-process dict -- avoids a DB round trip on repeat requests within
       the same warm worker.
    2. Shared commerce_pulse_cache table -- written every ~4h by the
       collector daemon, read here regardless of age. This is what makes a
       request never block on live computation: every Fly machine reads the
       same row, so a cold machine or a fresh deploy is still instant.
    3. Live compute -- cold-start bootstrap ONLY, for a country+lang the
       collector has never written yet (e.g. brand new country). Persists
       its result so every subsequent request hits layer 2 instead.
    """
    from market_core import get_db

    from commerce_pulse_cache import (
        ensure_commerce_pulse_cache_table,
        read_pulse_cache,
        write_pulse_cache,
    )

    cc = (country or "PE").strip().upper()[:2]
    language = "en" if lang.lower().startswith("en") else "es"
    key = f"{cc}:{language}"
    now = time.time()

    entry = _pulse_cache.get(key)
    if entry and now - entry["ts"] < _PULSE_TTL:
        return dict(entry["data"])

    db = get_db()
    try:
        ensure_commerce_pulse_cache_table(db)
        cached = read_pulse_cache(db, cc, language)
    finally:
        db.close()
    if cached is not None:
        _pulse_cache[key] = {"data": cached, "ts": now}
        return dict(cached)

    from market_pulse import generate_commerce_pulse

    from routers.dashboard import get_cached_dashboard_data

    pulse = generate_commerce_pulse(
        country=cc,
        days=7,
        lang=language,
        dashboard=get_cached_dashboard_data(),
    )
    pulse.pop("brief", None)

    db = get_db()
    try:
        ensure_commerce_pulse_cache_table(db)
        write_pulse_cache(db, cc, language, pulse)
        db.commit()
    finally:
        db.close()

    _pulse_cache[key] = {"data": pulse, "ts": now}
    return pulse


def _frame_headers() -> dict[str, str]:
    ancestors = os.getenv(
        "INTEL_EMBED_FRAME_ANCESTORS",
        "'self' https://cli-market.dev https://www.cli-market.dev http://localhost:3000",
    )
    return {"Content-Security-Policy": f"frame-ancestors {ancestors}"}


@router.get("/intelligence", response_class=HTMLResponse)
def intelligence_landing(
    request: Request,
    country: str = Query(default="PE"),
    lang: str = Query(default="es"),
):
    """Public Intelligence Terminal landing — This Week in LatAm Commerce."""
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"intelligence-web:{client_ip}")
    pulse = _load_pulse(country, lang)
    html = render_commerce_pulse_page(pulse, embed=False, api_base=_API_BASE)
    return HTMLResponse(content=html, headers=_frame_headers())


@router.get("/embed/commerce-pulse", response_class=HTMLResponse)
def embed_commerce_pulse(
    request: Request,
    country: str = Query(default="PE"),
    lang: str = Query(default="es"),
):
    """Iframe-friendly Commerce Pulse widget for cli-market.dev homepage."""
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"intelligence-embed:{client_ip}")
    pulse = _load_pulse(country, lang)
    html = render_commerce_pulse_page(pulse, embed=True, api_base=_API_BASE)
    return HTMLResponse(content=html, headers=_frame_headers())


@router.get("/public/intelligence/data")
def intelligence_data_json(
    request: Request,
    country: str = Query(default="PE"),
    lang: str = Query(default="es"),
):
    """JSON feed for cli-market.dev client-side widgets."""
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"intelligence-data:{client_ip}")
    pulse = _load_pulse(country, lang)
    return pulse_view_model(pulse)


@router.get("/public/intelligence/embed-snippet", response_class=PlainTextResponse)
def intelligence_embed_snippet():
    """HTML snippet to paste into cli-market.dev homepage."""
    return PlainTextResponse(embed_snippet_for_homepage(_API_BASE), media_type="text/html; charset=utf-8")
