"""Intelligence API v1 — shared queries for /v1/quality, /v1/prices, /v1/dispersion, /v1/coverage."""

from __future__ import annotations

from dashboard_quality import QUALITY_FILTERS
from market_core import STORES
from market_db import price_snapshots_has_confidence
from market_spread import build_spread_analytics, find_median_outliers
from price_confidence import discount_is_scrape_error, spread_confidence

MAX_LIMIT = 500
DEFAULT_LIMIT = 50


def _clamp_limit(limit: int, default: int = DEFAULT_LIMIT) -> int:
    return max(1, min(int(limit or default), MAX_LIMIT))


def _discount_pct(price: float, list_price: float | None) -> float | None:
    if not list_price or list_price <= price or price <= 0:
        return None
    return round((1 - price / list_price) * 100, 1)


def _load_spread_products(db) -> list[dict]:
    rows = db.execute(
        """
        SELECT line, line_name, currency, category, name, brand, price, store, store_name
        FROM price_snapshots WHERE price > 0 AND price < 999999
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _outlier_keys(products: list[dict]) -> set[tuple[str | None, str | None]]:
    outliers = find_median_outliers(products, min_group=5, band=5.0, limit=50000)
    return {(o.get("store"), o.get("name")) for o in outliers}


def count_flagged_outliers(db) -> int:
    products = _load_spread_products(db)
    return len(_outlier_keys(products))


def build_coverage_matrix(db, *, line: str | None = None) -> dict:
    line_country_raw = db.execute(
        """SELECT ps.line, ps.store, COUNT(*) as n
           FROM price_snapshots ps WHERE ps.price>0
           GROUP BY ps.line, ps.store"""
    ).fetchall()
    line_country_map: dict[str, set] = {}
    for r in line_country_raw:
        country = STORES.get(r["store"], {}).get("country", "??")
        ln = r["line"]
        if line and ln != line:
            continue
        key = f"{ln}|{country}"
        line_country_map.setdefault(key, set()).add(r["store"])

    cells = [
        {"line": k.split("|")[0], "country": k.split("|")[1], "stores": len(v)}
        for k, v in line_country_map.items()
    ]
    lines = sorted(set(c["line"] for c in cells))
    countries = sorted(set(c["country"] for c in cells))
    lookup = {f"{c['line']}|{c['country']}": c["stores"] for c in cells}
    gaps = []
    for ln in lines:
        for country in countries:
            if lookup.get(f"{ln}|{country}", 0) == 0:
                gaps.append(f"{ln}×{country}")

    return {
        "lines": lines,
        "countries": countries,
        "cells": cells,
        "gaps": gaps,
    }


def query_flagged(
    db,
    *,
    reason: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    limit = _clamp_limit(limit)
    offset = max(0, int(offset or 0))
    items: list[dict] = []

    if reason in (None, "discount"):
        rows = db.execute(
            """
            SELECT name, store, store_name, price, list_price, currency, line, line_name,
                   ROUND(((1 - price / NULLIF(list_price, 0)) * 100)::numeric, 1) as discount_pct
            FROM price_snapshots
            WHERE list_price > price AND price > 0 AND list_price < 999999
              AND ROUND(((1 - price / NULLIF(list_price, 0)) * 100)::numeric) >= 90
            ORDER BY discount_pct DESC, store, name
            """
        ).fetchall()
        for r in rows:
            items.append({
                "name": r["name"],
                "store": r["store"],
                "store_name": r["store_name"],
                "reason": "discount>=90%",
                "discount_pct": float(r["discount_pct"] or 0),
                "price": float(r["price"]),
                "list_price": float(r["list_price"]) if r["list_price"] else None,
                "currency": r["currency"],
                "line_name": r["line_name"] or r["line"],
                "confidence": "suspect",
            })

    if reason in (None, "outlier"):
        products = _load_spread_products(db)
        for o in find_median_outliers(products, min_group=5, band=5.0, limit=50000):
            items.append({
                "name": o.get("name"),
                "store": o.get("store"),
                "store_name": o.get("store_name"),
                "reason": "median_outlier_5x",
                "discount_pct": None,
                "price": o.get("price"),
                "list_price": None,
                "currency": o.get("currency"),
                "line_name": o.get("line_name"),
                "confidence": "suspect",
            })

    if reason in (None, "spread"):
        products = _load_spread_products(db)
        dispersion = build_spread_analytics(products)["dispersion"]
        for d in dispersion:
            if d.get("status") != "crit":
                continue
            items.append({
                "name": d.get("subcategory") or d.get("line"),
                "store": None,
                "store_name": None,
                "reason": "spread>10x",
                "discount_pct": None,
                "price": d.get("avg_price"),
                "list_price": None,
                "currency": d.get("currency"),
                "line_name": d.get("line"),
                "confidence": "crit",
                "spread_ratio": d.get("spread_ratio"),
            })

    items.sort(key=lambda x: (x.get("reason") or "", -(x.get("discount_pct") or 0)))
    total = len(items)
    page = items[offset : offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters_applied": list(QUALITY_FILTERS),
        "items": page,
    }


def query_prices(
    db,
    *,
    clean: bool = True,
    country: str | None = None,
    line: str | None = None,
    currency: str | None = None,
    store: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    limit = _clamp_limit(limit, default=100)
    offset = max(0, int(offset or 0))

    clauses = ["price > 0", "price < 999999"]
    params: list = []
    if country:
        store_keys = [k for k, s in STORES.items() if s.get("country") == country.upper()]
        if not store_keys:
            return {"total": 0, "limit": limit, "offset": offset, "clean": clean, "items": []}
        placeholders = ",".join("?" * len(store_keys))
        clauses.append(f"store IN ({placeholders})")
        params.extend(store_keys)
    if line:
        clauses.append("line = ?")
        params.append(line)
    if currency:
        clauses.append("currency = ?")
        params.append(currency.upper())
    if store:
        clauses.append("store = ?")
        params.append(store)

    use_confidence = clean and price_snapshots_has_confidence(db)
    if use_confidence:
        clauses.append("confidence = 'ok'")

    where = " AND ".join(clauses)
    select_cols = (
        "product_id, name, store, store_name, price, list_price, currency, "
        "line, line_name, queried_at"
    )
    if use_confidence:
        select_cols += ", confidence"

    if use_confidence:
        total_row = db.execute(
            f"SELECT COUNT(*) as n FROM price_snapshots WHERE {where}",
            tuple(params),
        ).fetchone()
        total = int(total_row["n"] if total_row else 0)
        rows = db.execute(
            f"""
            SELECT {select_cols}
            FROM price_snapshots
            WHERE {where}
            ORDER BY queried_at DESC, store, name
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        ).fetchall()
        items = []
        for r in rows:
            row = dict(r)
            price = float(row["price"])
            list_price = float(row["list_price"]) if row.get("list_price") else None
            items.append({
                "product_id": row.get("product_id"),
                "name": row["name"],
                "store": row["store"],
                "store_name": row["store_name"],
                "price": price,
                "currency": row["currency"],
                "line": row["line"],
                "line_name": row["line_name"],
                "queried_at": row.get("queried_at"),
                "discount_pct": _discount_pct(price, list_price),
                "confidence": row.get("confidence") or "ok",
            })
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "clean": clean,
            "items": items,
        }

    rows = db.execute(
        f"""
        SELECT {select_cols}
        FROM price_snapshots
        WHERE {where}
        ORDER BY queried_at DESC, store, name
        """,
        tuple(params),
    ).fetchall()

    outlier_keys: set[tuple] = set()
    if clean:
        outlier_keys = _outlier_keys(_load_spread_products(db))

    filtered = []
    for r in rows:
        row = dict(r)
        price = float(row["price"])
        list_price = float(row["list_price"]) if row.get("list_price") else None
        disc = _discount_pct(price, list_price)
        if clean and discount_is_scrape_error(disc):
            continue
        if clean and (row["store"], row["name"]) in outlier_keys:
            continue
        filtered.append({
            "product_id": row.get("product_id"),
            "name": row["name"],
            "store": row["store"],
            "store_name": row["store_name"],
            "price": price,
            "currency": row["currency"],
            "line": row["line"],
            "line_name": row["line_name"],
            "queried_at": row.get("queried_at"),
            "discount_pct": disc,
            "confidence": "ok",
        })

    total = len(filtered)
    page = filtered[offset : offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "clean": clean,
        "items": page,
    }


