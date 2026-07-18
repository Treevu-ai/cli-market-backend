"""collect_gov_wto() — WTO Peru merchandise exports/imports ingest/read.

Third gov connector wired this session, same shape as test_gov_bcrp.py /
test_gov_sisap.py: POST /admin/cron/gov-wto ingests, GET
/v1/intel/gov-observations reads it back (source-agnostic).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_EXPORTS_PAYLOAD = {
    "Dataset": [
        {
            "IndicatorCode": "ITS_MTV_MX",
            "Indicator": "Total merchandise exports - monthly",
            "ReportingEconomy": "Peru",
            "PeriodCode": "M05",
            "Year": 2026,
            "Unit": "Million US dollar",
            "Value": 11229.0,
        }
    ]
}
_IMPORTS_PAYLOAD = {
    "Dataset": [
        {
            "IndicatorCode": "ITS_MTV_MM",
            "Indicator": "Total merchandise imports - monthly",
            "ReportingEconomy": "Peru",
            "PeriodCode": "M05",
            "Year": 2026,
            "Unit": "Million US dollar",
            "Value": 6174.0,
        }
    ]
}


@pytest.fixture
def index_env(monkeypatch, tmp_path):
    import index_gate as gate

    monkeypatch.setenv("INDEX_PERSISTENCE", "1")
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("WTO_API_SUBSCRIPTION_KEY", "test-key")
    monkeypatch.delenv("INDEX_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    gate._service = None
    yield gate
    gate._service = None


@pytest.mark.asyncio
async def test_collect_gov_wto_resolves_export_and_import_observations(index_env, monkeypatch):
    from connectors.gov.adapters import wto as wto_module

    async def fake_fetch(self):
        return {"ITS_MTV_MX": _EXPORTS_PAYLOAD, "ITS_MTV_MM": _IMPORTS_PAYLOAD}

    monkeypatch.setattr(wto_module.WTOConnector, "fetch", fake_fetch)

    result = await index_env.collect_gov_wto()

    assert result["ok"] is True
    assert result["source"] == "wto_pe"
    assert result["fetched"] == 2
    assert result["resolved"] == 2
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_gov_observations_reads_back_wto_data_without_colliding_with_bcrp_slugs(index_env, monkeypatch):
    from connectors.gov.adapters import wto as wto_module

    async def fake_fetch(self):
        return {"ITS_MTV_MX": _EXPORTS_PAYLOAD, "ITS_MTV_MM": _IMPORTS_PAYLOAD}

    monkeypatch.setattr(wto_module.WTOConnector, "fetch", fake_fetch)
    await index_env.collect_gov_wto()

    wto_exports = index_env.gov_observations(commodity_slug="exportaciones_wto_pe")
    assert len(wto_exports) == 1
    assert wto_exports[0]["price"] == 11229.0

    # Deliberately distinct from BCRP's exportaciones_fob_pe slug (different
    # source/methodology) — this query must not accidentally match it.
    bcrp_exports = index_env.gov_observations(commodity_slug="exportaciones_fob_pe")
    assert bcrp_exports == []


def test_collect_gov_wto_handles_missing_api_key_gracefully(index_env, monkeypatch):
    import asyncio

    monkeypatch.delenv("WTO_API_SUBSCRIPTION_KEY", raising=False)

    result = asyncio.run(index_env.collect_gov_wto())

    assert result["ok"] is False
    assert "WTO_API_SUBSCRIPTION_KEY" in result["error"]
