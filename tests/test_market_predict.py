"""Tests for predictive commerce intelligence (forecast + arbitrage)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from market_predict import (
    _linear_regression,
    detect_arbitrage,
    forecast_product_price,
)


def test_linear_regression_upward_trend():
    points = [(0, 10.0), (7, 11.0), (14, 12.0), (21, 13.0)]
    slope, intercept, r2 = _linear_regression(points)
    assert slope > 0
    assert r2 > 0.9


def test_forecast_product_price_rising_history(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()

    db.execute(
        """
        INSERT INTO price_snapshots
        (product_id, store, store_name, name, price, currency, line, line_name, queried_at)
        VALUES ('m1', 'wong', 'Wong', 'Leche Gloria UHT 1L', 4.00, 'PEN',
                'supermercados', 'Supermercados', datetime('now'))
        """
    )
    base = datetime.now(timezone.utc) - timedelta(days=20)
    prices = [3.6, 3.7, 3.8, 3.9, 4.0, 4.1, 4.2]
    for i, price in enumerate(prices):
        ts = (base + timedelta(days=i * 3)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            """
            INSERT INTO price_history (product_id, store, price, list_price, discount, recorded_at)
            VALUES ('m1', 'wong', ?, 0, 0, ?)
            """,
            (price, ts),
        )
    db.commit()

    result = forecast_product_price(db, "leche", country="PE", horizon_days=21)
    db.close()

    assert result["confidence"] != "insufficient"
    assert result["forecast_pct"] is not None
    assert result["forecast_pct"] > 0
    assert result["procurement_signal"] in ("buy_today", "neutral", "wait")


def test_detect_arbitrage_finds_spread(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()

    rows = [
        ("r1", "wong", "Wong", "Arroz Costeño 1kg", 3.20, "PEN", "PE"),
        ("r2", "chedraui", "Chedraui", "Arroz Morelos 1kg", 32.0, "MXN", "MX"),
        ("r3", "jumbo_ar", "Jumbo", "Arroz Largo 1kg", 1200.0, "ARS", "AR"),
    ]
    for pid, store, sname, name, price, cur, _cc in rows:
        db.execute(
            """
            INSERT INTO price_snapshots
            (product_id, store, store_name, name, price, currency, line, line_name, queried_at)
            VALUES (?, ?, ?, ?, ?, ?, 'supermercados', 'Supermercados', datetime('now'))
            """,
            (pid, store, sname, name, price, cur),
        )
    db.commit()

    result = detect_arbitrage(db, "arroz", countries=["PE", "MX", "AR"], min_spread_pct=5.0)
    db.close()

    assert len(result["offers"]) >= 2
    assert result["cheapest"]["country"] in ("PE", "MX", "AR")
    assert result["spread_pct"] >= 0


def test_forecast_insufficient_data(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    result = forecast_product_price(db, "yogurt", country="PE")
    db.close()
    assert result["confidence"] == "insufficient"
