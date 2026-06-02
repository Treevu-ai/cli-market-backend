"""Per-subcategory enrichment — canasta staples with scoped indicator values."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from market_core import STORES
from market_enrich_sources import _wiki_momentum_for_articles
from market_spread import CANASTA_ITEMS, _CANASTA_ITEM_PATTERNS

logger = logging.getLogger(__name__)

# Full canasta básica — same buckets as market_spread.CANASTA_ITEMS
ENRICH_SUBCATEGORIES: tuple[str, ...] = tuple(CANASTA_ITEMS)

SUBCATEGORY_INDICATOR_KEYS: tuple[str, ...] = (
    "subcat_price_momentum",
    "subcat_wiki_momentum",
    "subcat_min_price",
)

COUNTRY_WIKI_LANG: dict[str, str] = {
    "PE": "es",
    "AR": "es",
    "MX": "es",
    "CO": "es",
    "CL": "es",
    "BR": "pt",
    "IT": "it",
    "FR": "fr",
}

SUBCAT_WIKI_TITLE: dict[str, dict[str, str]] = {
    "es": {
        "leche": "Leche",
        "arroz": "Arroz",
        "aceite": "Aceite de cocina",
        "pollo": "Pollo",
        "azucar": "Azúcar",
        "huevos": "Huevo (alimento)",
        "pan": "Pan",
        "cafe": "Café",
        "queso": "Queso",
        "jabon": "Jabón",
    },
    "pt": {
        "leche": "Leite",
        "arroz": "Arroz",
        "aceite": "Óleo de cozinha",
        "pollo": "Frango",
        "azucar": "Açúcar",
        "huevos": "Ovo (alimento)",
        "pan": "Pão",
        "cafe": "Café",
        "queso": "Queijo",
        "jabon": "Sabão",
    },
    "it": {
        "leche": "Latte",
        "arroz": "Riso",
        "aceite": "Olio di semi",
        "pollo": "Pollo",
        "azucar": "Zucchero",
        "huevos": "Uovo (alimento)",
        "pan": "Pane",
        "cafe": "Caffè",
        "queso": "Formaggio",
        "jabon": "Sapone",
    },
    "fr": {
        "leche": "Lait",
        "arroz": "Riz",
        "aceite": "Huile alimentaire",
        "pollo": "Poulet",
        "azucar": "Sucre",
        "huevos": "Œuf (aliment)",
        "pan": "Pain",
        "cafe": "Café",
        "queso": "Fromage",
        "jabon": "Savon",
    },
}


def _stores_for_country(country: str) -> list[str]:
    cc = country.upper()
    return [k for k, v in STORES.items() if v.get("country") == cc and not v.get("disabled")]


def _subcat_scope(country: str, subcategory: str) -> str:
    return f"{country.upper()}:subcat:{subcategory}"


def _name_matches_subcat(name: str, subcategory: str) -> bool:
    pat = _CANASTA_ITEM_PATTERNS.get(subcategory)
    if pat and pat.search(name or ""):
        return True
    return subcategory.lower() in (name or "").lower()


def compute_subcat_price_momentum(db, country: str, subcategory: str, days: int = 7) -> float | None:
    stores = _stores_for_country(country)
    if not stores:
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
    ph = ",".join("?" * len(stores))
    rows = db.execute(
        f"""
        SELECT ph.product_id, ph.store, ph.price, ph.recorded_at, ps.name
        FROM price_history ph
        INNER JOIN price_snapshots ps ON ps.product_id = ph.product_id AND ps.store = ph.store
        WHERE ph.store IN ({ph}) AND ph.price > 0 AND ph.recorded_at >= ?
        """,
        [*stores, since],
    ).fetchall()
    series: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        if not _name_matches_subcat(r["name"], subcategory):
            continue
        k = f"{r['store']}|{r['product_id']}"
        series.setdefault(k, []).append((r["recorded_at"], float(r["price"])))
    deltas: list[float] = []
    for pts in series.values():
        if len(pts) < 2:
            continue
        pts.sort(key=lambda x: x[0])
        first, last = pts[0][1], pts[-1][1]
        if first > 0:
            deltas.append((last - first) / first * 100)
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 2)


def compute_subcat_min_price(db, country: str, subcategory: str) -> float | None:
    stores = _stores_for_country(country)
    if not stores:
        return None
    ph = ",".join("?" * len(stores))
    rows = db.execute(
        f"""
        SELECT name, price FROM price_snapshots
        WHERE store IN ({ph}) AND line = 'supermercados' AND price > 0 AND name IS NOT NULL
        """,
        stores,
    ).fetchall()
    prices = [float(r["price"]) for r in rows if _name_matches_subcat(r["name"], subcategory)]
    if not prices:
        return None
    return round(min(prices), 2)


def fetch_subcat_wiki_momentum(country: str, subcategory: str) -> float | None:
    lang = COUNTRY_WIKI_LANG.get(country.upper())
    if not lang:
        return None
    title = SUBCAT_WIKI_TITLE.get(lang, {}).get(subcategory)
    if not title:
        return None
    return _wiki_momentum_for_articles(lang, [title])


def refresh_subcategory_enrichment(db, country: str, upsert_fn) -> int:
    """Write scoped subcategory indicators. upsert_fn matches market_indicators._upsert_indicator_value."""
    cc = country.upper()
    n = 0
    for subcat in ENRICH_SUBCATEGORIES:
        scope = _subcat_scope(cc, subcat)
        meta_base = {"subcategory": subcat}

        price_mom = compute_subcat_price_momentum(db, cc, subcat)
        if price_mom is not None:
            upsert_fn(
                db,
                indicator_key="subcat_price_momentum",
                scope=scope,
                value=price_mom,
                country=cc,
                metadata={**meta_base, "window_days": 7},
            )
            n += 1

        wiki_mom = fetch_subcat_wiki_momentum(cc, subcat)
        if wiki_mom is not None:
            upsert_fn(
                db,
                indicator_key="subcat_wiki_momentum",
                scope=scope,
                value=wiki_mom,
                country=cc,
                metadata=meta_base,
            )
            n += 1

        min_p = compute_subcat_min_price(db, cc, subcat)
        if min_p is not None:
            upsert_fn(
                db,
                indicator_key="subcat_min_price",
                scope=scope,
                value=min_p,
                country=cc,
                metadata=meta_base,
            )
            n += 1

    return n


def get_subcategory_enrichment(db, country: str, limit: int = 100) -> list[dict[str, Any]]:
    """Latest subcategory signals grouped by staple."""
    cc = country.upper()
    prefix = f"{cc}:subcat:"
    rows = db.execute(
        """
        SELECT iv.indicator_key, iv.scope, iv.country, iv.value, iv.metadata_json,
               iv.recorded_at, id.name, id.unit, id.source
        FROM indicator_values iv
        LEFT JOIN indicator_definitions id ON id.key = iv.indicator_key
        WHERE iv.scope LIKE ? AND iv.indicator_key IN ('subcat_price_momentum', 'subcat_wiki_momentum', 'subcat_min_price')
        ORDER BY iv.recorded_at DESC
        LIMIT ?
        """,
        (f"{prefix}%", limit),
    ).fetchall()
    by_subcat: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for r in rows:
        subcat = (r["scope"] or "").split(":subcat:")[-1]
        dedupe = f"{r['indicator_key']}|{subcat}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        bucket = by_subcat.setdefault(
            subcat,
            {"subcategory": subcat, "country": cc, "signals": {}},
        )
        bucket["signals"][r["indicator_key"]] = {
            "value": r["value"],
            "unit": r["unit"],
            "recorded_at": r["recorded_at"],
        }
    return sorted(by_subcat.values(), key=lambda x: ENRICH_SUBCATEGORIES.index(x["subcategory"]) if x["subcategory"] in ENRICH_SUBCATEGORIES else 99)
