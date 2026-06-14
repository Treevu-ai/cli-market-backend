"""
Index Gate — Semantic enrichment bridge for CLI Market Backend.

Delegates to cli-market-index IndexService with persistent Golden Records.
Postgres is used automatically when DATABASE_URL is set.

Usage (in any router):
    from index_gate import enrich_product, enrich_list
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from persistence.factory import create_store
    from services.index_service import IndexService
    _INDEX_AVAILABLE = True
except ImportError:
    create_store = None  # type: ignore[assignment]
    IndexService = None  # type: ignore[assignment,misc]
    _INDEX_AVAILABLE = False
    logging.getLogger("market.index_gate").warning(
        "cli-market-index not installed — index enrichment disabled"
    )

from price_snapshots_schema import ensure_canonical_product_id_column

logger = logging.getLogger("market.index_gate")

_service: Optional[IndexService] = None


def _bootstrap_index_env() -> None:
    """Wire index persistence to backend database paths when not explicitly set."""
    if not os.getenv("INDEX_DATABASE_URL", "").strip():
        db_url = os.getenv("DATABASE_URL", "").strip()
        if db_url.startswith(("postgres://", "postgresql://")):
            os.environ.setdefault("INDEX_DATABASE_URL", db_url)

    if not os.getenv("INDEX_DATA_DIR") and os.getenv("MARKET_DATA_DIR"):
        os.environ.setdefault(
            "INDEX_DATA_DIR",
            str(Path(os.environ["MARKET_DATA_DIR"]).expanduser() / "index"),
        )


def _get_service() -> "IndexService":
    global _service
    if not _INDEX_AVAILABLE:
        raise RuntimeError("cli-market-index is not installed — index enrichment unavailable")
    if _service is None:
        _bootstrap_index_env()
        if os.getenv("INDEX_PERSISTENCE", "1").strip().lower() in ("0", "false", "no"):
            _service = IndexService()
        else:
            _service = IndexService(store=create_store())
        logger.info(
            "Index gate ready (persistence=%s, registry_size=%d)",
            "on" if _service._store else "off",
            _service.size,
        )
    return _service


def registry_size() -> int:
    """Current Golden Record count in the index store."""
    try:
        return _get_service().size
    except Exception:
        return 0


def _brand_slug(product: Any) -> str:
    brand = product.brand
    if isinstance(brand, str):
        return brand
    return getattr(brand, "slug", str(brand))


def _product_payload(product: Any, *, match_type: str, confidence: float) -> Dict[str, Any]:
    measurement = None
    if product.measurement:
        measurement = {
            "value": product.measurement.value,
            "unit": product.measurement.unit,
            "display": product.measurement.display,
        }
    return {
        "object": "product",
        "id": product.id,
        "name": product.name,
        "brand": _brand_slug(product),
        "measurement": measurement,
        "match_type": match_type,
        "confidence": confidence,
    }


def index_resolve(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a retailer snapshot to a Golden Record. Never raises."""
    try:
        svc = _get_service()
        result = svc.resolve_snapshot(
            {
                "store": snapshot.get("store", ""),
                "sku": snapshot.get("sku") or snapshot.get("product_id", ""),
                "name": snapshot.get("name", ""),
                "brand": snapshot.get("brand", ""),
                "price": float(snapshot.get("price", 0) or 0),
                "currency": snapshot.get("currency", "USD"),
                "url": snapshot.get("url", ""),
            }
        )
        if not result.product:
            return {"resolved": False, "product": None, "match_type": result.match_type}
        return {
            "resolved": True,
            "product": _product_payload(
                result.product,
                match_type=result.match_type,
                confidence=result.confidence,
            ),
            "registry_size": svc.size,
        }
    except Exception as exc:
        logger.warning("index_resolve failed: %s", exc)
        return {"resolved": False, "error": str(exc)}


