"""Tests for canasta snapshot SQL patterns."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from market_core.market_basket import _canasta_name_sql, build_canasta_snapshot
from market_spread import CANASTA_SQL_LIKE, matches_canasta_item


def test_canasta_sql_covers_accent_variants():
    assert "%azúcar%" in CANASTA_SQL_LIKE["azucar"]
    assert "%café%" in CANASTA_SQL_LIKE["cafe"]
    assert "%huevo%" in CANASTA_SQL_LIKE["huevos"]


def test_huevo_singular_matches_canasta():
    row = {"line": "supermercados", "name": "Huevo Blanco El Calvario 18 Piezas"}
    assert matches_canasta_item(row, "huevos")


def test_canasta_snapshot_finds_huevos(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    db.execute(
        """
        INSERT INTO price_snapshots
        (product_id, store, store_name, name, price, currency, line, line_name, queried_at)
        VALUES ('h1', 'chedraui', 'Chedraui', 'Huevo Blanco 18 Piezas', 45, 'MXN',
                'supermercados', 'Supermercados', datetime('now'))
        """
    )
    db.execute(
        """
        INSERT INTO price_snapshots
        (product_id, store, store_name, name, price, currency, line, line_name, queried_at)
        VALUES ('a1', 'chedraui', 'Chedraui', 'Azúcar Estándar 1kg', 25, 'MXN',
                'supermercados', 'Supermercados', datetime('now'))
        """
    )
    db.commit()
    snap = build_canasta_snapshot(db, min_items=1, store_filter={"chedraui"})
    store = snap["stores"][0]
    assert store["items_found"] >= 2
    db.close()


def test_canasta_name_sql_builds_or_clause():
    sql, params = _canasta_name_sql("azucar")
    assert "OR" in sql
    assert "LOWER(name) LIKE LOWER(?)" in sql
    assert "%azúcar%" in params
