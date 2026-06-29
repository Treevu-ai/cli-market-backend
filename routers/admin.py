"""Operational / admin endpoints — used by the dashboard and ops scripts.

Endpoints:
  GET  /admin/debug-fetch     Test fetch_store + product_from_json for one store/query
  POST /admin/collect         Trigger a price collection run synchronously
  POST /v1/admin/scan-stores  Probe known retailer domains for liveness
  GET  /admin/contacts        List captured lead emails (plan, profile, signup date)
  POST /v1/admin/set-tier     Set a user's subscription tier (free|pro|enterprise)
  POST /admin/cron/funnel-digest  Post evening funnel digest to Slack (#funnel-cli-market)
  POST /admin/cron/command-control  Post morning founder panel (#command-control-cli-market)
  POST /admin/cron/adoption-index  Persist Adoption Index snapshot (nightly cron)
  POST /admin/cron/indicators-refresh  Refresh moat indicators (internal + macro + Phase 2)
  POST /admin/cron/commerce-pulse  Generate Agentic Commerce Pulse reports (weekly)

Protected with MARKET_API_TOKEN (Bearer). Set on Railway before exposing publicly.
"""

from __future__ import annotations

import io
import csv
import logging
import time

import httpx
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from market_core import STORES, db_get_subscription, db_get_users, db_set_subscription, fetch_store, product_from_json, get_db
from market_billing import TIERS
from server_deps import get_db_dep, require_admin

logger = logging.getLogger(__name__)

_VALID_TIERS = frozenset(TIERS.keys())

router = APIRouter(prefix="", tags=["admin"])


@router.post("/v1/admin/set-tier")
def admin_set_tier(
    body: dict = Body(...),
    authorization: str | None = Header(None),
):
    """Set a user's subscription tier. Admin-only (MARKET_API_TOKEN)."""
    require_admin(authorization)
    username = (body.get("username") or "").strip()
    tier = (body.get("tier") or "").strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    if tier not in _VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"tier must be one of {sorted(_VALID_TIERS)}")
    if username not in db_get_users():
        raise HTTPException(status_code=404, detail=f"user not found: {username}")
    db_set_subscription(username, tier)
    try:
        from market_funnel import record_funnel_event

        if tier == "pro":
            record_funnel_event(
                "activated",
                username=username,
                meta={"source": "ops_manual", "reason": "admin_set_tier"},
                dedupe=True,
            )
    except Exception:
        pass
    logger.info("audit admin_set_tier username=%s tier=%s", username, tier)
    return {"username": username, "subscription": db_get_subscription(username)}


