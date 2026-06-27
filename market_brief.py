"""Executive summary builder for `market brief` — Bloomberg-style terminal entry point."""

from __future__ import annotations

from typing import Any


KPI_LABELS = {
    "pvi": ("PVI", "Price Volatility Index"),
    "bai": ("BAI", "Basket Affordability Index"),
    "pdi": ("PDI", "Promo Detection Index"),
    "rcs": ("RCS", "Retail Competitiveness Score"),
}


def _indicator_value(data: dict, key: str) -> float | None:
    for ind in data.get("analytics", {}).get("indicators", []):
        if ind.get("key") == key:
            val = ind.get("value")
            return float(val) if val is not None else None
    return None


def _largest_anomaly(sub_items: list[dict]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_abs = 0.0
    for row in sub_items:
        sig = row.get("signals") or {}
        mom = sig.get("subcat_price_momentum") or {}
        val = mom.get("value")
        if val is None:
            continue
        try:
            delta = float(val)
        except (TypeError, ValueError):
            continue
        if abs(delta) > best_abs:
            best_abs = abs(delta)
            best = {
                "subcategory": row.get("subcategory") or row.get("name") or "—",
                "delta_pct": round(delta, 1),
            }
    return best


def _score_value(scores: dict, key: str) -> float | None:
    entry = scores.get(key)
    if isinstance(entry, dict):
        val = entry.get("score")
        return float(val) if val is not None else None
    if entry is not None:
        try:
            return float(entry)
        except (TypeError, ValueError):
            return None
    return None


def build_brief_summary(data: dict) -> dict[str, Any]:
    """Normalize /v1/intel/brief JSON into executive summary fields."""
    country = (data.get("country") or "PE").upper()
    shelf = data.get("shelf") or {}
    confidence = data.get("confidence") or {}
    scores = data.get("scores") or {}
    sub_items = data.get("subcategories", {}).get("subcategories") or []

    inflation_pct = _indicator_value(data, "shelf_inflation_avg_pct")
    pvi = shelf.get("price_dispersion")
    bai = shelf.get("basket_stress_index")
    pdi = shelf.get("promo_intensity")
    rcs = _score_value(scores, "fairness")

    return {
        "country": country,
        "headline": data.get("headline") or f"{country}: moat activo",
        "days": data.get("days", 7),
        "inflation_pct": inflation_pct,
        "pvi": pvi,
        "bai": bai,
        "pdi": pdi,
        "rcs": rcs,
        "stores_active": confidence.get("stores_active"),
        "moat_freshness_pct": confidence.get("moat_freshness_pct"),
        "largest_anomaly": _largest_anomaly(sub_items),
        "scores": scores,
    }


def _fmt_pct(val: float | None, *, signed: bool = False) -> str:
    if val is None:
        return "—"
    if signed:
        return f"{val:+.1f}%"
    return f"{val:.1f}%"


def _fmt_num(val: float | None) -> str:
    if val is None:
        return "—"
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return f"{val:.1f}"


def format_brief_lines(summary: dict[str, Any], *, lang: str = "es") -> list[tuple[str, str]]:
    """Return label/value rows for terminal rendering."""
    es = lang != "en"
    days = summary.get("days", 7)
    anomaly = summary.get("largest_anomaly")

    if es:
        rows: list[tuple[str, str]] = [
            ("Inflación retail", _fmt_pct(summary.get("inflation_pct"), signed=True) + f" ({days}d)"),
            (f"{KPI_LABELS['pvi'][0]} · dispersión", _fmt_pct(summary.get("pvi"))),
            (f"{KPI_LABELS['bai'][0]} · canasta", _fmt_num(summary.get("bai"))),
            (f"{KPI_LABELS['pdi'][0]} · promos", _fmt_num(summary.get("pdi"))),
            (f"{KPI_LABELS['rcs'][0]} · fairness", _fmt_num(summary.get("rcs"))),
        ]
        if anomaly:
            name = str(anomaly.get("subcategory", "—"))
            delta = anomaly.get("delta_pct", 0)
            rows.append(("Mayor anomalía", f"{name} {delta:+.1f}% vs mediana"))
        rows.extend([
            ("Tiendas activas", str(summary.get("stores_active") or "—")),
            ("Frescura del moat", _fmt_pct(summary.get("moat_freshness_pct"))),
        ])
        return rows

    rows = [
        ("Retail inflation", _fmt_pct(summary.get("inflation_pct"), signed=True) + f" ({days}d)"),
        (f"{KPI_LABELS['pvi'][0]} · dispersion", _fmt_pct(summary.get("pvi"))),
        (f"{KPI_LABELS['bai'][0]} · basket stress", _fmt_num(summary.get("bai"))),
        (f"{KPI_LABELS['pdi'][0]} · promos", _fmt_num(summary.get("pdi"))),
        (f"{KPI_LABELS['rcs'][0]} · fairness", _fmt_num(summary.get("rcs"))),
    ]
    if anomaly:
        name = str(anomaly.get("subcategory", "—"))
        delta = anomaly.get("delta_pct", 0)
        rows.append(("Largest anomaly", f"{name} {delta:+.1f}% vs median"))
    rows.extend([
        ("Stores active", str(summary.get("stores_active") or "—")),
        ("Moat freshness", _fmt_pct(summary.get("moat_freshness_pct"))),
    ])
    return rows


def brief_title(country: str, *, lang: str = "es") -> str:
    cc = (country or "PE").upper()
    if lang == "en":
        return f"Agentic Commerce Brief — {cc}"
    return f"Agentic Commerce Brief — {cc}"


def brief_footer(*, lang: str = "es") -> str:
    if lang == "en":
        return "market compare · market basket · market scores · market inflation"
    return "market compare · market basket · market scores · market inflation"
