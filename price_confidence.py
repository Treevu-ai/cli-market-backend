"""Price confidence flags — separate detection from publication."""

from __future__ import annotations

DISCOUNT_PUBLIC_MIN = 5
DISCOUNT_PUBLIC_MAX = 80
MEDIAN_OUTLIER_BAND = 5.0
MARKETING_MAX_SPREAD = 10.0


def discount_public_ok(discount_pct: float | None) -> bool:
    """Retail-sane discount range for public top_discounts."""
    if discount_pct is None:
        return False
    return DISCOUNT_PUBLIC_MIN <= float(discount_pct) <= DISCOUNT_PUBLIC_MAX


def discount_is_scrape_error(discount_pct: float | None) -> bool:
    """Likely bad list_price scrape (e.g. -99%)."""
    if discount_pct is None:
        return False
    return float(discount_pct) >= 90


def median_outlier_bounds(median: float, band: float = MEDIAN_OUTLIER_BAND) -> tuple[float, float]:
    if median <= 0:
        return (0.0, 0.0)
    return median / band, median * band


def price_vs_median_confidence(price: float, median: float, band: float = MEDIAN_OUTLIER_BAND) -> str:
    """ok when inside [median/band, median*band]; else suspect."""
    if price <= 0 or median <= 0:
        return "ok"
    lo, hi = median_outlier_bounds(median, band)
    if price < lo or price > hi:
        return "suspect"
    return "ok"


def spread_confidence(spread_ratio: float) -> str:
    if spread_ratio > MARKETING_MAX_SPREAD:
        return "crit"
    if spread_ratio > 2:
        return "warn"
    return "ok"


def spread_public_ok(spread_ratio: float) -> bool:
    """Comparable spreads for marketing/API defaults."""
    return spread_confidence(spread_ratio) != "crit"


def discount_pct(price: float, list_price: float | None) -> float | None:
    if not list_price or list_price <= price or price <= 0:
        return None
    return round((1 - price / list_price) * 100, 1)


def compute_snapshot_confidence(price: float, list_price: float | None) -> str:
    """Row-level confidence at ingest (discount scrape errors → suspect)."""
    if discount_is_scrape_error(discount_pct(price, list_price)):
        return "suspect"
    return "ok"
