"""WhatsApp (Twilio) bot webhook — shares _bot_command_reply() with the
Telegram bot (see test_telegram_webhook.py) so the two channels can't drift.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

AUTH_TOKEN = "test-twilio-auth-token"
WEBHOOK_URL = "http://testserver/whatsapp/webhook"


def _twilio_signature(url: str, params: dict, auth_token: str = AUTH_TOKEN) -> str:
    data = url
    for key in sorted(params.keys()):
        data += key + str(params[key])
    digest = hmac.new(auth_token.encode(), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


@pytest.fixture
def whatsapp_client(isolated_db, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app
    import routers.misc as misc

    monkeypatch.setattr(misc, "TWILIO_ACCOUNT_SID", "test-sid")
    monkeypatch.setattr(misc, "TWILIO_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setattr(misc, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    monkeypatch.setattr(misc, "WHATSAPP_ALLOWED_NUMBERS", set())
    monkeypatch.setattr(misc, "_send_whatsapp", AsyncMock(return_value=True))
    return TestClient(app), misc


def _post(client, params: dict, *, sign: bool = True, auth_token: str = AUTH_TOKEN):
    headers = {}
    if sign:
        headers["X-Twilio-Signature"] = _twilio_signature(WEBHOOK_URL, params, auth_token)
    return client.post("/whatsapp/webhook", data=params, headers=headers)


def test_disabled_without_twilio_credentials(isolated_db, monkeypatch):
    from fastapi.testclient import TestClient
    from market_server import app
    import routers.misc as misc

    monkeypatch.setattr(misc, "TWILIO_ACCOUNT_SID", "")
    client = TestClient(app)
    r = client.post("/whatsapp/webhook", data={"From": "whatsapp:+51999999999", "Body": "hola"})
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_rejects_invalid_signature(whatsapp_client):
    client, misc = whatsapp_client
    params = {"From": "whatsapp:+51999999999", "Body": "/status", "ProfileName": "Test"}
    r = _post(client, params, sign=False)
    assert r.status_code == 403
    misc._send_whatsapp.assert_not_awaited()


def test_rejects_tampered_body_even_with_a_signature(whatsapp_client):
    client, misc = whatsapp_client
    signed_params = {"From": "whatsapp:+51999999999", "Body": "/status", "ProfileName": "Test"}
    headers = {"X-Twilio-Signature": _twilio_signature(WEBHOOK_URL, signed_params)}
    tampered = dict(signed_params, Body="/coverage")
    r = client.post("/whatsapp/webhook", data=tampered, headers=headers)
    assert r.status_code == 403
    misc._send_whatsapp.assert_not_awaited()


def test_status_command_replies_with_whatsapp_style_bold(whatsapp_client):
    client, misc = whatsapp_client
    params = {"From": "whatsapp:+51999999999", "Body": "/status", "ProfileName": "Test"}

    with patch(
        "store_credentials.get_all_stores",
        return_value={"a": {"line": "supermercados"}, "b": {"line": "farmacias"}},
    ):
        r = _post(client, params)
        assert r.status_code == 200, r.text
        misc._send_whatsapp.assert_awaited_once()
        to_number, reply = misc._send_whatsapp.call_args.args
        assert to_number == "whatsapp:+51999999999"
        assert "2 retailers" in reply
        # shared reply builder still emits <b> markup — the whatsapp sender is
        # responsible for converting it, not the command handler itself.
        assert "<b>" in reply


def test_html_to_whatsapp_converts_bold_markup(whatsapp_client):
    _client, misc = whatsapp_client
    assert misc._html_to_whatsapp("<b>CLI Market</b> is online") == "*CLI Market* is online"


def test_allowed_numbers_gate_blocks_unlisted_sender(whatsapp_client):
    client, misc = whatsapp_client
    misc.WHATSAPP_ALLOWED_NUMBERS.add("+51111111111")
    params = {"From": "whatsapp:+51999999999", "Body": "/status", "ProfileName": "Test"}

    r = _post(client, params)
    assert r.status_code == 200
    assert r.json()["status"] == "not_allowed"
    misc._send_whatsapp.assert_not_awaited()


def test_allowed_numbers_gate_permits_listed_sender(whatsapp_client):
    client, misc = whatsapp_client
    misc.WHATSAPP_ALLOWED_NUMBERS.add("+51999999999")
    params = {"From": "whatsapp:+51999999999", "Body": "/docs", "ProfileName": "Test"}

    r = _post(client, params)
    assert r.status_code == 200, r.text
    misc._send_whatsapp.assert_awaited_once()
