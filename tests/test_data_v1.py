"""Tests for Intelligence API v1 (/v1/quality, /v1/prices, /v1/basket, etc.)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


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


def _auth_headers(monkeypatch):
    monkeypatch.setenv("MARKET_API_TOKEN", "test-token")
    import server_deps

    monkeypatch.setattr(server_deps, "DEFAULT_TOKEN", "test-token")
    return {"Authorization": "Bearer test-token"}


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


def test_coverage_matrix_api(isolated_db, monkeypatch):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    _seed_snapshot(db, store="wong", name="Item PE", price=10, list_price=12, product_id="p-cov")
    db.commit()
    db.close()

    from fastapi.testclient import TestClient
    import market_server

    headers = _auth_headers(monkeypatch)
    with TestClient(market_server.app) as client:
        r = client.get("/v1/coverage/matrix", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert "cells" in body
        assert "gaps" in body


def test_v1_endpoints_registered(isolated_db, monkeypatch):
    market_core = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    db.close()

    from fastapi.testclient import TestClient
    import market_server

    headers = _auth_headers(monkeypatch)
    with TestClient(market_server.app) as client:
        for path in (
            "/v1/quality/flagged?limit=1",
            "/v1/prices?clean=1&limit=1",
            "/v1/dispersion?clean=1&limit=1",
            "/v1/basket",
            "/v1/coverage/matrix",
        ):
            r = client.get(path, headers=headers)
            assert r.status_code == 200, path


def test_v1_rejects_unauthenticated(isolated_db, monkeypatch):
    """All /v1/* endpoints must return 401 when no valid token is supplied.

    This guards against DEFAULT_TOKEN being set to a non-empty value in prod
    that would allow unauthenticated access, and against auth middleware being
    accidentally removed from a route.
    """
    import server_deps
    from fastapi.testclient import TestClient
    import market_server

    # Ensure DEFAULT_TOKEN is empty so there's no server-side bypass
    monkeypatch.setattr(server_deps, "DEFAULT_TOKEN", "")
    monkeypatch.delenv("MARKET_API_TOKEN", raising=False)

    isolated_db.ensure_db_initialized()

    protected_paths = [
        "/v1/quality/flagged?limit=1",
        "/v1/prices?clean=1&limit=1",
        "/v1/dispersion?clean=1&limit=1",
        "/v1/basket",
        "/v1/coverage/matrix",
    ]

    with TestClient(market_server.app) as client:
        for path in protected_paths:
            # No Authorization header
            r_none = client.get(path)
            assert r_none.status_code in (401, 403), (
                f"GET {path} without auth returned {r_none.status_code}, expected 401/403"
            )

            # Wrong token
            r_bad = client.get(path, headers={"Authorization": "Bearer wrong-token"})
            assert r_bad.status_code in (401, 403), (
                f"GET {path} with bad token returned {r_bad.status_code}, expected 401/403"
            )


def test_v1_rejects_demo_token_as_default_bypass(isolated_db, monkeypatch):
    """DEFAULT_TOKEN must not be used as a secret-free backdoor in prod.

    If MARKET_API_TOKEN env var is set to 'demo' or any guessable value,
    a caller who knows the convention can authenticate without a real API key.
    This test verifies the auth path actually validates the token value.
    """
    import server_deps
    from fastapi.testclient import TestClient
    import market_server

    monkeypatch.setattr(server_deps, "DEFAULT_TOKEN", "secret-ops-token")
    isolated_db.ensure_db_initialized()

    with TestClient(market_server.app) as client:
        # Wrong token should still fail even if DEFAULT_TOKEN is set
        r = client.get("/v1/prices?limit=1", headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code in (401, 403), (
            f"Wrong token accepted when DEFAULT_TOKEN is set: {r.status_code}"
        )
        # Correct token should work
        r_ok = client.get("/v1/prices?limit=1", headers={"Authorization": "Bearer secret-ops-token"})
        assert r_ok.status_code == 200, (
            f"Correct DEFAULT_TOKEN was rejected: {r_ok.status_code}"
        )