@router.get("/admin/contacts")
def admin_contacts(
    plan: str | None = Query(default=None, description="Filter by plan: free, pro, enterprise, newsletter"),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    fmt: str = Query(default="json", alias="format", description="json or csv"),
    authorization: str | None = Header(None),
    db = Depends(get_db_dep),
):
    """List lead emails captured via the landing contact forms.

    ?plan=free   — only Free tier signups
    ?plan=pro    — Pro requests
    ?format=csv  — download as CSV
    """
    require_admin(authorization)
    if plan:
        rows = db.execute(
            """
            SELECT username AS email, first_name AS name, last_message, created_at
            FROM contacts
            WHERE last_message LIKE ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (f"[{plan}%", limit, offset),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT username AS email, first_name AS name, last_message, created_at
            FROM contacts
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    contacts = []
    for r in rows:
        msg = r["last_message"] or ""
        # Extract profile tag: "[free/dev] ..." → "dev"
        profile = ""
        if msg.startswith("[") and "/" in msg.split("]")[0]:
            profile = msg.split("/", 1)[1].split("]")[0]
        contacts.append({
            "email": r["email"],
            "name": r["name"] if r["name"] not in ("free", "pro", "enterprise", "newsletter", "") else "",
            "profile": profile,
            "message": msg,
            "signed_up": r["created_at"],
        })

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["email", "name", "profile", "signed_up", "message"])
        writer.writeheader()
        writer.writerows(contacts)
        buf.seek(0)
        filename = f"contacts-{plan or 'all'}.csv"
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return {"total": len(contacts), "plan": plan or "all", "contacts": contacts}


@router.get("/admin/debug-fetch")
async def debug_fetch(
    store: str = "wong",
    query: str = "leche",
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    """Smoke test the data pipeline for one store: raw fetch → normalize."""
    raw = await fetch_store(store, query, page=1, limit=3)
    products = [product_from_json(p, store) for p in raw[:3]]
    return {"store": store, "query": query, "results": len(raw), "products": products}


@router.post("/admin/collect")
async def admin_collect(
    stores: int = 0,
    queries: int = 0,
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    """Trigger a price collection run directly (synchronous).

    Useful for manual smoke testing on Render after deploys. Use ?stores=2&queries=2
    for a quick sanity check; default runs the full catalog.
    """
    from collect_prices import (
        build_query_list,
        _get_feedback_db,
        run_collection,
    )
    from market_core import ensure_db_initialized

    ensure_db_initialized()

    sl = list(STORES.keys())
    if stores:
        sl = sl[:stores]

    db = _get_feedback_db()
    ql = build_query_list(db=db, cycle=0)
    if queries:
        ql = ql[:queries]

    t0 = time.monotonic()
    result = await run_collection(sl, ql)
    return {
        "status": "ok",
        "elapsed_s": round(time.monotonic() - t0, 1),
        "stores_attempted": result["stores_attempted"],
        "stores_succeeded": result["stores_succeeded"],
        "prices_collected": result["prices_collected"],
    }


@router.post("/admin/collect-catalog")
async def admin_collect_catalog(
    store: str = Query(..., description="Store key, e.g. nunaorganica_pe"),
    authorization: str | None = Header(None),
):
    """Force full catalog upsert for one WooCommerce/VTEX store (bypasses 60-min interval)."""
    require_admin(authorization)
    from collect_prices import force_catalog_stores
    from market_core import STORES

    if store not in STORES:
        return {"error": f"unknown store: {store}"}
    t0 = time.monotonic()
    result = await force_catalog_stores([store])
    return {
        "status": "ok",
        "store": store,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "prices_collected": result["prices_collected"],
        "per_store": result["stores"],
    }


@router.post("/v1/admin/scan-stores")
async def admin_scan_stores(
    body: dict = Body(default_factory=dict),
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    """Probe each known retailer with a tiny VTEX catalog query."""
    line_filter = body.get("line")
    candidates: list[dict] = []
    for sk, sv in STORES.items():
        if line_filter and sv.get("line") != line_filter:
            continue
        base = sv["base"]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                r = await client.get(
                    f"{base}/api/catalog_system/pub/products/search/test?_from=0&_to=1"
                )
                candidates.append(
                    {
                        "store": sk,
                        "name": sv["name"],
                        "status": r.status_code,
                        "ok": r.status_code in (200, 206),
                    }
                )
        except Exception as e:
            candidates.append(
                {
                    "store": sk,
                    "name": sv["name"],
                    "status": 0,
                    "ok": False,
                    "error": str(e)[:100],
                }
            )
    ok = [c for c in candidates if c["ok"]]
    return {"scanned": len(candidates), "working": len(ok), "candidates": candidates}


@router.post("/admin/cron/adoption-index")
def admin_cron_adoption_index(
    authorization: str | None = Header(None),
    days: int = 30,
    github: bool = True,
):
    """Persist Adoption Index snapshot (nightly cron)."""
    require_admin(authorization)
    days = max(1, min(days, 90))

    from market_adoption_index import compute_adoption_index, persist_snapshot

    payload = compute_adoption_index(days=days, include_github=github)
    saved = persist_snapshot(payload)
    return {"ok": True, "score": payload["score"], "grade": payload["grade"], "snapshot": saved}


@router.post("/admin/cron/indicators-refresh")
def admin_cron_indicators_refresh(
    authorization: str | None = Header(None),
    country: str | None = None,
):
    """Refresh moat indicators across countries (nightly cron)."""
    require_admin(authorization)

    from market_indicators import refresh_after_collection, refresh_indicators

    if country:
        cc = country.upper()
        result = refresh_indicators(country=cc, line=None)
        return {"ok": True, "country": cc, **result}

    summary = refresh_after_collection()
    return {"ok": True, **summary}


@router.post("/admin/cron/funnel-digest")
def admin_cron_funnel_digest(
    authorization: str | None = Header(None),
    hours: int = 24,
):
    """Post adoption funnel digest to #funnel-cli-market (evening cron)."""
    require_admin(authorization)
    hours = max(1, min(hours, 168))

    import sys
    from pathlib import Path

    ops_dir = Path(__file__).resolve().parent.parent / "ops"
    if str(ops_dir) not in sys.path:
        sys.path.insert(0, str(ops_dir))
    from billing_slack import format_funnel_digest_message, notify_funnel_digest

    text = format_funnel_digest_message(hours=hours)
    if not notify_funnel_digest(hours=hours):
        raise HTTPException(
            status_code=503,
            detail="Slack funnel channel not configured or delivery failed",
        )
    return {"ok": True, "hours": hours, "posted": True, "preview": text[:800]}


@router.post("/admin/cron/command-control")
def admin_cron_command_control(
    authorization: str | None = Header(None),
    full: bool = False,
):
    """Post founder Command & Control panel (morning cron)."""
    require_admin(authorization)

    import sys
    from pathlib import Path

    ops_dir = Path(__file__).resolve().parent.parent / "ops"
    if str(ops_dir) not in sys.path:
        sys.path.insert(0, str(ops_dir))
    from command_control_daily import publish_command_control

    try:
        return publish_command_control(remote=True, brief=not full, save=True)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Slack command-control not configured: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("command-control cron failed")
        raise HTTPException(status_code=500, detail=str(exc)[:200]) from exc


@router.post("/admin/cron/commerce-pulse")
def admin_cron_commerce_pulse(
    authorization: str | None = Header(None),
    body: dict = Body(default_factory=dict),
):
    """Generate Agentic Commerce Pulse reports (weekly cron / ops)."""
    require_admin(authorization)

    from pathlib import Path

    from market_pulse import generate_commerce_pulse

    from routers.dashboard import get_cached_dashboard_data

    countries = body.get("countries") or ["PE", "MX", "CL", "CO", "AR", "BR"]
    days = int(body.get("days") or 7)
    lang = body.get("lang") or "es"
    save = body.get("save", True)
    llm = bool(body.get("llm", False))
    dashboard = get_cached_dashboard_data()

    reports: list[dict] = []
    written: list[str] = []

    for raw_cc in countries:
        cc = str(raw_cc).strip().upper()[:2]
        if len(cc) != 2:
            continue
        pulse = generate_commerce_pulse(
            country=cc,
            days=days,
            lang=lang,
            dashboard=dashboard,
            llm=llm,
        )
        item = {
            "country": cc,
            "week": pulse.get("week"),
            "publishable": pulse.get("publishable"),
            "headline": pulse.get("headline"),
            "highlights": len(pulse.get("executive_highlights") or []),
        }
        reports.append(item)
        if save:
            import sys

            ops_dir = Path(__file__).resolve().parent.parent / "ops"
            if str(ops_dir) not in sys.path:
                sys.path.insert(0, str(ops_dir))
            from content_paths import metrics_dir

            metrics_dir().mkdir(parents=True, exist_ok=True)
            week = pulse.get("week", "unknown")
            path = metrics_dir() / f"commerce-pulse-{cc}-{week}.md"
            path.write_text(pulse.get("markdown", ""), encoding="utf-8")
            written.append(str(path.name))

    return {
        "ok": True,
        "reports": reports,
        "written": written,
        "llm": llm,
    }

