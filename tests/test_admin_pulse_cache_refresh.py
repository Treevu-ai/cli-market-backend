"""POST /admin/cron/pulse-cache-refresh — lets the collector warm the shared
commerce_pulse_cache table over HTTP instead of importing commerce_pulse_cache
(and its routers.dashboard dependency) directly into the minimal collector image.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_pulse_cache_refresh_requires_admin_token(monkeypatch):
    import server_deps
    from market_server import app

    monkeypatch.setattr(server_deps, "DEFAULT_TOKEN", "secret-token")
    client = TestClient(app)

    response = client.post("/admin/cron/pulse-cache-refresh")
    assert response.status_code == 401


def test_pulse_cache_refresh_calls_refresh_all(monkeypatch):
    import server_deps
    from market_server import app

    monkeypatch.setattr(server_deps, "DEFAULT_TOKEN", "secret-token")
    monkeypatch.setattr(
        "commerce_pulse_cache.refresh_all", lambda: {"written": 12, "errors": 0}
    )

    client = TestClient(app)
    response = client.post(
        "/admin/cron/pulse-cache-refresh",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"written": 12, "errors": 0}
