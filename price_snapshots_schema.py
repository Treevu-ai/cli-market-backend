"""Backend migrations for price_snapshots beyond cli-market-core DDL."""

from __future__ import annotations

import logging

logger = logging.getLogger("market.price_snapshots_schema")


def price_snapshots_has_canonical_id(db) -> bool:
    import market_core

    try:
        if market_core.USE_PG:
            row = db.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'price_snapshots' AND column_name = 'canonical_product_id'
                LIMIT 1
                """
            ).fetchone()
            return bool(row)
        rows = db.execute("PRAGMA table_info(price_snapshots)").fetchall()
        return any(r["name"] == "canonical_product_id" for r in rows)
    except Exception:
        return False


def ensure_canonical_product_id_column(db=None) -> bool:
    """Add canonical_product_id (Golden Record UPID) to price_snapshots. Idempotent."""
    import market_core

    owns = db is None
    if owns:
        market_core.ensure_db_initialized()
        db = market_core.get_db()

    try:
        if price_snapshots_has_canonical_id(db):
            return True

        if market_core.USE_PG:
            db.execute(
                "ALTER TABLE price_snapshots ADD COLUMN IF NOT EXISTS canonical_product_id TEXT"
            )
        else:
            try:
                db.execute("ALTER TABLE price_snapshots ADD COLUMN canonical_product_id TEXT")
            except Exception:
                pass

        for idx_sql in [
            (
                "CREATE INDEX IF NOT EXISTS idx_ps_canonical_product "
                "ON price_snapshots(canonical_product_id) "
                "WHERE canonical_product_id IS NOT NULL AND canonical_product_id != ''"
            ),
            "CREATE INDEX IF NOT EXISTS idx_ps_unlinked ON price_snapshots(queried_at DESC) "
            "WHERE (canonical_product_id IS NULL OR canonical_product_id = '')",
        ]:
            try:
                db.execute(idx_sql)
            except Exception as exc:
                logger.warning("canonical_product_id index skipped: %s", exc)

        if owns:
            db.commit()
        logger.info("Migrated price_snapshots: added canonical_product_id")
        return True
    except Exception as exc:
        logger.warning("canonical_product_id migration skipped: %s", exc)
        return False
    finally:
        if owns:
            db.close()