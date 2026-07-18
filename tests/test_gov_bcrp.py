"""collect_gov_bcrp() + gov_observations() — official BCRP data ingest/read.

The BCRP connector (cli-market-index) was fully built and tested but never
scheduled anywhere, and had no read path back into the backend — this
closes both gaps: POST /admin/cron/gov-bcrp ingests, GET
/v1/intel/gov-observations reads it back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TC_RESPONSE = {
    "periods": [{"name": "17.Jul.26", "values": ["3.750", "3.740"]}],
}
_IPC_RESPONSE = {
    "periods": [{"name": "Jun.2026", "values": ["112.5"]}],
}


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
async def test_collect_gov_bcrp_resolves_exchange_rate_and_ipc(index_env, monkeypatch):
    from connectors.gov.adapters import bcrp as bcrp_module

    async def fake_fetch(self):
        return {
            "tc": _TC_RESPONSE,
            "tc_url": "https://estadisticas.bcrp.gob.pe/fake-tc",
            "ipc": _IPC_RESPONSE,
            "ipc_url": "https://estadisticas.bcrp.gob.pe/fake-ipc",
        }

    monkeypatch.setattr(bcrp_module.BCRPConnector, "fetch", fake_fetch)

    result = await index_env.collect_gov_bcrp()

    assert result["ok"] is True
    assert result["source"] == "bcrp_pe"
    # venta + compra (tipo_cambio_usd_pen) + ipc_lima = 3 observations
    assert result["fetched"] == 3
    assert result["resolved"] == 3
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_gov_observations_reads_back_ingested_data(index_env, monkeypatch):
    from connectors.gov.adapters import bcrp as bcrp_module

    async def fake_fetch(self):
        return {
            "tc": _TC_RESPONSE,
            "tc_url": "https://estadisticas.bcrp.gob.pe/fake-tc",
            "ipc": _IPC_RESPONSE,
            "ipc_url": "https://estadisticas.bcrp.gob.pe/fake-ipc",
        }

    monkeypatch.setattr(bcrp_module.BCRPConnector, "fetch", fake_fetch)
    await index_env.collect_gov_bcrp()

    observations = index_env.gov_observations(commodity_slug="tipo_cambio_usd_pen")
    assert len(observations) >= 1

    all_observations = index_env.gov_observations()
    assert len(all_observations) == 3


def test_collect_gov_bcrp_handles_connector_failure_gracefully(index_env, monkeypatch):
    from connectors.gov.adapters import bcrp as bcrp_module
    import asyncio

    async def failing_fetch(self):
        raise RuntimeError("BCRP API unreachable")

    monkeypatch.setattr(bcrp_module.BCRPConnector, "fetch", failing_fetch)

    result = asyncio.run(index_env.collect_gov_bcrp())

    assert result["ok"] is False
    assert "error" in result
