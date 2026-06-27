"""Predictive commerce intelligence — Walmart Labs layer.

Price forecasting from price_history and cross-border arbitrage detection.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from market_core import STORES, convert_currency, price_to_usd

LATAM_COUNTRIES = ("PE", "MX", "CL", "CO", "AR", "BR")

PROCUREMENT_BUY_THRESHOLD_PCT = 5.0
PROCUREMENT_WAIT_THRESHOLD_PCT = -3.0


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _stores_for_country(country: str | None) -> list[str]:
    cc = (country or "").upper()
    if not cc:
        return []
    return [k for k, v in STORES.items() if v.get("country") == cc and not v.get("disabled")]


def _like_pattern(query: str) -> str:
    q = _normalize_query(query)
    return f"%{q}%" if q else "%"


def _parse_ts(value: str) -> datetime:
    raw = (value or "").replace("T", " ").replace("Z", "")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _linear_regression(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Return slope, intercept, r_squared."""
    n = len(points)
    if n < 2:
        return 0.0, points[0][1] if points else 0.0, 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in points)
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return 0.0, y_mean, 0.0
    slope = num / den
    intercept = y_mean - slope * x_mean
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in points)
    r2 = max(0.0, min(1.0, 1.0 - ss_res / ss_tot)) if ss_tot > 0 else 0.0
    return slope, intercept, r2


def _confidence_label(data_points: int, r2: float) -> str:
    if data_points < 3:
        return "insufficient"
    if data_points >= 5 and r2 >= 0.5:
        return "high"
    if data_points >= 3 and r2 >= 0.3:
        return "medium"
    return "low"


def _procurement_signal(forecast_pct: float | None) -> tuple[str, str]:
    if forecast_pct is None:
        return "neutral", "Datos insuficientes para recomendación."
    if forecast_pct >= PROCUREMENT_BUY_THRESHOLD_PCT:
        return (
            "buy_today",
            f"Precio proyectado {forecast_pct:+.1f}% — conviene comprar antes del alza.",
        )
    if forecast_pct <= PROCUREMENT_WAIT_THRESHOLD_PCT:
        return (
            "wait",
            f"Precio proyectado {forecast_pct:+.1f}% — presión a la baja, puede esperar.",
        )
    return "neutral", f"Precio proyectado {forecast_pct:+.1f}% — señal estable."


def _best_history_series(db, product_query: str, country: str, *, lookback_days: int = 90) -> dict | None:
    """Pick the price_history series with the most observations for *product_query*."""
    stores = _stores_for_country(country)
    if not stores:
        return None

    since = (datetime.now(timezone.utc) - timedelta(days=max(7, lookback_days))).strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ",".join("?" * len(stores))
    like = _like_pattern(product_query)
    rows = db.execute(
        f"""
        SELECT ph.product_id, ph.store, ph.price, ph.recorded_at,
               ps.name, ps.store_name, ps.currency
        FROM price_history ph
        INNER JOIN price_snapshots ps
          ON ps.product_id = ph.product_id AND ps.store = ph.store
        WHERE ph.store IN ({placeholders})
          AND ph.price > 0
          AND ph.recorded_at >= ?
          AND LOWER(ps.name) LIKE ?
        ORDER BY ph.recorded_at ASC
        """,
        [*stores, since, like],
    ).fetchall()

    series: dict[str, dict] = {}
    for row in rows:
        key = f"{row['store']}|{row['product_id']}"
        bucket = series.setdefault(
            key,
            {
                "product_id": row["product_id"],
                "store": row["store"],
                "store_name": row["store_name"],
                "name": row["name"],
                "currency": row["currency"],
                "points": [],
            },
        )
        ts = _parse_ts(str(row["recorded_at"]))
        bucket["points"].append((ts, float(row["price"])))

    if not series:
        return None

    best = max(series.values(), key=lambda s: len(s["points"]))
    if len(best["points"]) < 2:
        return None
    return best


