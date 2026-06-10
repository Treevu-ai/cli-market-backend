"""Onboarding funnel — event ingest + dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse

from market_adoption import adoption_summary
from market_adoption_index import (
    compute_adoption_index,
    latest_snapshot,
    list_snapshots,
    score_grade,
)
from market_funnel import FUNNEL_EVENTS, funnel_summary, record_funnel_event
from market_golive import go_live_summary, render_go_live_html
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


@router.get("/analytics/adoption")
def analytics_adoption_public(days: int = 30):
    """Public PyPI + funnel adoption comparison (no PII)."""
    days = max(1, min(days, 90))
    return adoption_summary(days=days)


def _public_signals(signals: dict) -> dict:
    out = {
        "pypi": signals.get("pypi"),
        "funnel": signals.get("funnel"),
        "retention_7d": signals.get("retention_7d"),
        "agent_usage_proxy": signals.get("agent_usage_proxy"),
    }
    gh = signals.get("github")
    if isinstance(gh, dict) and gh.get("ok"):
        out["github"] = gh
    return out


@router.get("/analytics/adoption-index")
def analytics_adoption_index_public(
    days: int = 30,
    github: bool = False,
    cached: bool = True,
):
    """Public Adoption Index V1 (composite score, no PII)."""
    days = max(1, min(days, 90))
    if cached:
        snap = latest_snapshot()
        if snap and snap.get("score") is not None:
            return {
                "scope": snap.get("scope"),
                "version": "v1",
                "score": snap["score"],
                "grade": score_grade(float(snap["score"])),
                "breakdown": snap.get("breakdown"),
                "signals": _public_signals(snap.get("signals") or {}),
                "computed_at": snap.get("created_at"),
                "source": "snapshot",
            }
    live = compute_adoption_index(days=days, include_github=github)
    return {
        "scope": live["scope"],
        "version": live["version"],
        "score": live["score"],
        "grade": live["grade"],
        "breakdown": live["breakdown"],
        "signals": _public_signals(live.get("signals") or {}),
        "computed_at": live["computed_at"],
        "source": "live",
    }


@router.get("/dashboard/adoption-index")
def dashboard_adoption_index(
    authorization: str | None = Header(None),
    days: int = 30,
    github: bool = True,
    history: int = 14,
):
    """Admin: live Adoption Index + recent snapshots."""
    require_admin(authorization)
    days = max(1, min(days, 90))
    history = max(0, min(history, 90))
    live = compute_adoption_index(days=days, include_github=github)
    return {
        "live": live,
        "history": list_snapshots(limit=history) if history else [],
    }


@router.get("/analytics/pypi")
def analytics_pypi_public():
    """Public PyPI install stats via Pepy (cached server-side)."""
    data = pepy_summary()
    if not data.get("ok"):
        return {
            "ok": False,
            "project": data.get("project"),
            "pepy_project": data.get("pepy_project"),
            "configured": data.get("configured", False),
        }
    return {
        "ok": True,
        "project": data["project"],
        "pepy_project": data.get("pepy_project"),
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


@router.get("/dashboard/go-live")
def dashboard_go_live(
    authorization: str | None = Header(None),
    days: int = 30,
):
    """Admin go-live KPIs + alerts (activation, revenue, data moat)."""
    require_admin(authorization)
    days = max(1, min(days, 90))
    return go_live_summary(days=days)


@router.get("/dashboard/go-live/page")
def dashboard_go_live_page(
    authorization: str | None = Header(None),
    days: int = 30,
):
    """Admin HTML go-live dashboard."""
    require_admin(authorization)
    days = max(1, min(days, 90))
    return HTMLResponse(render_go_live_html(days=days))