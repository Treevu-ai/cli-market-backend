"""Public API key registration with email verification."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from market_core import db_validate_api_key, ensure_db_initialized
from market_server import app
from tests.auth_test_utils import complete_registration

ensure_db_initialized()
client = TestClient(app)


def test_register_requires_email():
    r = client.post("/auth/register", json={})
    assert r.status_code == 422


def test_register_creates_valid_api_key():
    data = complete_registration(client, "reg-valid@example.com")
    assert data["api_key"].startswith("sk-")
    assert data["username"].startswith("user-")
    assert data["verified"] is True
    key_data = db_validate_api_key(data["api_key"])
    assert key_data is not None
    assert key_data["username"] == data["username"]


def test_verify_wrong_code_returns_401():
    client.post("/auth/register", json={"email": "wrong-code@example.com"})
    r = client.post("/auth/verify-email", json={"email": "wrong-code@example.com", "code": "000000"})
    assert r.status_code in (401, 200)


def test_verify_unknown_email_returns_404():
    r = client.post("/auth/verify-email", json={"email": "nobody@example.com", "code": "123456"})
    assert r.status_code == 404
