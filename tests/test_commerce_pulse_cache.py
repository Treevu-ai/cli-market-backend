"""Tests for the shared commerce_pulse_cache table.

Root issue this fixes: /intelligence read from a per-process in-memory dict,
so each of cli-market-api's multiple Fly machines had its own independent
cache -- any visitor could land on a cold machine and block on a live pulse
computation. This table is the single shared source of truth every machine
reads from; the collector daemon is the only writer.
"""

from __future__ import annotations

import pytest

from commerce_pulse_cache import (
    SUPPORTED_COUNTRIES,
    SUPPORTED_LANGS,
    ensure_commerce_pulse_cache_table,
    read_pulse_cache,
    refresh_all,
    write_pulse_cache,
)
from market_core import get_db


def test_read_missing_key_returns_none(isolated_db):
    db = get_db()
    try:
        ensure_commerce_pulse_cache_table(db)
        assert read_pulse_cache(db, "PE", "es") is None
    finally:
        db.close()


def test_write_then_read_round_trip(isolated_db):
    db = get_db()
    try:
        ensure_commerce_pulse_cache_table(db)
        pulse = {"country": "PE", "headline": "Test headline", "kpis": {"pvi": 12.3}}
        write_pulse_cache(db, "PE", "es", pulse)
        db.commit()

        result = read_pulse_cache(db, "PE", "es")
        assert result is not None
        assert result["country"] == "PE"
        assert result["headline"] == "Test headline"
        assert result["kpis"]["pvi"] == 12.3
        assert "_cache_computed_at" in result
    finally:
        db.close()


def test_write_upserts_not_duplicates(isolated_db):
    db = get_db()
    try:
        ensure_commerce_pulse_cache_table(db)
        write_pulse_cache(db, "PE", "es", {"headline": "first"})
        write_pulse_cache(db, "PE", "es", {"headline": "second"})
        db.commit()

        result = read_pulse_cache(db, "PE", "es")
        assert result["headline"] == "second"

        row_count = db.execute(
            "SELECT COUNT(*) as n FROM commerce_pulse_cache WHERE country = ?", ("PE",)
        ).fetchone()
        assert row_count["n"] == 1
    finally:
        db.close()


def test_cache_key_is_case_insensitive_and_scoped_by_lang(isolated_db):
    db = get_db()
    try:
        ensure_commerce_pulse_cache_table(db)
        write_pulse_cache(db, "pe", "ES", {"headline": "spanish"})
        write_pulse_cache(db, "PE", "en", {"headline": "english"})
        db.commit()

        assert read_pulse_cache(db, "PE", "es")["headline"] == "spanish"
        assert read_pulse_cache(db, "PE", "en")["headline"] == "english"
    finally:
        db.close()


def test_refresh_all_writes_every_supported_combo(isolated_db, monkeypatch):
    def fake_generate(*, country, days, lang, dashboard):
        return {"country": country, "lang": lang, "headline": f"{country}-{lang}", "brief": {"big": "blob"}}

    monkeypatch.setattr("market_pulse.generate_commerce_pulse", fake_generate)
    monkeypatch.setattr("routers.dashboard.get_cached_dashboard_data", lambda: {})

    result = refresh_all()
    assert result["errors"] == 0
    assert result["written"] == len(SUPPORTED_COUNTRIES) * len(SUPPORTED_LANGS)

    db = get_db()
    try:
        cached = read_pulse_cache(db, "MX", "en")
        assert cached["headline"] == "MX-en"
        assert "brief" not in cached  # stripped before persisting, matches _load_pulse's own behavior
    finally:
        db.close()


def test_refresh_all_continues_past_individual_country_errors(isolated_db, monkeypatch):
    def flaky_generate(*, country, days, lang, dashboard):
        if country == "AR":
            raise RuntimeError("simulated failure")
        return {"country": country, "lang": lang}

    monkeypatch.setattr("market_pulse.generate_commerce_pulse", flaky_generate)
    monkeypatch.setattr("routers.dashboard.get_cached_dashboard_data", lambda: {})

    result = refresh_all()
    assert result["errors"] == len(SUPPORTED_LANGS)  # AR fails for both langs
    assert result["written"] == (len(SUPPORTED_COUNTRIES) - 1) * len(SUPPORTED_LANGS)
