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