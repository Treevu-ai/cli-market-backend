"""collect_gov_sisap() — official SISAP (MIDAGRI) data ingest/read.

Mirrors test_gov_bcrp.py's shape for the second gov connector wired this
session: POST /admin/cron/gov-sisap ingests, GET /v1/intel/gov-observations
reads it back (same read path, source-agnostic).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_LIMA_TABLE = (
    '<table><tbody><tr class=contenido><td>Arroz extra</td><td>Kilogramo</td>'
    '<td>1.00</td><td class=numero rel="x">4.64</td></tr>'
    '<tr class=contenido><td>Leche reconstituida (azul) 395 gr</td><td>Lata</td>'
    '<td>1.00</td><td class=numero rel="y">4.03</td></tr></tbody></table>'
)
_EMPTY_TABLE = "<table><tbody></tbody></table>"


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
async def test_collect_gov_sisap_resolves_canasta_observations(index_env, monkeypatch):
    from connectors.gov.adapters import sisap as sisap_module

    async def fake_fetch(self):
        return {region: (_LIMA_TABLE if region == "lima" else _EMPTY_TABLE) for region in sisap_module._REGION_CODES}

    monkeypatch.setattr(sisap_module.SISAPConnector, "fetch", fake_fetch)

    result = await index_env.collect_gov_sisap()

    assert result["ok"] is True
    assert result["source"] == "sisap_pe"
    assert result["fetched"] == 2
    assert result["resolved"] == 2
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_gov_observations_reads_back_sisap_data(index_env, monkeypatch):
    from connectors.gov.adapters import sisap as sisap_module

    async def fake_fetch(self):
        return {region: (_LIMA_TABLE if region == "lima" else _EMPTY_TABLE) for region in sisap_module._REGION_CODES}

    monkeypatch.setattr(sisap_module.SISAPConnector, "fetch", fake_fetch)
    await index_env.collect_gov_sisap()

    arroz = index_env.gov_observations(commodity_slug="arroz")
    assert len(arroz) >= 1
    assert arroz[0]["region"] == "lima"

    all_observations = index_env.gov_observations()
    assert len(all_observations) == 2


def test_collect_gov_sisap_handles_connector_failure_gracefully(index_env, monkeypatch):
    from connectors.gov.adapters import sisap as sisap_module
    import asyncio

    async def failing_fetch(self):
        raise RuntimeError("SISAP unreachable")

    monkeypatch.setattr(sisap_module.SISAPConnector, "fetch", failing_fetch)

    result = asyncio.run(index_env.collect_gov_sisap())

    assert result["ok"] is False
    assert "error" in result
