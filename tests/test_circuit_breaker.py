"""Tests for collector circuit breaker (CB class) and persistent skip logic."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ── CB class unit tests ───────────────────────────────────────────────────────

def _make_cb(threshold: int = 3, cooldown: int = 300):
    """Create a fresh CB instance with explicit thresholds (avoids global state)."""
    import collect_prices as cp
    original_threshold = cp.CB_FAIL_THRESHOLD
    original_cooldown = cp.CB_COOLDOWN
    cp.CB_FAIL_THRESHOLD = threshold
    cp.CB_COOLDOWN = cooldown
    from collections import defaultdict
    cb = cp.CB()
    cp.CB_FAIL_THRESHOLD = original_threshold
    cp.CB_COOLDOWN = original_cooldown
    return cb, threshold, cooldown


def test_cb_ok_initially():
    import collect_prices as cp
    cb = cp.CB()
    assert cb.ok("some_store") is True


def test_cb_trips_after_threshold():
    import collect_prices as cp
    original = cp.CB_FAIL_THRESHOLD
    cp.CB_FAIL_THRESHOLD = 3
    try:
        cb = cp.CB()
        for _ in range(3):
            cb.lose("broken_store")
        assert cb.ok("broken_store") is False
    finally:
        cp.CB_FAIL_THRESHOLD = original


def test_cb_does_not_trip_below_threshold():
    import collect_prices as cp
    original = cp.CB_FAIL_THRESHOLD
    cp.CB_FAIL_THRESHOLD = 3
    try:
        cb = cp.CB()
        for _ in range(2):
            cb.lose("marginal_store")
        assert cb.ok("marginal_store") is True
    finally:
        cp.CB_FAIL_THRESHOLD = original


def test_cb_win_resets_failure_count():
    import collect_prices as cp
    original = cp.CB_FAIL_THRESHOLD
    cp.CB_FAIL_THRESHOLD = 3
    try:
        cb = cp.CB()
        cb.lose("store_a")
        cb.lose("store_a")
        cb.win("store_a")   # reset failures
        cb.lose("store_a")
        cb.lose("store_a")
        assert cb.ok("store_a") is True  # only 2 fails since win, threshold=3
    finally:
        cp.CB_FAIL_THRESHOLD = original


def test_cb_cooldown_expires():
    import collect_prices as cp
    original_threshold = cp.CB_FAIL_THRESHOLD
    original_cooldown = cp.CB_COOLDOWN
    cp.CB_FAIL_THRESHOLD = 1
    cp.CB_COOLDOWN = 1  # 1-second cooldown; sleep past it to verify recovery
    try:
        cb = cp.CB()
        cb.lose("temp_store")
        assert cb.ok("temp_store") is False  # open
        time.sleep(1.1)
        assert cb.ok("temp_store") is True   # cooldown expired
    finally:
        cp.CB_FAIL_THRESHOLD = original_threshold
        cp.CB_COOLDOWN = original_cooldown


def test_cb_reset_clears_all_state():
    import collect_prices as cp
    original = cp.CB_FAIL_THRESHOLD
    cp.CB_FAIL_THRESHOLD = 1
    try:
        cb = cp.CB()
        cb.lose("store_x")
        cb.lose("store_y")
        cb.reset()
        assert cb.ok("store_x") is True
        assert cb.ok("store_y") is True
    finally:
        cp.CB_FAIL_THRESHOLD = original


def test_cb_threshold_default_is_low():
    """Default threshold should be much lower than 50 (old value) — currently 3."""
    import collect_prices as cp
    assert cp.CB_FAIL_THRESHOLD <= 5, (
        f"CB_FAIL_THRESHOLD={cp.CB_FAIL_THRESHOLD} is too high; "
        "stores with <50 queries would never trip the circuit"
    )


# ── persistent skip: _sq_consecutive_failures ────────────────────────────────

def test_sq_consecutive_failures_returns_zero_when_no_row(isolated_db):
    from market_core import get_db
    import collect_prices as cp
    db = get_db()
    db.execute(
        "CREATE TABLE IF NOT EXISTS store_health "
        "(store TEXT PRIMARY KEY, consecutive_failures INTEGER DEFAULT 0, total_requests INTEGER DEFAULT 0)"
    )
    db.commit()
    result = cp._sq_consecutive_failures(db, "nonexistent_store")
    db.close()
    assert result == 0


def test_sq_consecutive_failures_reads_from_store_health(isolated_db):
    from market_core import get_db
    import collect_prices as cp
    db = get_db()
    db.execute(
        "CREATE TABLE IF NOT EXISTS store_health "
        "(store TEXT PRIMARY KEY, consecutive_failures INTEGER DEFAULT 0, total_requests INTEGER DEFAULT 0)"
    )
    db.commit()
    db.execute(
        "INSERT INTO store_health (store, consecutive_failures, total_requests) VALUES (?, ?, ?)",
        ("broken_store", 15, 20),
    )
    db.commit()
    result = cp._sq_consecutive_failures(db, "broken_store")
    db.close()
    assert result == 15


def test_sq_consecutive_failures_returns_zero_on_db_error(isolated_db):
    import collect_prices as cp
    bad_db = MagicMock()
    bad_db.execute.side_effect = Exception("table missing")
    result = cp._sq_consecutive_failures(bad_db, "any_store")
    assert result == 0
