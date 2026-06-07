"""Onboarding funnel — event ingest + dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from market_funnel import FUNNEL_EVENTS, funnel_summary, record_funnel_event
from market_pepy import pepy_summary
from server_deps import auth_user, check_rate_limit, require_admin

router = APIRouter(tags=["funnel"])


@router.post("/v1/events")
def ingest_event(body: dict, authorization: str | None = Header(None)):
    """Record onboarding funnel event (CLI / landing). Rate limited."""
    check_rate_limit("funnel-events")
    event = (body.get("event") or "").strip().lower()
    if event not in FUNNEL_EVENTS:
        raise HTTPException(status_code=400, detail=f"event must be one of: {sorted(FUNNEL_EVENTS)}")

    username = None
    if authorization:
        try:
            token = authorization.replace("Bearer ", "").strip()
            username = auth_user(token)
        except HTTPException:
            pass

    if not username:
        username = (body.get("username") or "").strip() or None

    return record_funnel_event(
        event,
        username=username,
        session_id=(body.get("session_id") or "").strip() or None,
        meta=body.get("meta") if isinstance(body.get("meta"), dict) else None,
        dedupe=bool(body.get("dedupe", event in ("install", "first_search"))),
    )


@router.get("/dashboard/funnel")
def dashboard_funnel(
    authorization: str | None = Header(None),
    days: int = 30,
    include_test: bool = False,
):
    """Admin funnel dashboard: TTFV, TTC, drop-off."""
    require_admin(authorization)
    days = max(1, min(days, 90))
    return funnel_summary(days=days, include_test=include_test)


@router.get("/analytics/funnel")
def analytics_funnel_public(days: int = 30):
    """Public aggregate funnel (no PII). Test/smoke/PAM traffic excluded."""
    days = max(1, min(days, 90))
    data = funnel_summary(days=days, include_test=False)
    return {
        "window_days": data["window_days"],
        "events": data["events"],
        "conversion": data["conversion"],
        "ttfv_median_minutes": data["ttfv_median_minutes"],
        "ttc_median_hours": data["ttc_median_hours"],
        "funnel_steps": data["funnel_steps"],
        "excluded_test_events": data["excluded_test_events"],
    }


@router.get("/analytics/pypi")
def analytics_pypi_public():
    """Public PyPI install stats via Pepy (cached server-side)."""
    data = pepy_summary()
    if not data.get("ok"):
        return {
            "ok": False,
            "project": data.get("project"),
            "configured": data.get("configured", False),
        }
    return {
        "ok": True,
        "project": data["project"],
        "total_downloads": data.get("total_downloads"),
        "downloads_last_24h": data.get("downloads_last_24h"),
        "downloads_last_7d": data.get("downloads_last_7d"),
        "downloads_last_30d": data.get("downloads_last_30d"),
        "downloads_last_30d_no_ci": data.get("downloads_last_30d_no_ci"),
        "top_version_30d": data.get("top_version_30d"),
        "latest_version": data.get("latest_version"),
        "fetched_at": data.get("fetched_at"),
    }


@router.get("/dashboard/pypi")
def dashboard_pypi(authorization: str | None = Header(None)):
    """Admin Pepy stats (full payload)."""
    require_admin(authorization)
    return pepy_summary()