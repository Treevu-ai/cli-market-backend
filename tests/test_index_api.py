"""Tests for /index/* and /resolve REST endpoints."""

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
    gate._service = None
    yield gate
    gate._service = None


def _auth(monkeypatch):
    monkeypatch.setenv("MARKET_API_TOKEN", "test-token")
    import server_deps

    monkeypatch.setattr(server_deps, "DEFAULT_TOKEN", "test-token")
    return {"Authorization": "Bearer test-token"}


def test_index_resolve_post(isolated_db, index_env, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app

    headers = _auth(monkeypatch)
    with TestClient(app) as client:
        r = client.post(
            "/index/resolve",
            headers=headers,
            json={
                "name": "Leche Gloria Entera 1L",
                "brand": "Gloria",
                "store": "wong",
                "sku": "g1",
                "price": 4.5,
                "currency": "PEN",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] is True
    assert body["product"]["id"].startswith("prod_")


def test_resolve_get_alias(isolated_db, index_env, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app

    headers = _auth(monkeypatch)
    with TestClient(app) as client:
        r = client.get(
            "/resolve",
            headers=headers,
            params={"name": "Aceite Primor 1L", "brand": "Primor", "store": "metro_pe"},
        )
    assert r.status_code == 200
    assert r.json()["resolved"] is True


def test_index_stats(isolated_db, index_env, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app

    headers = _auth(monkeypatch)
    with TestClient(app) as client:
        r = client.get("/index/stats", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "registry_size" in body
    assert "linkage_pct" in body


def test_index_backfill_admin(isolated_db, index_env, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app

    headers = _auth(monkeypatch)
    market_core = isolated_db
    market_core.ensure_db_initialized()
    market_core.save_price_snapshot(
        {
            "id": "bf-api-1",
            "product_id": "bf-api-1",
            "name": "Leche Gloria 1L",
            "brand": "Gloria",
            "price": 4.5,
            "list_price": 4.5,
            "store": "wong",
            "store_name": "Wong",
            "currency": "PEN",
            "line": "supermercados",
            "line_name": "Supermercados",
        }
    )

    with TestClient(app) as client:
        r = client.post(
            "/index/backfill",
            headers=headers,
            params={"limit": 10, "batches": 1},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["linked"] >= 1
    assert "before" in body and "after" in body


def test_index_lookup_404(isolated_db, index_env, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app

    headers = _auth(monkeypatch)
    with TestClient(app) as client:
        r = client.get("/index/lookup/prod_nonexistent_xyz", headers=headers)
    assert r.status_code == 404