"""Price alert CRUD and event history endpoints.

Endpoints:
  POST   /v1/alerts                      Create a new price alert (Pro+)
  GET    /v1/alerts                      List caller's active alerts
  DELETE /v1/alerts/{alert_id}           Delete an alert
  PUT    /v1/alerts/{alert_id}/toggle    Enable / disable an alert
  GET    /v1/alerts/{alert_id}/events    Firing history for one alert
  POST   /v1/alerts/evaluate             Trigger evaluation manually (Pro+)

Conditions: price_jump | price_drop | price_min_30d | dispersion_anomaly
Channels:   notify_email (Pro) | notify_webhook (Enterprise)
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, field_validator

from market_alerts import (
    SUPPORTED_CONDITIONS,
    db_create_alert,
    db_delete_alert,
    db_get_alert,
    db_list_alerts,
    db_toggle_alert,
    evaluate_alerts,
)
from market_core import get_db, db_get_subscription
from server_deps import require_api_key

router = APIRouter(tags=["alerts"])

_ALERTS_TIERS = {"starter", "pro", "enterprise", "builder"}


def _require_alerts_tier(username: str) -> None:
    sub = db_get_subscription(username)
    tier = (sub.get("tier") or "free").lower()
    if tier not in _ALERTS_TIERS:
        from market_billing import price_label_for_plan
        raise HTTPException(
            status_code=403,
            detail=(
                f"Price alerts require CLI Market Starter or Pro ({price_label_for_plan('starter')} / {price_label_for_plan('pro')}). "
                "Upgrade with: market upgrade --plan starter"
            ),
        )


def _check_alert_limit(username: str) -> None:
    """Enforce per-tier alert count limit (Pro=10, Enterprise=unlimited)."""
    from market_billing import TIERS
    sub = db_get_subscription(username)
    tier = (sub.get("tier") or "free").lower()
    limit = TIERS.get(tier, TIERS["free"]).get("alerts", 0)
    if limit == 0:
        raise HTTPException(status_code=403, detail="Upgrade to Pro to create price alerts.")
    if limit > 0:
        current = len(db_list_alerts(username))
        if current >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Alert limit reached ({limit} alerts on {tier} plan). Delete one to add another.",
            )


class AlertCreateRequest(BaseModel):
    name: str = ""
    condition: str
    product_query: str
    store: str = ""
    threshold_pct: float = 5.0
    notify_email: str = ""
    notify_webhook: str = ""
    cooldown_hours: int = 24

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: str) -> str:
        if v not in SUPPORTED_CONDITIONS:
            raise ValueError(f"condition must be one of: {', '.join(SUPPORTED_CONDITIONS)}")
        return v

    @field_validator("threshold_pct")
    @classmethod
    def validate_threshold(cls, v: float) -> float:
        if not 0.1 <= v <= 100.0:
            raise ValueError("threshold_pct must be between 0.1 and 100")
        return v

    @field_validator("cooldown_hours")
    @classmethod
    def validate_cooldown(cls, v: int) -> int:
        if not 1 <= v <= 720:
            raise ValueError("cooldown_hours must be between 1 and 720")
        return v


@router.post("/v1/alerts")
def create_alert(body: AlertCreateRequest, authorization: str | None = Header(None)):
    """Create a price alert. Requires Pro tier."""
    username = require_api_key(authorization)
    _require_alerts_tier(username)
    _check_alert_limit(username)
    alert = db_create_alert(
        username=username,
        name=body.name or f"{body.condition} · {body.product_query[:30]}",
        condition=body.condition,
        product_query=body.product_query,
        store=body.store,
        threshold_pct=body.threshold_pct,
        notify_email=body.notify_email,
        notify_webhook=body.notify_webhook,
        cooldown_hours=body.cooldown_hours,
    )
    return {"ok": True, "alert": alert}


@router.get("/v1/alerts")
def list_alerts(authorization: str | None = Header(None)):
    """List all price alerts for the authenticated user."""
    username = require_api_key(authorization)
    alerts = db_list_alerts(username)
    return {"alerts": alerts, "total": len(alerts)}


@router.delete("/v1/alerts/{alert_id}")
def delete_alert(alert_id: str, authorization: str | None = Header(None)):
    """Delete a price alert by ID."""
    username = require_api_key(authorization)
    deleted = db_delete_alert(username, alert_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Alert not found or not yours")
    return {"ok": True, "deleted": alert_id}


@router.put("/v1/alerts/{alert_id}/toggle")
def toggle_alert(alert_id: str, body: dict | None = None, authorization: str | None = Header(None)):
    """Enable or disable a price alert. Pass {\"active\": true/false} in body."""
    username = require_api_key(authorization)
    alert = db_get_alert(alert_id)
    if not alert or alert.get("username") != username:
        raise HTTPException(status_code=404, detail="Alert not found or not yours")
    active = bool((body or {}).get("active", not alert.get("active", True)))
    updated = db_toggle_alert(username, alert_id, active)
    return {"ok": True, "alert": updated}


@router.get("/v1/alerts/{alert_id}/events")
def alert_events(
    alert_id: str,
    limit: int = 20,
    authorization: str | None = Header(None),
):
    """Firing history for a specific alert."""
    username = require_api_key(authorization)
    alert = db_get_alert(alert_id)
    if not alert or alert.get("username") != username:
        raise HTTPException(status_code=404, detail="Alert not found or not yours")
    db = get_db()
    rows = db.execute(
        "SELECT * FROM alert_events WHERE alert_id=? ORDER BY fired_at DESC LIMIT ?",
        (alert_id, limit),
    ).fetchall()
    db.close()
    return {
        "alert_id": alert_id,
        "alert_name": alert.get("name"),
        "events": [dict(r) for r in rows],
        "total": len(rows),
    }


@router.post("/v1/alerts/evaluate")
def trigger_evaluate(authorization: str | None = Header(None)):
    """Manually trigger alert evaluation against current price_snapshots. Pro+."""
    username = require_api_key(authorization)
    _require_alerts_tier(username)
    fired = evaluate_alerts()
    return {"ok": True, "alerts_fired": fired}
