"""Tests for Intelligence API v1 (/v1/quality, /v1/prices, /v1/basket, etc.)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    import market_core

    data_dir = tmp_path / "market_data"
    data_dir.mkdir()
    db_file = data_dir / "market.db"
    monkeypatch.setenv("MARKET_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr(market_core, "DATA_DIR", data_dir)
    monkeypatch.setattr(market_core, "DB_FILE", db_file)
    monkeypatch.setattr(market_core, "USE_PG", False)
    monkeypatch.setattr(market_core, "_db_initialized", False)
    return market_core


def _seed_snapshot(
    db,
    *,
    store="wong",
    name="Arroz 1kg",
    price=10.0,
    list_price=100.0,
    product_id="p1",
    confidence="ok",
):
    db.execute(
        """
        INSERT INTO price_snapshots
        (product_id, store, store_name, name, price, list_price, currency, line, line_name,
         queried_at, confidence)
        VALUES (?, ?, 'Wong', ?, ?, ?, 'PEN', 'supermercados', 'Supermercados', datetime('now'), ?)
        """,
        (product_id, store, name, price, list_price, confidence),
    )


def test_quality_flagged_discount(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    _seed_snapshot(db, price=1.0, list_price=100.0)
    db.commit()

    from data_v1_service import query_flagged

    payload = query_flagged(db, reason="discount", limit=10)
    assert payload["total"] >= 1
    assert payload["items"][0]["reason"] == "discount>=90%"
    assert payload["items"][0]["discount_pct"] >= 90
    db.close()


def test_prices_clean_excludes_scrape_error(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    _seed_snapshot(db, name="Bad discount", price=1.0, list_price=100.0, product_id="p-bad", confidence="suspect")
    _seed_snapshot(db, name="Good item", price=12.0, list_price=15.0, product_id="p-good", confidence="ok")
    db.commit()

    from data_v1_service import query_prices

    payload = query_prices(db, clean=True, limit=50)
    names = [i["name"] for i in payload["items"]]
    assert "Good item" in names
    assert "Bad discount" not in names
    assert payload["items"][0]["confidence"] == "ok"
    db.close()


def test_prices_clean_sql_pagination_with_confidence(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    for i in range(5):
        _seed_snapshot(
            db,
            name=f"Ok item {i}",
            price=10.0 + i,
            list_price=12.0,
            product_id=f"p-ok-{i}",
            confidence="ok",
        )
    db.commit()

    from data_v1_service import query_prices

    page = query_prices(db, clean=True, limit=2, offset=1)
    assert page["total"] == 5
    assert len(page["items"]) == 2
    db.close()


def test_basket_snapshot_source(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    for prod in ("leche", "arroz", "aceite", "azucar"):
        db.execute(
            """
            INSERT INTO price_snapshots
            (product_id, store, store_name, name, price, currency, line, line_name, queried_at, confidence)
            VALUES (?, 'wong', 'Wong', ?, 5, 'PEN', 'supermercados', 'Supermercados', datetime('now'), 'ok')
            """,
            (prod, f"{prod} 1kg"),
        )
    db.commit()

    from market_basket import build_canasta_snapshot

    payload = build_canasta_snapshot(db, min_items=3)
    assert payload["source"] == "snapshot"
    assert len(payload["stores"]) >= 1
    db.close()


def test_coverage_matrix_api(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    _seed_snapshot(db, store="wong", name="Item PE", price=10, list_price=12, product_id="p-cov")
    db.commit()
    db.close()

    from fastapi.testclient import TestClient
    import market_server

    with TestClient(market_server.app) as client:
        r = client.get("/v1/coverage/matrix")
        assert r.status_code == 200
        body = r.json()
        assert "cells" in body
        assert "gaps" in body


def test_v1_endpoints_registered(isolated_db):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    db.close()

    from fastapi.testclient import TestClient
    import market_server

    with TestClient(market_server.app) as client:
        for path in (
            "/v1/quality/flagged?limit=1",
            "/v1/prices?clean=1&limit=1",
            "/v1/dispersion?clean=1&limit=1",
            "/v1/basket",
            "/v1/coverage/matrix",
        ):
            r = client.get(path)
            assert r.status_code == 200, path
