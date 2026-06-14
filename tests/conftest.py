"""Shared pytest fixtures for backend tests."""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from services.index_service import IndexService as _IndexService  # noqa: F401
    _INDEX_AVAILABLE = True
except ImportError:
    _INDEX_AVAILABLE = False


def pytest_collection_modifyitems(items: list) -> None:
    if _INDEX_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="cli-market-index not installed — skipping index tests")
    index_files = {"test_index_api.py", "test_index_gate.py"}
    for item in items:
        if Path(item.fspath).name in index_files:
            item.add_marker(skip)


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """SQLite test DB with market_core state reset (package + implementation module)."""
    import market_core
    import market_core.market_core as mc

    data_dir = tmp_path / "market_data"
    data_dir.mkdir()
    db_file = data_dir / "market.db"
    monkeypatch.setenv("MARKET_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", "")

    for mod in (mc, market_core):
        monkeypatch.setattr(mod, "DATA_DIR", data_dir, raising=False)
        monkeypatch.setattr(mod, "DB_FILE", db_file, raising=False)
        monkeypatch.setattr(mod, "USE_PG", False, raising=False)
        monkeypatch.setattr(mod, "_db_initialized", False, raising=False)

    return market_core