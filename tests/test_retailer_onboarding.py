"""Retailer application approval → store_credentials."""

from __future__ import annotations

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
    return market_core, data_dir


@pytest.fixture
def onboarding_db(isolated_db, monkeypatch):
    import market_core
    import retailer_onboarding
    import store_credentials

    market_core.ensure_db_initialized()
    store_credentials.invalidate_credential_cache()
    yield
    store_credentials.invalidate_credential_cache()


def test_apply_stores_secret_separately(onboarding_db, monkeypatch):
    import market_core
    from retailer_onboarding import db_get_retailer_application, token_hint

    db = market_core.get_db()
    db.execute(
        """
        INSERT INTO retailer_applications
            (id, store_name, platform, country, contact_email, website,
             api_token, api_token_hint, status)
        VALUES ('RET-TEST01', 'Falabella Test', 'magento', 'PE', 'ops@test.com',
                'https://www.falabella.com.pe', 'secret-magento-token-xyz', ?, 'pending')
        """,
        (token_hint("secret-magento-token-xyz"),),
    )
    db.commit()
    db.close()

    row = db_get_retailer_application("RET-TEST01")
    assert row["api_token"] == "secret-magento-token-xyz"
    assert row["api_token_hint"].endswith("xyz") or row["api_token_hint"] == "****"


def test_approve_moves_token_to_store_credentials(onboarding_db):
    import market_core
    import store_credentials
    from retailer_onboarding import approve_retailer_application, db_get_retailer_application, token_hint

    db = market_core.get_db()
    db.execute(
        """
        INSERT INTO retailer_applications
            (id, store_name, platform, country, contact_email, website,
             api_token, api_token_hint, status)
        VALUES ('RET-TEST02', 'Falabella PE', 'magento', 'PE', 'ops@test.com',
                'https://www.falabella.com.pe', 'magento-integration-token', ?, 'pending')
        """,
        (token_hint("magento-integration-token"),),
    )
    db.commit()
    db.close()

    result = approve_retailer_application("RET-TEST02", store_id="falabella_pe")
    assert result["store_id"] == "falabella_pe"
    assert "magento_token" in result["credentials_fields"]

    store_credentials.invalidate_credential_cache()
    assert "falabella_pe" in store_credentials.get_default_stores()
    cfg = store_credentials.resolve_store_config("falabella_pe")
    assert cfg["magento_token"] == "magento-integration-token"

    app = db_get_retailer_application("RET-TEST02")
    assert app["status"] == "approved"
    assert app["store_id"] == "falabella_pe"


def test_guess_store_id_from_website(onboarding_db):
    from retailer_onboarding import guess_store_id

    assert guess_store_id("https://www.falabella.com.pe/tienda", "magento", "PE") == "falabella_pe"
    assert guess_store_id("https://www.wong.pe", "vtex", "PE") == "wong"


def test_admin_approve_endpoint(onboarding_db, monkeypatch):
    import market_core
    from fastapi.testclient import TestClient
    import market_server
    from retailer_onboarding import token_hint

    monkeypatch.setenv("MARKET_API_TOKEN", "ops-admin-token")
    import server_deps
    monkeypatch.setattr(server_deps, "DEFAULT_TOKEN", "ops-admin-token")
    market_core.db_save_user("admin", market_server.hash_password("market"), "test-token-123")

    db = market_core.get_db()
    db.execute(
        """
        INSERT INTO retailer_applications
            (id, store_name, platform, country, contact_email, website,
             api_token, api_token_hint, status)
        VALUES ('RET-API01', 'Gymshark', 'shopify', 'US', 'dev@gym.com',
                'https://www.gymshark.com', 'shpat_test_token_value', ?, 'pending')
        """,
        (token_hint("shpat_test_token_value"),),
    )
    db.commit()
    db.close()

    client = TestClient(market_server.app)
    r = client.post(
        "/admin/retailer-applications/RET-API01/approve",
        headers={"Authorization": "Bearer ops-admin-token"},
        json={"store_id": "gymshark"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["store_id"] == "gymshark"
    assert body["active_catalog_size"] >= 1
