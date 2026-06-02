"""Health checks, root, and catalog endpoints (lines / stores / countries).

Endpoints:
  GET /                  Service banner + counts
  GET /health            Liveness check
  GET /health/collector  Collector freshness (last run, age, store coverage)
  GET /v1/sources/health Per-store scraping health (success rate + freshness)
  GET /lines             Catalog of business lines with their stores
  GET /stores            Catalog of retailers (filterable by country/line)
  GET /countries         Catalog of countries with store lists
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from market_core import STORES, LINES, COUNTRIES, get_db
from server_deps import check_rate_limit
from source_health import build_sources_health

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
    import market_core
    # Hitting this endpoint also nudges a Postgres recovery attempt when we
    # are in SQLite fallback mode (throttled internally).
    try:
        market_core.recover_pg_if_needed()
    except Exception:
        pass
    from market_core import USE_PG, DATABASE_URL, DB_FILE
    pg_error = None
    if DATABASE_URL and not USE_PG:
        # PG was attempted but fell back — try to get the connection error
        try:
            import psycopg2
            psycopg2.connect(DATABASE_URL, connect_timeout=5)
        except Exception as e:
            pg_error = str(e)[:200]
    db = get_db()
    try:
        db_type = "postgresql" if USE_PG else "sqlite"
        snapshots = db.execute("SELECT COUNT(*) as n FROM price_snapshots").fetchone()["n"]
        if not USE_PG:
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        else:
            tables = db.execute(
                "SELECT tablename as name FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename"
            ).fetchall()
        db.close()
        upsert_ready = None
        if USE_PG:
            try:
                chk = get_db()
                upsert_ready = bool(chk.execute(
                    """
                    SELECT 1 FROM pg_indexes
                    WHERE tablename = 'price_snapshots'
                      AND indexdef ILIKE '%UNIQUE%'
                      AND indexdef ILIKE '%product_id%'
                      AND indexdef ILIKE '%store%'
                    LIMIT 1
                    """
                ).fetchone())
                chk.close()
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
def health_collector():
    """Collector health: last run, staleness, store coverage."""
    try:
        db = get_db()
        last = db.execute(
            "SELECT started_at, finished_at, stores_attempted, stores_succeeded, prices_collected "
            "FROM collector_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        total_runs = db.execute("SELECT COUNT(*) as n FROM collector_runs").fetchone()["n"]
        active_stores = db.execute(
            "SELECT COUNT(DISTINCT store) as n FROM price_snapshots WHERE price > 0"
        ).fetchone()["n"]
        db.close()
    except Exception:
        return {"status": "unknown", "error": "Database not initialized"}

    if not last:
        return {"status": "unknown", "message": "No collector runs yet", "runs_total": 0}

    finished = last["finished_at"]
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
        "last_run": last["started_at"],
        "last_finished": finished,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "stores_attempted": last["stores_attempted"],
        "stores_succeeded": last["stores_succeeded"],
        "prices_collected": last["prices_collected"],
        "stores_active": active_stores or 0,
        "stores_total": len(STORES),
        "runs_total": total_runs,
    }


@router.get("/v1/sources/health")
def sources_health(
    store: str | None = None,
    catalog_only: bool = True,
):
    """Per-store scraping health: success rate, failures, and snapshot freshness."""
    db = get_db()
    try:
        return build_sources_health(db, catalog_only=catalog_only, store=store)
    finally:
        db.close()


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
            "line_name": LINES[s["line"]]["name"],
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
