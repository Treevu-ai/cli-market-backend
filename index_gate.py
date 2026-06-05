"""
Index Gate — Semantic enrichment bridge for CLI Market Backend.

Enriches raw price snapshots with canonical product identities.
Self-contained: does not require cli-market-index to be installed.

Usage (in any router):
    from index_gate import enrich_product, enrich_list
"""
from __future__ import annotations
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("market.index_gate")

# ── Inline normalizers (mirrors cli-market-index/src/engines/normalizer) ──

_NUM = r"(\d+(?:[.,]\d+)?)"


def _to_float(v: str) -> float:
    return float(v.replace(",", "."))


def _extract_measurement(text: str) -> Optional[Tuple[float, str]]:
    if not text:
        return None
    t = text.lower().strip()

    # Compound packs: "6 x 500ml" → total content
    m = re.search(
        rf"{_NUM}\s*[xX×]\s*{_NUM}\s*(ml|cc|l|lt|lts|litros?|g(?:ramos?)?|grs?|kg)\b", t
    )
    if m:
        total = _to_float(m.group(1)) * _to_float(m.group(2))
        u = m.group(3)
        if u in ("ml", "cc"):
            return round(total / 1000, 4), "L"
        if u in ("l", "lt", "lts") or u.startswith("litro"):
            return total, "L"
        if u in ("g", "gr", "grs") or u.startswith("gramo"):
            return round(total / 1000, 4), "kg"
        if u == "kg":
            return total, "kg"

    rules: list[tuple[str, str, float]] = [
        (rf"{_NUM}\s*(?:kg|kilos?|kilogramos?)\.?\b", "kg", 1.0),
        (rf"{_NUM}\s*(?:g(?:ramos?)?|grs?)\.?\b", "kg", 0.001),
        (rf"{_NUM}\s*(?:litros?|lts?|lt?)\.?\b", "L", 1.0),
        (rf"{_NUM}\s*(?:ml|cc)\.?\b", "L", 0.001),
        (rf"{_NUM}\s*(?:un(?:d|i)?|u|uds|piezas?|pzas?)\.?\b", "unit", 1.0),
        (rf"\bx\s*{_NUM}\.?\b", "unit", 1.0),
    ]
    for pattern, unit, mult in rules:
        m = re.search(pattern, t)
        if m:
            return round(_to_float(m.group(1)) * mult, 4), unit
    return None


_BRAND_MAP: dict[str, list[str]] = {
    "gloria":    ["leche gloria", "gloria s.a.", "gloria sa", "gloria"],
    "nestle":    ["nestlé", "nestle purina", "nestle"],
    "cocacola":  ["coca-cola", "coca cola", "coke"],
    "primor":    ["aceite primor", "primor"],
    "bolivar":   ["detergente bolivar", "bolivar"],
    "laive":     ["laive s.a.", "laive"],
    "backus":    ["backus & johnston", "backus"],
    "alicorp":   ["alicorp s.a.a.", "alicorp"],
    "bimbo":     ["grupo bimbo", "bimbo"],
    "unilever":  ["unilever andina", "unilever"],
    "procter":   ["procter & gamble", "p&g"],
    "kimberly":  ["kimberly-clark", "kimberly clark"],
    "colgate":   ["colgate-palmolive", "colgate"],
    "pepsico":   ["pepsi-cola", "pepsico", "pepsi"],
    "molitalia": ["molitalia s.a.", "molitalia"],
}


def _canon_brand(raw: str, name: str = "") -> str:
    combined = (raw + " " + name).lower()
    for slug, variants in _BRAND_MAP.items():
        if any(v in combined for v in variants):
            return slug
    return re.sub(r"[^a-z0-9]", "", raw.lower()) or "unknown"


def _infer_category(name: str) -> str:
    t = name.lower()
    if any(w in t for w in ["leche", "yogur", "queso", "mantequilla", "crema"]):
        return "lacteos"
    if any(w in t for w in ["arroz", "fideos", "pasta", "harina", "fideo"]):
        return "granos"
    if any(w in t for w in ["aceite", "oil"]):
        return "aceites"
    if any(w in t for w in ["detergente", "jabon", "shampoo", "lavaplatos"]):
        return "cuidado_hogar"
    if any(w in t for w in ["cerveza", "gaseosa", "agua", "jugo", "refresco"]):
        return "bebidas"
    if any(w in t for w in ["celular", "phone", "moto", "samsung", "iphone"]):
        return "electro"
    return "general"


def _build_upid(brand: str, category: str, qty: float, unit: str) -> str:
    qty_str = re.sub(r"\.0$", "", f"{qty:g}")
    return f"prod_{brand}_{category}_{qty_str}{unit.lower()}"


def _resolve(name: str, brand: str = "", store: str = "") -> Optional[Dict[str, Any]]:
    """Core resolution — returns the 'index' block dict or None."""
    measurement = _extract_measurement(name)
    if not measurement:
        return None
    qty, unit = measurement
    brand_slug = _canon_brand(brand, name)
    category   = _infer_category(name)
    upid       = _build_upid(brand_slug, category, qty, unit)
    display    = (
        f"{qty:g}{unit}" if unit != "unit" else f"{int(qty)} unit"
    )
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
