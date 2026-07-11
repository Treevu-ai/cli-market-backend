"""Tests for stock_history_schema.py."""

from __future__ import annotations

from stock_history_schema import append_stock_history, ensure_stock_history_table


def test_ensure_stock_history_table_idempotent(isolated_db):
    market_core = isolated_db
    market_core.init_db()
    db = market_core.get_db()
    try:
        assert ensure_stock_history_table(db) is True
        assert ensure_stock_history_table(db) is True  # second call must not raise
    finally:
        db.close()


def test_append_stock_history_inserts_every_call_no_dedup(isolated_db):
    market_core = isolated_db
    market_core.init_db()
    db = market_core.get_db()
    try:
        ensure_stock_history_table(db)
        append_stock_history(db, "p1", "metro", True)
        append_stock_history(db, "p1", "metro", True)  # same state again -- must still insert
        append_stock_history(db, "p1", "metro", False)
        db.commit()

        rows = db.execute(
            "SELECT in_stock FROM stock_history WHERE product_id = ? AND store = ? ORDER BY id",
            ("p1", "metro"),
        ).fetchall()
        assert [r["in_stock"] for r in rows] == [1, 1, 0]
    finally:
        db.close()


def test_append_stock_history_out_of_stock_flag(isolated_db):
    market_core = isolated_db
    market_core.init_db()
    db = market_core.get_db()
    try:
        ensure_stock_history_table(db)
        append_stock_history(db, "p2", "wong", False)
        db.commit()
        row = db.execute(
            "SELECT in_stock FROM stock_history WHERE product_id = ? AND store = ?",
            ("p2", "wong"),
        ).fetchone()
        assert row["in_stock"] == 0
    finally:
        db.close()
