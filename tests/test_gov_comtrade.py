"""collect_gov_comtrade() — UN Comtrade Peru merchandise exports/imports ingest/read.

Fourth gov connector wired this session, same shape as test_gov_bcrp.py /
test_gov_sisap.py / test_gov_wto.py: POST /admin/cron/gov-comtrade ingests,
GET /v1/intel/gov-observations reads it back (source-agnostic).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_EXPORTS_PAYLOAD = {
    "data": [
        {"period": "2024", "motCode": 0, "primaryValue": 74053924515.753},
    ]
}
_IMPORTS_PAYLOAD = {
    "data": [
        {"period": "2024", "motCode": 0, "primaryValue": 55026607411.735},
    ]
}


@pytest.fixture
def index_env(monkeypatch, tmp_path):
    import index_gate as gate

    monkeypatch.setenv("INDEX_PERSISTENCE", "1")
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("COMTRADE_API_SUBSCRIPTION_KEY", "test-key")
    monkeypatch.delenv("INDEX_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    gate._service = None
    yield gate
    gate._service = None


@pytest.mark.asyncio
async def test_collect_gov_comtrade_resolves_export_and_import_observations(index_env, monkeypatch):
    from connectors.gov.adapters import comtrade as comtrade_module

    async def fake_fetch(self):
        return {"X": _EXPORTS_PAYLOAD, "M": _IMPORTS_PAYLOAD}

    monkeypatch.setattr(comtrade_module.UNComtradeConnector, "fetch", fake_fetch)

    result = await index_env.collect_gov_comtrade()

    assert result["ok"] is True
    assert result["source"] == "comtrade_pe"
    assert result["fetched"] == 2
    assert result["resolved"] == 2
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_gov_observations_reads_back_comtrade_data_without_colliding_with_wto_or_bcrp_slugs(index_env, monkeypatch):
    from connectors.gov.adapters import comtrade as comtrade_module

    async def fake_fetch(self):
        return {"X": _EXPORTS_PAYLOAD, "M": _IMPORTS_PAYLOAD}

    monkeypatch.setattr(comtrade_module.UNComtradeConnector, "fetch", fake_fetch)
    await index_env.collect_gov_comtrade()

    comtrade_exports = index_env.gov_observations(commodity_slug="exportaciones_comtrade_pe")
    assert len(comtrade_exports) == 1
    assert comtrade_exports[0]["price"] == 74053924515.753

    # Deliberately distinct from BCRP's and WTO's exports slugs (different
    # source/methodology) — this query must not accidentally match them.
    assert index_env.gov_observations(commodity_slug="exportaciones_fob_pe") == []
    assert index_env.gov_observations(commodity_slug="exportaciones_wto_pe") == []


def test_collect_gov_comtrade_handles_missing_api_key_gracefully(index_env, monkeypatch):
    import asyncio

    monkeypatch.delenv("COMTRADE_API_SUBSCRIPTION_KEY", raising=False)

    result = asyncio.run(index_env.collect_gov_comtrade())

    assert result["ok"] is False
    assert "COMTRADE_API_SUBSCRIPTION_KEY" in result["error"]
