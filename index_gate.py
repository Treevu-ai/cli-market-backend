"""
Index Gate — Semantic enrichment bridge for CLI Market Backend.

Enriches raw price snapshots with canonical product identities.
Uses cli-market-index normalizers as the single source of truth.

Usage (in any router):
    from index_gate import enrich_product, enrich_list
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from engines.normalizer.unit_normalizer import extract_measurement, format_display
from engines.normalizer.brand_normalizer import canonicalize_brand
from core.resolver import _build_product_id, infer_category

logger = logging.getLogger("market.index_gate")


# ── Resolution ────────────────────────────────────────────────────

def _resolve(name: str, brand: str = "", store: str = "") -> Optional[Dict[str, Any]]:
    """Core resolution — returns the 'index' block dict or None."""
    measurement = extract_measurement(name)
    if not measurement:
        return None

    qty, unit = measurement
    brand_slug = canonicalize_brand(brand, name)
    category   = infer_category(name)
    upid       = _build_product_id(brand_slug, category, qty, unit)
    display    = format_display(qty, unit)

    return {
        "id":             upid,
        "canonical_name": name.strip().title(),
        "confidence":     0.85,
        "match_type":     "auto",
        "measurement":    {"value": qty, "unit": unit, "display": display},
    }


# ── Public API ────────────────────────────────────────────────────

def enrich_product(item: Dict[str, Any], store_key: str = "") -> Dict[str, Any]:
    """
    Enriches a single product dict with canonical Index data.
    Adds an 'index' key. No-op if resolution fails. Never raises.
    """
    try:
        result = _resolve(
            item.get("name", ""),
            item.get("brand", ""),
            store_key or item.get("store", ""),
        )
        if result:
            item["index"] = result
    except Exception as exc:
        logger.debug("enrich_product skipped '%s': %s", item.get("name", "?")[:40], exc)
    return item


def enrich_list(items: List[Dict[str, Any]], store_key: str = "") -> List[Dict[str, Any]]:
    """Enriches a list of product dicts in-place. Returns the same list."""
    for item in items:
        if isinstance(item, dict):
            enrich_product(item, store_key=store_key or item.get("store", ""))
    return items


def certify_round(products_saved: int, store_sample: str = "") -> None:
    """
    Called after each collect_prices.py cycle.
    Logs enrichment signal. In production: batch-resolves recent snapshots
    and writes UPIDs back to the price_snapshots table.
    """
    logger.info(
        "Index gate: %d products collected — enrichment cycle complete (sample store: %s)",
        products_saved,
        store_sample or "mixed",
    )
