"""Shared pytest configuration — single SQLite test DB for all unit tests."""

import os
import tempfile

# Must run before test modules import market_core (conftest loads first).
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="market_test_")
os.environ["MARKET_DATA_DIR"] = _TEST_DATA_DIR
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("MARKET_LEGACY_CHECKOUT", "1")
