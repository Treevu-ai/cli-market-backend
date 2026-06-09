"""Observatory — public and admin adoption telemetry."""

from __future__ import annotations

from fastapi import APIRouter, Header

from market_observatory import compute_daily_observatory_metrics, observatory_summary
from server_deps import require_admin

router = APIRouter(tags=["observatory"])


@router.get("/analytics/observatory")
def analytics_observatory_public(days: int = 30):
    """Public aggregate agent telemetry (no PII)."""
    days = max(1, min(days, 90))
    return observatory_summary(days=days)


@router.get("/dashboard/observatory")
def dashboard_observatory(
    authorization: str | None = Header(None),
    days: int = 30,
):
    """Admin: full observatory summary."""
    require_admin(authorization)
    days = max(1, min(days, 90))
    return observatory_summary(days=days)


@router.post("/admin/observatory/snapshot")
def admin_observatory_snapshot(authorization: str | None = Header(None)):
    """Admin/cron: compute and persist daily_observatory_metrics."""
    require_admin(authorization)
    return compute_daily_observatory_metrics()