def index_lookup(product_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a Golden Record by prod_* id."""
    try:
        product = _get_service().get_product(product_id)
        if not product:
            return None
        return _product_payload(product, match_type="lookup", confidence=1.0)
    except Exception as exc:
        logger.warning("index_lookup failed for %s: %s", product_id, exc)
        return None


def index_stats() -> Dict[str, Any]:
    """Registry size plus price_snapshots linkage metrics."""
    import market_core

    stats: Dict[str, Any] = {
        "registry_size": registry_size(),
        "snapshots_linked": 0,
        "golden_records_distinct": 0,
        "unlinked_snapshots": 0,
        "linkage_pct": 0.0,
    }
    db = market_core.get_db()
    try:
        ensure_canonical_product_id_column(db)
        total = db.execute(
            "SELECT COUNT(*) as n FROM price_snapshots WHERE price > 0"
        ).fetchone()["n"]
        linked = db.execute(
            """
            SELECT COUNT(*) as n FROM price_snapshots
            WHERE price > 0
              AND canonical_product_id IS NOT NULL AND canonical_product_id != ''
            """
        ).fetchone()["n"]
        distinct = db.execute(
            """
            SELECT COUNT(DISTINCT canonical_product_id) as n FROM price_snapshots
            WHERE canonical_product_id IS NOT NULL AND canonical_product_id != ''
            """
        ).fetchone()["n"]
        stats["snapshots_linked"] = int(linked)
        stats["golden_records_distinct"] = int(distinct)
        stats["unlinked_snapshots"] = int(total) - int(linked)
        stats["linkage_pct"] = round(int(linked) / int(total) * 100, 1) if total else 0.0
    except Exception as exc:
        logger.debug("index_stats db metrics skipped: %s", exc)
    finally:
        db.close()
    return stats


def enrich_product(item: Dict[str, Any], store_key: str = "") -> Dict[str, Any]:
    """Enrich a single product dict with canonical Index data. Never raises."""
    try:
        return _get_service().enrich(item, store_key=store_key or item.get("store", ""))
    except Exception as exc:
        logger.debug("enrich_product skipped '%s': %s", item.get("name", "?")[:40], exc)
        return item


def enrich_list(items: List[Dict[str, Any]], store_key: str = "") -> List[Dict[str, Any]]:
    """Enrich a list of product dicts in-place."""
    for item in items:
        if isinstance(item, dict):
            enrich_product(item, store_key=store_key or item.get("store", ""))
    return items


def _row_to_snapshot(row: Any) -> tuple[str, str, Dict[str, Any]]:
    store = str(row["store"] or "")
    pid = str(row["product_id"] or "")
    snapshot = {
        "store": store,
        "sku": pid,
        "name": str(row["name"] or ""),
        "brand": str(row["brand"] or ""),
        "price": float(row["price"] or 0),
        "currency": str(row["currency"] or "USD"),
    }
    return store, pid, snapshot


def _resolve_and_link(
    svc: IndexService,
    db: Any,
    row: Any,
    *,
    dry_run: bool,
    stats: Dict[str, int],
) -> None:
    store, pid, snapshot = _row_to_snapshot(row)
    try:
        result = svc.resolve_snapshot(snapshot)
        if not result.product:
            stats["skipped"] += 1
            return
        stats["resolved"] += 1
        match = result.match_type
        if match in stats:
            stats[match] += 1
        elif match == "none":
            stats["auto"] += 1

        prod_id = result.product.id
        if not dry_run and prod_id:
            db.execute(
                """
                UPDATE price_snapshots
                SET canonical_product_id = ?
                WHERE store = ? AND product_id = ?
                """,
                (prod_id, store, pid),
            )
            stats["linked"] += 1
    except Exception as exc:
        stats["errors"] += 1
        logger.debug("index snapshot %s/%s: %s", store, pid, exc)


def _fetch_recent_snapshot_rows(db: Any, *, since_minutes: int, limit: int) -> list[Any]:
    import market_core

    if market_core.USE_PG:
        return db.execute(
            """
            SELECT store, product_id, name, brand, price, currency
            FROM price_snapshots
            WHERE queried_at >= NOW() - (%s * INTERVAL '1 minute')
              AND price > 0 AND name IS NOT NULL AND trim(name) != ''
            ORDER BY queried_at DESC
            LIMIT %s
            """,
            (since_minutes, limit),
        ).fetchall()
    return db.execute(
        """
        SELECT store, product_id, name, brand, price, currency
        FROM price_snapshots
        WHERE queried_at >= datetime('now', ?)
          AND price > 0 AND name IS NOT NULL AND trim(name) != ''
        ORDER BY queried_at DESC
        LIMIT ?
        """,
        (f"-{since_minutes} minutes", limit),
    ).fetchall()


def _fetch_unlinked_snapshot_rows(db: Any, *, limit: int) -> list[Any]:
    """One row per (store, product_id) so each batch covers new SKUs, not duplicate history rows."""
    import market_core

    if market_core.USE_PG:
        return db.execute(
            """
            SELECT DISTINCT ON (store, product_id)
              store, product_id, name, brand, price, currency
            FROM price_snapshots
            WHERE (canonical_product_id IS NULL OR canonical_product_id = '')
              AND price > 0 AND name IS NOT NULL AND trim(name) != ''
            ORDER BY store, product_id, queried_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return db.execute(
        """
        SELECT store, product_id,
               MAX(name) AS name, MAX(brand) AS brand,
               MAX(price) AS price, MAX(currency) AS currency
        FROM price_snapshots
        WHERE (canonical_product_id IS NULL OR canonical_product_id = '')
          AND price > 0 AND name IS NOT NULL AND trim(name) != ''
        GROUP BY store, product_id
        ORDER BY store, product_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _index_snapshot_rows(
    rows: list[Any],
    *,
    dry_run: bool = False,
) -> Dict[str, int]:
    import market_core

    svc = _get_service()
    stats = {
        "resolved": 0,
        "linked": 0,
        "exact": 0,
        "fuzzy": 0,
        "auto": 0,
        "skipped": 0,
        "errors": 0,
        "registry_size": svc.size,
    }

    db = market_core.get_db()
    try:
        ensure_canonical_product_id_column(db)
        seen: set[tuple[str, str]] = set()
        for row in rows:
            store = str(row["store"] or "")
            pid = str(row["product_id"] or "")
            key = (store, pid)
            if key in seen:
                continue
            seen.add(key)
            _resolve_and_link(svc, db, row, dry_run=dry_run, stats=stats)
        if not dry_run:
            db.commit()
    finally:
        db.close()

    stats["registry_size"] = svc.size
    return stats


def _index_recent_snapshots(
    *,
    limit: Optional[int] = None,
    since_minutes: Optional[int] = None,
) -> Dict[str, int]:
    """Batch-resolve recent price_snapshots into Golden Records."""
    import market_core

    limit = limit if limit is not None else int(os.getenv("INDEX_COLLECT_LIMIT", "500"))
    since_minutes = since_minutes if since_minutes is not None else int(
        os.getenv("INDEX_COLLECT_SINCE_MINUTES", "15")
    )

    db = market_core.get_db()
    try:
        ensure_canonical_product_id_column(db)
        rows = _fetch_recent_snapshot_rows(db, since_minutes=since_minutes, limit=limit)
    finally:
        db.close()

    return _index_snapshot_rows(rows)


def backfill_canonical_product_ids(
    *,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Resolve snapshots missing canonical_product_id and stamp the UPID column."""
    import market_core

    limit = limit if limit is not None else int(os.getenv("INDEX_BACKFILL_LIMIT", "1000"))
    db = market_core.get_db()
    try:
        ensure_canonical_product_id_column(db)
        rows = _fetch_unlinked_snapshot_rows(db, limit=limit)
    finally:
        db.close()

    stats = _index_snapshot_rows(rows, dry_run=dry_run)
    stats["fetched"] = len(rows)
    return stats


def sync_golden_taxonomy_to_core() -> int:
    """Export index Golden Record taxonomy → core enrichment_cache for indicators."""
    try:
        import market_core
        from market_core.golden_taxonomy import set_taxonomy_registry

        svc = _get_service()
        products = svc.export_taxonomy_registry()
        if not products:
            return 0
        db = market_core.get_db()
        try:
            set_taxonomy_registry(db, products, registry_size=svc.size)
            db.commit()
        finally:
            db.close()
        logger.info("Index taxonomy synced: %d golden records", len(products))
        return len(products)
    except Exception as exc:
        logger.warning("sync_golden_taxonomy_to_core failed: %s", exc)
        return 0


def certify_round(
    products_saved: int,
    store_sample: str = "",
    *,
    limit: Optional[int] = None,
    since_minutes: Optional[int] = None,
) -> Dict[str, int]:
    """
    Called after each collect_prices.py cycle.
    Batch-resolves recent snapshots and returns indexing stats.
    """
    try:
        stats = _index_recent_snapshots(limit=limit, since_minutes=since_minutes)
        stats["taxonomy_synced"] = sync_golden_taxonomy_to_core()
        logger.info(
            "Index gate: %d prices collected → %d resolved, %d linked "
            "(%d exact, %d fuzzy, %d auto) registry=%d taxonomy=%d store=%s",
            products_saved,
            stats["resolved"],
            stats["linked"],
            stats["exact"],
            stats["fuzzy"],
            stats["auto"],
            stats["registry_size"],
            stats.get("taxonomy_synced", 0),
            store_sample or "mixed",
        )
        return stats
    except Exception as exc:
        logger.warning("Index gate certify_round failed: %s", exc)
        return {
            "resolved": 0,
            "linked": 0,
            "exact": 0,
            "fuzzy": 0,
            "auto": 0,
            "skipped": 0,
            "errors": 0,
            "registry_size": 0,
            "failed": 1,
        }