"""Telegram bot webhook — /status and /coverage must reflect the live,
DB-primary retailer catalog (get_all_stores()), not the frozen STORES dict
this file used to import directly (2026-07-24 STORES->DB migration
follow-up)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def telegram_client(isolated_db, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app
    import routers.misc as misc

    monkeypatch.setattr(misc, "TELEGRAM_TOKEN", "test-token")
    monkeypatch.setattr(misc, "_send_telegram", AsyncMock(return_value=True))
    return TestClient(app), misc


def _update(text: str) -> dict:
    return {
        "message": {
            "chat": {"id": 12345, "first_name": "Test"},
            "text": text,
        }
    }


def test_status_command_reflects_db_primary_store_count(telegram_client):
    client, misc = telegram_client

    with patch(
        "store_credentials.get_all_stores",
        return_value={"a": {"line": "supermercados"}, "b": {"line": "farmacias"}},
    ):
        r = client.post("/telegram/webhook", json=_update("/status"))
        assert r.status_code == 200, r.text
        misc._send_telegram.assert_awaited_once()
        _chat_id, reply = misc._send_telegram.call_args.args
        assert "2 retailers" in reply


def test_coverage_command_counts_from_get_all_stores(telegram_client):
    client, misc = telegram_client

    with patch(
        "store_credentials.get_all_stores",
        return_value={
            "a": {"line": "supermercados"},
            "b": {"line": "supermercados"},
            "c": {"line": "farmacias"},
        },
    ):
        r = client.post("/telegram/webhook", json=_update("/coverage"))
        assert r.status_code == 200, r.text
        misc._send_telegram.assert_awaited_once()
        _chat_id, reply = misc._send_telegram.call_args.args
        assert "supermercados" in reply.lower() or "Supermercados" in reply
