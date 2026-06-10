#!/usr/bin/env python3
"""Print founding Pro seat availability (ops / spike dashboard)."""

from __future__ import annotations

from market_billing import (
    FOUNDING_PROMO_CODE,
    FOUNDING_SEAT_LIMIT,
    founding_seats_remaining,
    db_count_promo_redemptions,
)
from market_core import ensure_db_initialized


def main() -> int:
    ensure_db_initialized()
    used = db_count_promo_redemptions(FOUNDING_PROMO_CODE)
    remaining = founding_seats_remaining()
    print(f"promo={FOUNDING_PROMO_CODE} limit={FOUNDING_SEAT_LIMIT} used={used} remaining={remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
