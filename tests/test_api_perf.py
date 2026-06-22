"""API performance and regression smoke tests."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

API_BASE = os.getenv(
    "MARKET_API_BASE",
    "https://cli-market-production.up.railway.app",
)


def _skip_live_latency_benchmark() -> None:
    """Production latency checks are for local/ops runs only — opt-in."""
    if os.getenv("MARKET_RUN_LIVE") != "1":
        pytest.skip("live API latency benchmarks skipped — set MARKET_RUN_LIVE=1 to enable")


def _post(path: str, body: dict, timeout: int = 30) -> tuple[int, float, dict | list]:
    url = f"{API_BASE.rstrip('/')}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        elapsed_ms = (time.perf_counter() - start) * 1000
        payload = json.loads(resp.read())
        return resp.status, elapsed_ms, payload


def _skip_if_ci() -> None:
    """Skip unless MARKET_RUN_LIVE=1 is explicitly set — these tests hit production.

    Inverted default: opt-in rather than opt-out so that network-restricted
    environments (CI, remote containers, sandboxes) never hit prod by accident.
    """
    if os.getenv("MARKET_RUN_LIVE") != "1":
        pytest.skip("live API tests skipped — set MARKET_RUN_LIVE=1 to enable")


@pytest.mark.integration
def test_api_health():
    _skip_if_ci()
    url = f"{API_BASE.rstrip('/')}/"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())
    assert resp.status == 200
    assert data["status"] == "running"


@pytest.mark.integration
def test_search_regression_contract():
    """Search returns list with expected product fields."""
    _skip_if_ci()
    status, elapsed_ms, data = _post("/products/search", {"query": "leche", "limit": 3})
    assert status == 200
    assert elapsed_ms < 5000, f"search too slow: {elapsed_ms}ms"
    results = data.get("results", data) if isinstance(data, dict) else data
    assert isinstance(results, list)
    if results:
        item = results[0]
        for key in ("name", "price", "store"):
            assert key in item, f"missing {key} in search result"


@pytest.mark.integration
def test_search_p95_under_sla():
    """p95 search latency under 5s (5 samples; local/ops only, not CI)."""
    _skip_live_latency_benchmark()
    latencies = []
    for _ in range(5):
        _, ms, _ = _post("/products/search", {"query": "arroz", "limit": 5})
        latencies.append(ms)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)]
    assert p95 < 5000, f"p95 {p95}ms exceeds 5s live baseline (target SLA 2s)"


@pytest.mark.integration
def test_compare_regression():
    _skip_if_ci()
    status, elapsed_ms, data = _post("/products/compare", {"query": "leche", "limit": 5})
    assert status == 200
    assert elapsed_ms < 8000
    assert isinstance(data, (list, dict))


@pytest.mark.integration
def test_dashboard_data_public():
    _skip_if_ci()
    url = f"{API_BASE.rstrip('/')}/dashboard/data"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read())
    assert "kpis" in data
    assert data["kpis"]["total_stores"] >= 1
