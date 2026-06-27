#!/usr/bin/env python3
"""Ops helpers for weekly content sync — dashboard fetch + price pulse export."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from market_pulse import build_price_pulse_markdown  # noqa: E402

API_BASE = os.getenv(
    "MARKET_API_BASE",
    os.getenv("MARKET_API_URL", "https://cli-market-production.up.railway.app"),
).rstrip("/")


def fetch_data() -> dict:
    """Fetch /dashboard/data — local import first, then production HTTP."""
    try:
        from routers.dashboard import get_cached_dashboard_data

        return get_cached_dashboard_data()
    except Exception:
        url = f"{API_BASE}/dashboard/data"
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.loads(resp.read())


def load_store_meta() -> dict:
    """Store metadata for pulse reports (placeholder for legacy call sites)."""
    try:
        from market_core import STORES

        return {
            k: {"country": v.get("country"), "name": v.get("name", k)}
            for k, v in STORES.items()
            if not v.get("disabled")
        }
    except Exception:
        return {}


def build_price_pulse(data: dict, meta: dict | None = None) -> str:
    return build_price_pulse_markdown(data, meta)
