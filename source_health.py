"""Unified store scraping health — shared by dashboard and GET /v1/sources/health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from market_core import get_default_stores, STORES


def store_health_state(success_pct: float) -> str:
    """Map lifetime success rate to ok / partial / dead (dashboard semantics)."""
    pct = float(success_pct or 0)
    if pct < 30:
        return "dead"
    if pct >= 80:
        return "ok"
    return "partial"


def _fresh_24h(last_seen, age_hours_fn) -> bool:
    if last_seen is None:
        return False
    age_h = age_hours_fn(last_seen)
    return age_h is not None and age_h < 24


def build_sources_health(
    db,
    *,
    catalog_only: bool = True,
    store: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Build sources health payload from store_health + price_snapshots freshness."""
    from routers.health import _age_hours

    now = now or datetime.now(timezone.utc)

    health_rows = db.execute(
        """
        SELECT store, total_requests, total_successes,
               CASE WHEN total_requests>0
                    THEN ROUND((total_successes*100.0/total_requests)::numeric,1)
                    ELSE 0 END as success_pct,
               consecutive_failures, last_success, last_error
        FROM store_health
        ORDER BY success_pct ASC, consecutive_failures DESC
        """
    ).fetchall()

    last_seen_rows = db.execute(
        """
        SELECT store, MAX(queried_at) as last_seen
        FROM price_snapshots
        WHERE price > 0
        GROUP BY store
        """
    ).fetchall()
    last_seen_map = {r["store"]: r["last_seen"] for r in last_seen_rows}

    # coverage_7d_pct: share of a store's snapshots refreshed in the last 7 days.
    # Representativeness signal (issue #72, P1) — cross-DB parameterized.
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    coverage_rows = db.execute(
        """SELECT store,
                  COUNT(*) FILTER (WHERE queried_at >= ?) as snapshots_7d,
                  COUNT(*) as total_snapshots
           FROM price_snapshots WHERE price > 0
           GROUP BY store""",
        (cutoff_7d,),
    ).fetchall()
    coverage_map: dict[str, float] = {}
    for r in coverage_rows:
        sid = r["store"]
        total = int(r["total_snapshots"] or 0)
        coverage_map[sid] = round(int(r["snapshots_7d"] or 0) / total * 100, 1) if total > 0 else 0.0

    stores_out: list[dict] = []
    for row in health_rows:
        sid = row["store"]
        if catalog_only and sid not in get_default_stores():
            continue
        if store is not None and sid != store:
            continue

        success_pct = float(row["success_pct"] or 0)
        state = store_health_state(success_pct)
        meta = STORES.get(sid, {})
        last_seen = last_seen_map.get(sid)

        stores_out.append({
            "store": sid,
            "store_name": meta.get("name", sid),
            "country": meta.get("country", "??"),
            "success_pct": success_pct,
            "consecutive_failures": int(row["consecutive_failures"] or 0),
            "state": state,
            "last_success": row["last_success"],
            "last_error": row["last_error"],
            "last_seen": str(last_seen) if last_seen else None,
            "fresh_24h": _fresh_24h(last_seen, _age_hours),
            "coverage_7d_pct": coverage_map.get(sid, 0.0),
        })

    summary = {"ok": 0, "partial": 0, "dead": 0, "total": len(stores_out)}
    for item in stores_out:
        summary[item["state"]] += 1

    return {
        "generated_at": now.isoformat(),
        "summary": summary,
        "stores": stores_out,
    }
