"""collect_gov_bcb() — Banco Central do Brasil (USD/BRL + IPCA) ingest/read.

Fifth gov connector, same shape as test_gov_comtrade.py: POST /admin/cron/gov-bcb
ingests, GET /v1/intel/gov-observations reads it back (source-agnostic).
Unlike Comtrade/WTO, BCB needs no API key — no "missing key" test here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_FX_PAYLOAD = [{"data": "17/07/2026", "valor": "5.1176"}]
_IPCA_PAYLOAD = [{"data": "01/06/2026", "valor": "0.16"}]


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


@pytest.mark.asyncio
async def test_collect_gov_bcb_resolves_fx_and_ipca_observations(index_env, monkeypatch):
    from connectors.gov.adapters import bcb as bcb_module

    async def fake_fetch(self):
        return {"fx": _FX_PAYLOAD, "ipca": _IPCA_PAYLOAD}

    monkeypatch.setattr(bcb_module.BCBConnector, "fetch", fake_fetch)

    result = await index_env.collect_gov_bcb()

    assert result["ok"] is True
    assert result["source"] == "bcb_br"
    assert result["fetched"] == 2
    assert result["resolved"] == 2
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_gov_observations_reads_back_bcb_data_without_colliding_with_bcrp_slugs(index_env, monkeypatch):
    from connectors.gov.adapters import bcb as bcb_module

    async def fake_fetch(self):
        return {"fx": _FX_PAYLOAD, "ipca": _IPCA_PAYLOAD}

    monkeypatch.setattr(bcb_module.BCBConnector, "fetch", fake_fetch)
    await index_env.collect_gov_bcb()

    bcb_fx = index_env.gov_observations(commodity_slug="usdbrl_bcb_br")
    assert len(bcb_fx) == 1
    assert bcb_fx[0]["price"] == 5.1176

    # Deliberately distinct from BCRP's own tipo_cambio_usd_pen (different
    # country/currency/source) — this query must not accidentally match it.
    assert index_env.gov_observations(commodity_slug="tipo_cambio_usd_pen") == []


@pytest.mark.asyncio
async def test_collect_gov_bcb_needs_no_api_key(index_env, monkeypatch):
    """BCB's SGS API is fully public — collect() must succeed with zero
    BCB-related env vars set, unlike Comtrade/WTO which require a key."""
    from connectors.gov.adapters import bcb as bcb_module

    monkeypatch.delenv("BCB_API_KEY", raising=False)

    async def fake_fetch(self):
        return {"fx": _FX_PAYLOAD, "ipca": _IPCA_PAYLOAD}

    monkeypatch.setattr(bcb_module.BCBConnector, "fetch", fake_fetch)

    result = await index_env.collect_gov_bcb()
    assert result["ok"] is True