def query_dispersion(
    db,
    *,
    clean: bool = True,
    line: str | None = None,
    currency: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    limit = _clamp_limit(limit)
    offset = max(0, int(offset or 0))
    products = _load_spread_products(db)
    dispersion = build_spread_analytics(products)["dispersion"]

    items = []
    for d in dispersion:
        if line and d.get("line_key") != line and d.get("line") != line:
            continue
        if currency and (d.get("currency") or "").upper() != currency.upper():
            continue
        status = d.get("status") or spread_confidence(float(d.get("spread_ratio") or 0))
        if clean and status == "crit":
            continue
        items.append({
            "line": d.get("line"),
            "subcategory": d.get("subcategory"),
            "currency": d.get("currency"),
            "spread_ratio": d.get("spread_ratio"),
            "status": status,
            "stores": d.get("count"),
            "avg_price": d.get("avg_price"),
            "price_basis": d.get("price_basis"),
            "suspect": status == "crit",
        })

    total = len(items)
    page = items[offset : offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "clean": clean,
        "grouping": "line+currency+subcategory",
        "items": page,
    }


def intelligence_acceso_examples(base: str = "https://cli-market-production.up.railway.app") -> list[dict]:
    return [
        {"cmd": f"curl -s {base}/v1/sources/health | jq '.summary'", "use": "Salud scraping por tienda"},
        {"cmd": f"curl -s '{base}/v1/quality/flagged?limit=5' | jq '.total'", "use": "Conteo anomalías (discount/outlier/spread)"},
        {"cmd": f"curl -s '{base}/v1/prices?clean=1&country=PE&limit=10' | jq '.total'", "use": "Precios limpios paginados"},
        {"cmd": f"curl -s '{base}/v1/dispersion?clean=1&limit=10' | jq '.items[0]'", "use": "Brechas por subcategoría"},
        {"cmd": f"curl -s {base}/v1/basket | jq '.stores[]|{{store_name,comparable,total}}'", "use": "Canasta snapshot (DB)"},
        {"cmd": f"curl -s {base}/v1/coverage/matrix | jq '.gaps[:5]'", "use": "Huecos país × línea"},
    ]
