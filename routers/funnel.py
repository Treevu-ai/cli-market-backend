"""Onboarding funnel — event ingest + dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from market_funnel import FUNNEL_EVENTS, funnel_summary, record_funnel_event
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
):
    """Admin funnel dashboard: TTFV, TTC, drop-off."""
    require_admin(authorization)
    days = max(1, min(days, 90))
    return funnel_summary(days=days)


@router.get("/analytics/funnel")
def analytics_funnel_public(days: int = 30):
    """Public aggregate funnel (no PII)."""
    days = max(1, min(days, 90))
    data = funnel_summary(days=days)
    return {
        "window_days": data["window_days"],
        "events": data["events"],
        "conversion": data["conversion"],
        "ttfv_median_minutes": data["ttfv_median_minutes"],
        "ttc_median_hours": data["ttc_median_hours"],
        "funnel_steps": data["funnel_steps"],
    }