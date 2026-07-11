"""Backend-only migration: stock_history time series (beyond cli-market-core DDL).

Unlike price_history (cli-market-core, only appends on a price change),
stock_history is meant to be appended on EVERY collector run for every
product, so a future "% of time in stock" aggregate isn't biased by
sparse, change-triggered sampling. See append_stock_history() below.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("market.stock_history_schema")


def ensure_stock_history_table(db=None) -> bool:
    """Create stock_history if missing. Idempotent."""
    import market_core

    owns = db is None
    if owns:
        market_core.ensure_db_initialized()
        db = market_core.get_db()

    try:
        if market_core.USE_PG:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_history (
                    id SERIAL PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    store TEXT NOT NULL,
                    in_stock INTEGER NOT NULL,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        else:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id TEXT NOT NULL,
                    store TEXT NOT NULL,
                    in_stock INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sh_product_store "
            "ON stock_history(product_id, store, recorded_at DESC)"
        )

        if owns:
            db.commit()
        return True
    except Exception as exc:
        logger.warning("stock_history migration skipped: %s", exc)
        return False
    finally:
        if owns:
            db.close()


def append_stock_history(db, product_id: str, store: str, in_stock: bool) -> None:
    """Append one sample. Always inserts (no dedup/change-detection) -- a
    steady per-run sample is required to later compute time-in-stock
    ratios; skipping unchanged runs (like append_price_history does for
    price) would bias that toward stock CHANGES rather than stock STATE.
    """
    try:
        db.execute(
            "INSERT INTO stock_history (product_id, store, in_stock) VALUES (?, ?, ?)",
            (product_id, store, 1 if in_stock else 0),
        )
    except Exception as exc:
        logger.debug("stock_history append skipped: %s", exc)
