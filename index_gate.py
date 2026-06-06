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

from persistence.factory import create_store
from services.index_service import IndexService

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


def _get_service() -> IndexService:
    global _service
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


def certify_round(products_saved: int, store_sample: str = "") -> None:
    """
    Called after each collect_prices.py cycle.
    Logs enrichment signal and confirms the persistent index is active.
    """
    try:
        svc = _get_service()
        logger.info(
            "Index gate: %d products collected — registry=%d snapshots persisted (store=%s)",
            products_saved,
            svc.size,
            store_sample or "mixed",
        )
    except Exception as exc:
        logger.warning("Index gate certify_round failed: %s", exc)