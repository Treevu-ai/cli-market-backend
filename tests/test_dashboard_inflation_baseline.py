"""Regression test for cli-market-backend#126: /dashboard/data must report
delta_pct=None (not a fabricated +0.0%) when a line/currency has price data
only in the recent window, with nothing in the older comparison window."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _seed_recent_only_snapshot(db, *, line="electro", currency="ARS", price=194343.0):
    db.execute(
        """
        INSERT INTO price_snapshots
        (product_id, store, store_name, name, price, list_price, currency, line, line_name,
         queried_at, confidence)
        VALUES ('p-new', 'coppel_ar', 'Coppel AR', 'Notebook', ?, ?, ?, ?, 'Electro y Tecnología',
                datetime('now'), 'ok')
        """,
        (price, price, currency, line),
    )
    db.commit()


def test_inflation_line_with_no_older_baseline_reports_null_delta(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app) as client:
        # App startup (schema init) happens on entering the context manager.
        db = isolated_db.get_db()
        _seed_recent_only_snapshot(db)
        r = client.get("/dashboard/data")
    assert r.status_code == 200
    body = r.json()

    rows = [row for row in body.get("inflation", []) if row.get("line_key") == "electro" and row.get("currency") == "ARS"]
    assert rows, "expected an electro/ARS inflation row for the seeded recent-only snapshot"
    row = rows[0]
    assert row["avg_before"] == 0
    assert row["avg_now"] > 0
    assert row["delta_pct"] is None, (
        f"delta_pct should be null when there's no older-window baseline, got {row['delta_pct']!r}"
    )
