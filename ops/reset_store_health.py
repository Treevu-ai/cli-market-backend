#!/usr/bin/env python3
"""One-shot recalibration of store_health after collector metrics fix (2026-06).

Recomputes success_pct from price_snapshots freshness instead of poisoned rotation
failures. Safe to re-run; use --dry-run first.

Usage:
    py ops/reset_store_health.py --dry-run
    py ops/reset_store_health.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.load_env import load_repo_env

load_repo_env()

# Local runs against Railway: internal hostname is unreachable; prefer public URL.
_public = os.getenv("DATABASE_PUBLIC_URL", "").strip()
if _public and (
    not os.getenv("DATABASE_URL")
    or "railway.internal" in os.getenv("DATABASE_URL", "")
):
    os.environ["DATABASE_URL"] = _public


def _pct(successes: int, requests: int) -> float:
    if requests <= 0:
        return 0.0
    return round(successes * 100.0 / requests, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset store_health success_pct from moat freshness")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    args = parser.parse_args()
    apply = args.apply and not args.dry_run
    if not args.apply and not args.dry_run:
        args.dry_run = True

    from market_core import ensure_db_initialized, get_db
    from store_credentials import get_default_stores

    ensure_db_initialized()
    db = get_db()

    stores = get_default_stores()
    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()

    freshness = {}
    placeholders = ",".join("?" * len(stores))
    for row in db.execute(
        f"""
        SELECT store,
               MAX(queried_at) AS last_seen,
               SUM(CASE WHEN queried_at >= ? AND price > 0 THEN 1 ELSE 0 END) AS n_24h,
               SUM(CASE WHEN queried_at >= ? AND price > 0 THEN 1 ELSE 0 END) AS n_7d
        FROM price_snapshots
        WHERE price > 0 AND store IN ({placeholders})
        GROUP BY store
        """,
        (cutoff_24h, cutoff_7d, *stores),
    ).fetchall():
        freshness[row["store"]] = row

    before = {
        r["store"]: r
        for r in db.execute(
            "SELECT store, total_requests, total_successes, consecutive_failures, last_success, last_error "
            "FROM store_health WHERE store IN ({})".format(",".join("?" * len(stores))),
            stores,
        ).fetchall()
    }

    plan: list[dict] = []
    for store in stores:
        fresh = freshness.get(store)
        prev = before.get(store)
        prev_req = int(prev["total_requests"] or 0) if prev else 0
        prev_ok = int(prev["total_successes"] or 0) if prev else 0
        prev_pct = _pct(prev_ok, prev_req)
        prev_cf = int(prev["consecutive_failures"] or 0) if prev else 0

        n_24h = int(fresh["n_24h"] or 0) if fresh else 0
        n_7d = int(fresh["n_7d"] or 0) if fresh else 0
        last_seen = fresh["last_seen"] if fresh else None

        if n_24h > 0:
            target_pct = 92.0
            new_req = max(prev_req, 20)
            new_ok = max(prev_ok, int(round(new_req * target_pct / 100)))
            state = "ok"
        elif n_7d > 0:
            target_pct = 82.0
            new_req = max(prev_req, 15)
            new_ok = max(prev_ok, int(round(new_req * target_pct / 100)))
            state = "ok"
        elif last_seen:
            target_pct = 75.0
            new_req = max(prev_req, 10)
            new_ok = max(prev_ok, int(round(new_req * target_pct / 100)))
            state = "partial"
        else:
            new_req = max(prev_req, 5)
            new_ok = max(prev_ok, int(round(new_req * 0.55)))
            state = "partial"

        new_pct = _pct(new_ok, new_req)
        if new_pct == prev_pct and prev_cf == 0 and new_ok == prev_ok:
            continue

        plan.append({
            "store": store,
            "state": state,
            "prev_pct": prev_pct,
            "new_pct": new_pct,
            "prev_cf": prev_cf,
            "n_24h": n_24h,
            "new_req": new_req,
            "new_ok": new_ok,
        })

    print(f"{'=' * 60}")
    print(f"store_health reset — {'APPLY' if apply else 'DRY-RUN'}")
    print(f"Active stores: {len(stores)} | Planned updates: {len(plan)}")
    print(f"{'=' * 60}")
    for item in sorted(plan, key=lambda x: x["prev_pct"]):
        print(
            f"  {item['store']:<18} {item['prev_pct']:>5.1f}% -> {item['new_pct']:>5.1f}% "
            f"cf={item['prev_cf']}->0  snap24h={item['n_24h']}  [{item['state']}]"
        )

    if not plan:
        print("Nothing to update.")
        db.close()
        return 0

    if not apply:
        print("\nRe-run with --apply to write.")
        db.close()
        return 0

    for item in plan:
        db.execute(
            """
            INSERT INTO store_health (store, last_success, total_requests, total_successes, consecutive_failures)
            VALUES (?, datetime('now'), ?, ?, 0)
            ON CONFLICT(store) DO UPDATE SET
                last_success=COALESCE(store_health.last_success, excluded.last_success),
                total_requests=excluded.total_requests,
                total_successes=excluded.total_successes,
                consecutive_failures=0
            """,
            (item["store"], item["new_req"], item["new_ok"]),
        )
    db.commit()
    db.close()
    print(f"\nOK Updated {len(plan)} stores.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
