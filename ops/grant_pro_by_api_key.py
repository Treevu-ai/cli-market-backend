#!/usr/bin/env python3
"""Grant Pro tier to the user owning MARKET_API_KEY (ops / smoke tests only)."""

from __future__ import annotations

import os
import sys

from market_core import db_set_subscription, db_validate_api_key, ensure_db_initialized


def main() -> int:
    key = os.getenv("MARKET_API_KEY", "").strip()
    if not key.startswith("sk-"):
        print("Set MARKET_API_KEY=sk-... before running.", file=sys.stderr)
        return 1

    ensure_db_initialized()
    row = db_validate_api_key(key)
    if not row:
        print("API key not found in production DB.", file=sys.stderr)
        return 1

    username = row["username"]
    result = db_set_subscription(username, "pro")
    print(f"Pro activated: {result['username']} (tier={result['tier']})")

    try:
        from market_funnel import record_funnel_event

        record_funnel_event(
            "activated",
            username=username,
            meta={"source": "ops_manual", "reason": "smoke_test_grant"},
            dedupe=True,
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