def forecast_product_price(
    db,
    product_query: str,
    *,
    country: str = "PE",
    horizon_days: int = 21,
    lookback_days: int = 90,
) -> dict[str, Any]:
    """Linear forecast from price_history — procurement-oriented signal."""
    cc = (country or "PE").upper()
    horizon_days = max(1, min(horizon_days, 90))
    series = _best_history_series(db, product_query, cc, lookback_days=lookback_days)

    if not series:
        return {
            "product": product_query,
            "country": cc,
            "horizon_days": horizon_days,
            "confidence": "insufficient",
            "data_points": 0,
            "message": "Insufficient price_history for forecast — need more collector cycles.",
            "disclaimer": "Internal shelf signal — not a guarantee of future prices.",
        }

    points = sorted(series["points"], key=lambda p: p[0])
    origin = points[0][0]
    reg_points = [((p[0] - origin).total_seconds() / 86400.0, p[1]) for p in points]
    slope, intercept, r2 = _linear_regression(reg_points)
    last_x = reg_points[-1][0]
    current_price = reg_points[-1][1]
    forecast_x = last_x + float(horizon_days)
    forecast_price = max(0.01, intercept + slope * forecast_x)
    forecast_pct = round((forecast_price - current_price) / current_price * 100, 1) if current_price > 0 else None
    confidence = _confidence_label(len(reg_points), r2)
    signal, rationale = _procurement_signal(forecast_pct)

    weeks = max(1, round(horizon_days / 7))
    headline = (
        f"{series['name'][:40]}: {forecast_pct:+.1f}% proyectado en {weeks} semana(s)"
        if forecast_pct is not None
        else f"{series['name'][:40]}: señal insuficiente"
    )

    return {
        "product": product_query,
        "product_name": series["name"],
        "product_id": series["product_id"],
        "country": cc,
        "store": series["store"],
        "store_name": series["store_name"],
        "currency": series["currency"],
        "horizon_days": horizon_days,
        "lookback_days": lookback_days,
        "current_price": round(current_price, 2),
        "forecast_price": round(forecast_price, 2),
        "forecast_pct": forecast_pct,
        "trend_per_day": round(slope, 4),
        "r2": round(r2, 3),
        "data_points": len(reg_points),
        "confidence": confidence,
        "headline": headline,
        "procurement_signal": signal,
        "procurement_rationale": rationale,
        "disclaimer": (
            "Forecast from online shelf price_history via linear trend — "
            "not official CPI or supplier contract pricing."
        ),
    }


def _min_offer_for_country(db, product_query: str, country: str) -> dict | None:
    stores = _stores_for_country(country)
    if not stores:
        return None
    placeholders = ",".join("?" * len(stores))
    like = _like_pattern(product_query)
    row = db.execute(
        f"""
        SELECT product_id, name, store, store_name, price, currency
        FROM price_snapshots
        WHERE store IN ({placeholders})
          AND price > 0
          AND LOWER(name) LIKE ?
        ORDER BY price ASC
        LIMIT 1
        """,
        [*stores, like],
    ).fetchone()
    if not row:
        return None
    price = float(row["price"])
    currency = str(row["currency"] or STORES.get(row["store"], {}).get("currency", "USD"))
    usd = price_to_usd(price, currency)
    return {
        "country": country,
        "product_id": row["product_id"],
        "name": row["name"],
        "store": row["store"],
        "store_name": row["store_name"],
        "price": round(price, 2),
        "currency": currency,
        "price_usd": usd,
        "canonical_product_id": None,
    }


