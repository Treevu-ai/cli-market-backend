"""Tests for persistent index_gate bridge."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def index_env(monkeypatch, tmp_path):
    import index_gate as gate

    monkeypatch.setenv("INDEX_PERSISTENCE", "1")
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "index"))
    monkeypatch.delenv("INDEX_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    gate._service = None
    yield gate
    gate._service = None


def test_enrich_product_adds_index_block(index_env):
    item = {
        "name": "Leche Gloria 1L",
        "price": 4.5,
        "store": "wong",
        "brand": "Gloria",
        "sku": "123",
    }
    enriched = index_env.enrich_product(item, store_key="wong")
    assert "index" in enriched
    assert enriched["index"]["id"].startswith("prod_")
    assert enriched["index"]["measurement"]["unit"] == "L"


def test_enrich_persists_across_restarts(index_env):
    item = {
        "name": "Aceite Primor 1L",
        "price": 9.9,
        "store": "metro_pe",
        "brand": "Primor",
        "sku": "55",
    }
    first = index_env.enrich_product(dict(item), store_key="metro_pe")
    prod_id = first["index"]["id"]

    index_env._service = None
    second = index_env.enrich_product(
        {**item, "name": "Aceite Vegetal Primor 1 Litro"},
        store_key="metro_pe",
    )
    assert second["index"]["id"] == prod_id
    assert second["index"]["confidence"] >= 0.75


def test_certify_round_indexes_recent_snapshots(index_env, isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    market_core.save_price_snapshot(
        {
            "id": "idx-p1",
            "product_id": "idx-p1",
            "name": "Yogurt Gloria 1L",
            "brand": "Gloria",
            "price": 5.5,
            "list_price": 6.0,
            "store": "wong",
            "store_name": "Wong",
            "currency": "PEN",
            "line": "supermercados",
            "line_name": "Supermercados",
        }
    )

    stats = index_env.certify_round(1, since_minutes=60)
    assert stats["resolved"] >= 1
    assert stats["linked"] >= 1
    assert stats["registry_size"] >= 1
    assert stats.get("failed", 0) == 0

    db = market_core.get_db()
    try:
        row = db.execute(
            "SELECT canonical_product_id FROM price_snapshots WHERE store = ? AND product_id = ?",
            ("wong", "idx-p1"),
        ).fetchone()
        assert row["canonical_product_id"].startswith("prod_")
    finally:
        db.close()


def test_backfill_canonical_product_ids(index_env, isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    market_core.save_price_snapshot(
        {
            "id": "bf-p1",
            "product_id": "bf-p1",
            "name": "Arroz Costeno 1kg",
            "brand": "Costeno",
            "price": 4.2,
            "list_price": 4.5,
            "store": "metro_pe",
            "store_name": "Metro",
            "currency": "PEN",
            "line": "supermercados",
            "line_name": "Supermercados",
        }
    )

    stats = index_env.backfill_canonical_product_ids(limit=10)
    assert stats["resolved"] >= 1
    assert stats["linked"] >= 1

    db = market_core.get_db()
    try:
        row = db.execute(
            "SELECT canonical_product_id FROM price_snapshots WHERE store = ? AND product_id = ?",
            ("metro_pe", "bf-p1"),
        ).fetchone()
        assert row["canonical_product_id"].startswith("prod_")
    finally:
        db.close()


def test_ensure_canonical_product_id_column(isolated_db):
    import market_core
    from price_snapshots_schema import ensure_canonical_product_id_column, price_snapshots_has_canonical_id

    market_core.ensure_db_initialized()
    db = market_core.get_db()
    try:
        assert ensure_canonical_product_id_column(db) is True
        assert price_snapshots_has_canonical_id(db) is True
    finally:
        db.close()