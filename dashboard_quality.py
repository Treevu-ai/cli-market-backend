"""Quality funnel counters for the Data Moat dashboard."""

from __future__ import annotations

QUALITY_FILTERS = [
    "discount>=90%",
    "spread>10x",
    "median_outlier_5x",
]


def build_quality_funnel(
    *,
    captured: int,
    flagged_discounts: int,
    flagged_outliers: int,
    citable: int,
) -> dict:
    """Aggregate quality tiers aligned with backend filters."""
    flagged = flagged_discounts + flagged_outliers
    clean = max(0, captured - flagged)
    return {
        "captured": captured,
        "flagged": flagged,
        "flagged_discounts": flagged_discounts,
        "flagged_outliers": flagged_outliers,
        "clean": clean,
        "citable": citable,
        "filters": list(QUALITY_FILTERS),
    }


def count_flagged_discounts(db) -> int:
    row = db.execute(
        """
        SELECT COUNT(*) as n
        FROM (
            SELECT ROUND(((1 - price / NULLIF(list_price, 0)) * 100)::numeric) as discount_pct
            FROM price_snapshots
            WHERE list_price > price AND price > 0 AND list_price < 999999
        ) discounted
        WHERE discount_pct >= 90
        """
    ).fetchone()
    return int(row["n"] if row else 0)
