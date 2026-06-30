"""Health checks, root, and catalog endpoints (lines / stores / countries).

Endpoints:
  GET /                  Service banner + counts
  GET /health            Liveness check
  GET /health/collector  Collector freshness (last run, age, store coverage)
  GET /v1/sources/health Per-store scraping health (success rate + freshness)
  GET /health/stats      Live moat KPIs + golden linkage % + sources summary
  GET /lines             Catalog of business lines with their stores
  GET /stores            Catalog of retailers (filterable by country/line)
  GET /countries         Catalog of countries with store lists
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from market_core import STORES, LINES, COUNTRIES, get_db
from server_deps import check_rate_limit, get_db_dep
from source_health import build_sources_health

# Cache pg_error probe so /health/db never blocks longer than _PG_PROBE_TTL seconds
# regardless of how often the endpoint is polled.
_pg_probe_cache: tuple[float, str | None] | None = None  # (timestamp, error_or_none)
_PG_PROBE_TTL = 30.0  # seconds

logger = logging.getLogger("market.server").getChild("health")

router = APIRouter(tags=["health"])


def _age_hours(timestamp_str: str | datetime | None) -> float | None:
    """Parse a SQLite/Postgres timestamp and return hours since.

    Accepts ISO strings, SQLite naive strings, or datetime objects from asyncpg/psycopg.
    Returns None if parsing fails. UTC is assumed for naive values.
    """
    if timestamp_str is None:
        return None
    if isinstance(timestamp_str, datetime):
        dt = timestamp_str
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if not timestamp_str:
        return None
    try:
        s = timestamp_str.replace("Z", "+00:00")
        # SQLite's datetime('now') uses space as separator; ISO uses T.
        # datetime.fromisoformat handles both since Python 3.11; for 3.10
        # we replace space → T defensively.
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
        # Naive timestamps from SQLite are UTC by convention here.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception as e:
        logger.warning("Could not parse timestamp %r: %s", timestamp_str, e)
        return None


def derive_collector_status(
    *,
    finished_at: str | datetime | None,
    prices_collected: int | None,
    moat_age_h: float | None = None,
) -> tuple[str, float | None]:
    """Map last collector run + moat freshness to a dashboard/health status.

    ``ok`` — finished recently and ingested prices.
    ``empty`` — finished recently but saved zero prices (runner alive, ingest broken).
    ``stale`` — run or moat data older than SLA (8h moat / 12h run).
    ``dead`` — run or moat very old (24h+).
    """
    if finished_at is None:
        return "running", None
    age_h = _age_hours(finished_at)
    if age_h is None:
        return "unknown", None
    collected = int(prices_collected or 0)
    if age_h > 24 or (moat_age_h is not None and moat_age_h >= 24):
        return "dead", age_h
    if age_h > 12 or (moat_age_h is not None and moat_age_h >= 8):
        return "stale", age_h
    if collected > 0:
        return "ok", age_h
    return "empty", age_h


@router.get("/health")
def health():
    return {"status": "healthy"}


@router.get("/health/db")
def health_db():
    """Database backend diagnostic — confirms PG vs SQLite."""
    global _pg_probe_cache
    import market_core
    try:
        market_core.recover_pg_if_needed()
    except Exception:
        pass
    from market_core import USE_PG, DATABASE_URL, DB_FILE
    try:
        db = get_db()
    except Exception as e:
        return {"backend": "error", "detail": f"DB connection failed: {e}"}

    # USE_PG can be True while recover_pg_if_needed() races; the live connection
    # is the source of truth for which SQL dialect to run.
    using_pg = bool(getattr(db, "_pg", USE_PG))

    # Probe PG connectivity only when fallen back to SQLite, and cache the
    # result so repeated health polls never each block for connect_timeout.
    pg_error: str | None = None
    if DATABASE_URL and not using_pg:
        now = time.monotonic()
        if _pg_probe_cache is None or (now - _pg_probe_cache[0]) > _PG_PROBE_TTL:
            try:
                import psycopg2
                psycopg2.connect(DATABASE_URL, connect_timeout=2)
                _pg_probe_cache = (now, None)
            except Exception as e:
                _pg_probe_cache = (now, str(e)[:200])
        pg_error = _pg_probe_cache[1] if _pg_probe_cache else None

    try:
        db_type = "postgresql" if using_pg else "sqlite"
        # Fast approximate row count — O(log n) for SQLite (MAX(rowid)), and
        # a stats-table lookup for PG — avoids a full COUNT(*) sequential scan.
        if using_pg:
            snap_row = db.execute(
                """
                SELECT reltuples::bigint AS n
                FROM pg_class WHERE relname = 'price_snapshots'
                """
            ).fetchone()
            snapshots = int(snap_row["n"]) if snap_row and snap_row["n"] else 0
        else:
            snap_row = db.execute(
                "SELECT MAX(rowid) AS n FROM price_snapshots"
            ).fetchone()
            snapshots = int(snap_row["n"]) if snap_row and snap_row["n"] else 0

        if not using_pg:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        else:
            tables = db.execute(
                "SELECT tablename as name FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename"
            ).fetchall()

        upsert_ready = None
        if using_pg:
            try:
                upsert_ready = bool(db.execute(
                    """
                    SELECT 1 FROM pg_indexes
                    WHERE tablename = 'price_snapshots'
                      AND indexdef ILIKE '%UNIQUE%'
                      AND indexdef ILIKE '%product_id%'
                      AND indexdef ILIKE '%store%'
                    LIMIT 1
                    """
                ).fetchone())
            except Exception:
                upsert_ready = False

        return {
            "backend": db_type,
            "database_url_set": bool(DATABASE_URL),
            "db_file": str(DB_FILE) if not USE_PG else None,
            "snapshots": snapshots,
            "price_snapshots_upsert_ready": upsert_ready,
            "tables": [t["name"] for t in tables],
            "pg_error": pg_error,
        }
    except Exception as e:
        return {"backend": "error", "detail": str(e)}


@router.get("/health/collector")
def health_collector(db = Depends(get_db_dep)):
    """Collector health: last run, staleness, store coverage."""
    try:
        try:
            last = db.execute(
                "SELECT started_at, finished_at, stores_attempted, stores_succeeded, "
                "prices_collected, stores_with_yield "
                "FROM collector_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except Exception:
            last = db.execute(
                "SELECT started_at, finished_at, stores_attempted, stores_succeeded, prices_collected "
                "FROM collector_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        total_runs = db.execute("SELECT COUNT(*) as n FROM collector_runs").fetchone()["n"]
        active_stores = db.execute(
            "SELECT COUNT(DISTINCT store) as n FROM price_snapshots WHERE price > 0"
        ).fetchone()["n"]
    except Exception:
        return {"status": "unknown", "error": "Database not initialized"}

    if not last:
        return {"status": "unknown", "message": "No collector runs yet", "runs_total": 0}

    finished = last["finished_at"]
    in_progress = finished is None
    if finished:
        status, age_h = derive_collector_status(
            finished_at=finished,
            prices_collected=last["prices_collected"],
        )
    else:
        status = "running"
        age_h = None

    return {
        "status": status,
        "in_progress": in_progress,
        "last_run": last["started_at"],
        "last_finished": finished,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "stores_attempted": last["stores_attempted"],
        "stores_succeeded": last["stores_succeeded"] if not in_progress else None,
        "stores_responded": last["stores_succeeded"] if not in_progress else None,
        "stores_with_yield": (
            last["stores_with_yield"]
            if not in_progress and "stores_with_yield" in last.keys()
            else None
        ),
        "prices_collected": last["prices_collected"] if not in_progress else None,
        "stores_active": active_stores or 0,
        "stores_total": len(STORES),
        "runs_total": total_runs,
    }


@router.get("/v1/capabilities")
def commerce_capabilities():
    """Public matrix: what checkout does (CLI Market internal payment) vs retailer fulfillment."""
    from market_core.commerce_capabilities import get_commerce_capabilities

    return get_commerce_capabilities()


@router.get("/v1/sources/health")
def sources_health(
    store: str | None = None,
    catalog_only: bool = True,
    db = Depends(get_db_dep),
):
    """Per-store scraping health: success rate, failures, and snapshot freshness."""
    return build_sources_health(db, catalog_only=catalog_only, store=store)


@router.get("/health/stores")
def health_stores(country: str | None = None, db = Depends(get_db_dep)):
    """Per-store freshness dashboard — ops view of collector coverage.

    For each store in the catalog returns:
      - status: ok | stale | dead | never
      - hours_since_snapshot: hours since last recorded price
      - snapshots_7d: prices collected in the last 7 days
      - consecutive_failures: scraper error streak (from store_health)

    Status thresholds: ok <8h · stale 8–24h · dead >24h · never = no data.
    No auth required — this is a public ops/monitoring endpoint.
    """
    from datetime import timezone as _tz

    # Last snapshot per store
    last_seen_rows = db.execute(
        "SELECT store, MAX(queried_at) as last_seen, COUNT(*) as total "
        "FROM price_snapshots WHERE price > 0 GROUP BY store"
    ).fetchall()
    last_seen: dict[str, tuple[str | None, int]] = {
        r["store"]: (r["last_seen"], int(r["total"] or 0))
        for r in last_seen_rows
    }

    # Snapshots in last 7 days per store
    cutoff = (datetime.now(_tz.utc).replace(tzinfo=None) - __import__("datetime").timedelta(days=7)).isoformat()
    snap7_rows = db.execute(
        "SELECT store, COUNT(*) as n FROM price_snapshots "
        "WHERE price > 0 AND queried_at >= ? GROUP BY store",
        (cutoff,),
    ).fetchall()
    snap7: dict[str, int] = {r["store"]: int(r["n"] or 0) for r in snap7_rows}

    # Consecutive failures from store_health
    try:
        failures_rows = db.execute(
            "SELECT store, consecutive_failures, last_error FROM store_health"
        ).fetchall()
        failures: dict[str, dict] = {
            r["store"]: {"consecutive_failures": int(r["consecutive_failures"] or 0), "last_error": r["last_error"]}
            for r in failures_rows
        }
    except Exception:
        failures = {}

    stores_out: list[dict] = []
    for store_id, meta in STORES.items():
        if meta.get("disabled"):
            continue
        if country and meta["country"] != country.upper():
            continue

        seen_at, total_snaps = last_seen.get(store_id, (None, 0))
        age_h = _age_hours(seen_at)

        if seen_at is None:
            status = "never"
        elif age_h is None:
            status = "unknown"
        elif age_h < 8:
            status = "ok"
        elif age_h < 24:
            status = "stale"
        else:
            status = "dead"

        fail_data = failures.get(store_id, {})
        stores_out.append({
            "store": store_id,
            "name": meta.get("name", store_id),
            "country": meta.get("country", "??"),
            "line": meta.get("line", ""),
            "status": status,
            "hours_since_snapshot": round(age_h, 1) if age_h is not None else None,
            "last_snapshot_at": seen_at,
            "snapshots_total": total_snaps,
            "snapshots_7d": snap7.get(store_id, 0),
            "consecutive_failures": fail_data.get("consecutive_failures", 0),
            "last_error": fail_data.get("last_error"),
        })

    # Sort: dead first, then stale, then never, then ok — worst visible first
    _order = {"dead": 0, "stale": 1, "never": 2, "ok": 3, "unknown": 4}
    stores_out.sort(key=lambda s: (_order.get(s["status"], 9), s["country"], s["name"]))

    summary = {"ok": 0, "stale": 0, "dead": 0, "never": 0, "total": len(stores_out)}
    for s in stores_out:
        k = s["status"]
        if k in summary:
            summary[k] += 1

    return {
        "summary": summary,
        "stores": stores_out,
        "thresholds": {"ok_h": 8, "stale_h": 24},
    }


@router.get("/health/stats")
def health_stats():
    """Live KPIs for landing and ops — moat freshness, linkage %, scraping summary."""
    from market_core.health_stats import build_health_stats

    registry_size = None
    try:
        from index_gate import registry_size as _registry_size
        registry_size = _registry_size()
    except Exception:
        pass

    try:
        db = get_db()
    except Exception as e:
        return {"error": f"DB connection failed: {e}", "status": "degraded"}

    try:
        return build_health_stats(db, registry_size=registry_size)
    except Exception as e:
        logger.error("health_stats build failed: %s", e)
        return {"error": str(e), "status": "degraded"}


@router.get("/")
def root(request: Request):
    try:
        check_rate_limit(request.client.host if request.client else "unknown")
    except Exception as e:
        logger.warning("Rate limit check failed: %s", e)
    return {
        "name": "CLI Market",
        "status": "running",
        "stores": len(STORES),
        "lines": len(LINES),
        "countries": len(COUNTRIES),
        "docs": "/docs",
    }


@router.get("/lines")
def list_lines():
    result: dict[str, dict] = {}
    for line_id, line_meta in LINES.items():
        line_stores: dict[str, dict] = {}
        for sk, sv in STORES.items():
            if sv["line"] == line_id:
                line_stores[sk] = {
                    "name": sv["name"],
                    "country": sv["country"],
                    "currency": sv["currency"],
                    "base": sv.get("base", ""),
                    "emoji": sv.get("emoji", ""),
                }
        result[line_id] = {
            "name": line_meta["name"],
            "emoji": line_meta["emoji"],
            "description": line_meta["description"],
            "stores": line_stores,
            "total_stores": len(line_stores),
        }
    return {"lines": result, "total": len(result)}


@router.get("/stores")
def list_stores(country: str | None = None, line: str | None = None):
    result = {}
    for key, s in STORES.items():
        if country and s["country"] != country.upper():
            continue
        if line and s["line"] != line:
            continue
        result[key] = {
            "name": s["name"],
            "country": s["country"],
            "currency": s["currency"],
            "line": s["line"],
            "line_name": LINES.get(s["line"], {}).get("name", s["line"]),
            "base": s["base"],
        }
    return {"stores": result, "total": len(result)}


@router.get("/countries")
def list_countries():
    return {
        "countries": {
            code: {"name": c["name"], "stores": c["stores"], "count": len(c["stores"])}
            for code, c in COUNTRIES.items()
        }
    }
