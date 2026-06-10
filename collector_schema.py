"""Idempotent collector_runs schema extensions."""

from __future__ import annotations


def ensure_collector_runs_columns(db=None) -> bool:
    """Add stores_with_yield to collector_runs (PG + SQLite)."""
    if db is None:
        from market_db import get_db

        db = get_db()
    try:
        from market_core import USE_PG

        if USE_PG:
            db.execute(
                "ALTER TABLE collector_runs ADD COLUMN IF NOT EXISTS stores_with_yield INT DEFAULT 0"
            )
        else:
            cols = {
                row["name"]
                for row in db.execute("PRAGMA table_info(collector_runs)").fetchall()
            }
            if "stores_with_yield" not in cols:
                db.execute(
                    "ALTER TABLE collector_runs ADD COLUMN stores_with_yield INT DEFAULT 0"
                )
        db.commit()
        return True
    except Exception:
        return False
