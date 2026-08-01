"""Regression coverage for credential-handling hardening."""

from fastapi.testclient import TestClient

from market_server import app
from server_deps import hash_password, verify_password


def test_password_hash_is_versioned_and_verifiable(monkeypatch):
    monkeypatch.setenv("MARKET_PASSWORD_HASH_ITERATIONS", "100000")

    stored = hash_password("correct horse battery staple")

    assert stored.startswith("pbkdf2_sha256$100000$")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong", stored)


def test_empty_user_store_requires_explicit_admin_password(monkeypatch):
    import routers.auth as auth

    monkeypatch.delenv("MARKET_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(auth, "db_get_users", lambda: {})

    response = TestClient(app).post(
        "/auth/login", json={"username": "admin", "password": "market"}
    )

    assert response.status_code == 503
