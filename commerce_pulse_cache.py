"""Backend-only migration: shared commerce_pulse_cache table.

/intelligence and /embed/commerce-pulse were reading from a per-process
in-memory dict (routers/intelligence_web.py's old _pulse_cache). cli-market-api
runs on multiple Fly machines behind a load balancer, so each machine had its
own independent cache -- any visitor could land on a cold machine and block on
a live pulse computation, and every deploy wiped all of them at once.

This table is the single shared source of truth all machines read from. The
collector daemon (collect_prices.py) is the only writer, on its normal 4h
cycle; the web layer only ever reads -- so a request is never blocked on
computation once a row exists for that country+lang.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("market.commerce_pulse_cache")


def ensure_commerce_pulse_cache_table(db=None) -> bool:
    """Create commerce_pulse_cache if missing. Idempotent."""
    import market_core

    owns = db is None
    if owns:
        market_core.ensure_db_initialized()
        db = market_core.get_db()

    try:
        if market_core.USE_PG:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS commerce_pulse_cache (
                    cache_key TEXT PRIMARY KEY,
                    country TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    pulse_json TEXT NOT NULL,
                    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        else:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS commerce_pulse_cache (
                    cache_key TEXT PRIMARY KEY,
                    country TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    pulse_json TEXT NOT NULL,
                    computed_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

        if owns:
            db.commit()
        return True
    except Exception as exc:
        logger.warning("commerce_pulse_cache migration skipped: %s", exc)
        return False
    finally:
        if owns:
            db.close()


def _cache_key(country: str, lang: str) -> str:
    return f"{country.strip().upper()[:2]}:{lang.strip().lower()}"


def write_pulse_cache(db, country: str, lang: str, pulse: dict) -> None:
    """Upsert one country+lang pulse. Always overwrites -- the collector is
    the sole writer and always has the latest computation."""
    import market_core

    key = _cache_key(country, lang)
    payload = json.dumps(pulse, default=str)
    try:
        if market_core.USE_PG:
            db.execute(
                """
                INSERT INTO commerce_pulse_cache (cache_key, country, lang, pulse_json, computed_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (cache_key) DO UPDATE SET
                    pulse_json = EXCLUDED.pulse_json,
                    computed_at = EXCLUDED.computed_at
                """,
                (key, country.upper(), lang.lower(), payload),
            )
        else:
            db.execute(
                """
                INSERT INTO commerce_pulse_cache (cache_key, country, lang, pulse_json, computed_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(cache_key) DO UPDATE SET
                    pulse_json = excluded.pulse_json,
                    computed_at = excluded.computed_at
                """,
                (key, country.upper(), lang.lower(), payload),
            )
    except Exception as exc:
        logger.warning("commerce_pulse_cache write skipped for %s: %s", key, exc)


def read_pulse_cache(db, country: str, lang: str) -> dict | None:
    """Read the last computed pulse for country+lang, regardless of age --
    staleness is bounded by how often the collector refreshes it, not by a
    read-time TTL. Returns None only if no row has ever been written."""
    key = _cache_key(country, lang)
    placeholder = "%s" if _use_pg() else "?"
    row = db.execute(
        f"SELECT pulse_json, computed_at FROM commerce_pulse_cache WHERE cache_key = {placeholder}",
        (key,),
    ).fetchone()
    if not row:
        return None
    try:
        pulse = json.loads(row["pulse_json"])
    except Exception:
        logger.warning("commerce_pulse_cache row unparseable for %s", key)
        return None
    pulse["_cache_computed_at"] = str(row["computed_at"])
    return pulse


def _use_pg() -> bool:
    import market_core

    return market_core.USE_PG


SUPPORTED_COUNTRIES = ["PE", "MX", "CL", "CO", "AR", "BR"]
SUPPORTED_LANGS = ["es", "en"]


def refresh_all(db=None) -> dict:
    """Recompute and persist the pulse for every supported country+lang.
    Called from the collector daemon after each collection cycle."""
    import market_core
    from market_pulse import generate_commerce_pulse
    from routers.dashboard import get_cached_dashboard_data

    owns = db is None
    if owns:
        market_core.ensure_db_initialized()
        db = market_core.get_db()

    ensure_commerce_pulse_cache_table(db)

    written = 0
    errors = 0
    dashboard = get_cached_dashboard_data()
    for country in SUPPORTED_COUNTRIES:
        for lang in SUPPORTED_LANGS:
            try:
                pulse = generate_commerce_pulse(
                    country=country, days=7, lang=lang, dashboard=dashboard
                )
                pulse.pop("brief", None)
                write_pulse_cache(db, country, lang, pulse)
                written += 1
            except Exception as exc:
                errors += 1
                logger.warning("pulse refresh failed for %s:%s: %s", country, lang, exc)

    if owns:
        db.commit()
        db.close()

    return {"written": written, "errors": errors}