def _arbitrage_result(
    *,
    query: str,
    offers: list[dict],
    countries_scanned: list[str],
    min_spread_pct: float,
    canonical_product_id: str | None = None,
) -> dict[str, Any]:
    if len(offers) < 2:
        out: dict[str, Any] = {
            "query": query,
            "countries_scanned": countries_scanned,
            "offers": offers,
            "arbitrage_opportunity": False,
            "message": "Need at least 2 countries with comparable shelf prices.",
            "disclaimer": "Cross-border comparison uses static FX — not duty/tax adjusted.",
        }
        if canonical_product_id:
            out["canonical_product_id"] = canonical_product_id
        return out

    offers = sorted(offers, key=lambda o: o["price_usd"] or 0)
    cheapest, priciest = offers[0], offers[-1]
    min_usd = float(cheapest["price_usd"])
    max_usd = float(priciest["price_usd"])
    spread_pct = round((max_usd - min_usd) / min_usd * 100, 1) if min_usd > 0 else 0.0
    opportunity = spread_pct >= min_spread_pct
    return {
        "query": query,
        "countries_scanned": countries_scanned,
        "offers": offers,
        "cheapest": cheapest,
        "priciest": priciest,
        "spread_pct": spread_pct,
        "spread_usd": round(max_usd - min_usd, 4),
        "min_spread_pct": min_spread_pct,
        "arbitrage_opportunity": opportunity,
        "headline": (
            f"Buy in {cheapest['country']} ({cheapest['currency']} {cheapest['price']}) · "
            f"save {spread_pct:.1f}% vs {priciest['country']}"
            if opportunity
            else f"No material spread ({spread_pct:.1f}%) across {len(offers)} countries"
        ),
        "fx_note": "Converted via static PEN-anchor FX table (/v1/utils/exchange).",
        "disclaimer": (
            "Shelf-price arbitrage signal only — excludes import duties, logistics, "
            "and channel constraints. Not procurement advice."
        ),
        **({"canonical_product_id": canonical_product_id} if canonical_product_id else {}),
    }


def detect_arbitrage(
    db,
    product_query: str,
    *,
    countries: list[str] | None = None,
    min_spread_pct: float = 10.0,
) -> dict[str, Any]:
    """Cross-border arbitrage: cheapest vs priciest country in USD."""
    scope = [c.upper()[:2] for c in (countries or LATAM_COUNTRIES)]
    offers: list[dict] = []
    for cc in scope:
        offer = _min_offer_for_country(db, product_query, cc)
        if offer and offer.get("price_usd"):
            offers.append(offer)

    return _arbitrage_result(
        query=product_query,
        offers=offers,
        countries_scanned=scope,
        min_spread_pct=min_spread_pct,
    )


def detect_arbitrage_canonical(
    db,
    canonical_product_id: str,
    *,
    countries: list[str] | None = None,
    min_spread_pct: float = 10.0,
) -> dict[str, Any]:
    """Golden Record cross-border arbitrage when canonical_product_id is linked."""
    cid = (canonical_product_id or "").strip()
    if not cid:
        return {"error": "canonical_product_id required"}

    scope = [c.upper()[:2] for c in (countries or LATAM_COUNTRIES)]
    offers: list[dict] = []
    for cc in scope:
        stores = _stores_for_country(cc)
        if not stores:
            continue
        placeholders = ",".join("?" * len(stores))
        try:
            row = db.execute(
                f"""
                SELECT product_id, name, store, store_name, price, currency, canonical_product_id
                FROM price_snapshots
                WHERE store IN ({placeholders})
                  AND price > 0
                  AND canonical_product_id = ?
                ORDER BY price ASC
                LIMIT 1
                """,
                [*stores, cid],
            ).fetchone()
        except Exception:
            continue
        if not row:
            continue
        price = float(row["price"])
        currency = str(row["currency"] or "USD")
        usd = price_to_usd(price, currency)
        if usd:
            offers.append(
                {
                    "country": cc,
                    "product_id": row["product_id"],
                    "name": row["name"],
                    "store": row["store"],
                    "store_name": row["store_name"],
                    "price": round(price, 2),
                    "currency": currency,
                    "price_usd": usd,
                    "canonical_product_id": cid,
                }
            )

    return _arbitrage_result(
        query=cid,
        offers=offers,
        countries_scanned=scope,
        min_spread_pct=min_spread_pct,
        canonical_product_id=cid,
    )


def convert_offer_to_currency(offer: dict, target_currency: str) -> float | None:
    """Helper for agents — convert an offer price to another currency."""
    price = offer.get("price")
    frm = offer.get("currency")
    if price is None or not frm:
        return None
    try:
        return round(convert_currency(float(price), str(frm), target_currency.upper()), 2)
    except ValueError:
        return None
