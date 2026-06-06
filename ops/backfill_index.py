#!/usr/bin/env python3
"""Backfill canonical_product_id on price_snapshots via cli-market-index."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_index")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Golden Record IDs on price_snapshots")
    parser.add_argument("--limit", type=int, default=None, help="Max rows per batch")
    parser.add_argument("--batches", type=int, default=1, help="Number of batches to run")
    parser.add_argument("--dry-run", action="store_true", help="Resolve only; do not UPDATE rows")
    parser.add_argument("--verbose", action="store_true", help="DEBUG logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    limit = args.limit
    if limit is None:
        limit = int(os.getenv("INDEX_BACKFILL_LIMIT", "1000"))

    from index_gate import backfill_canonical_product_ids, index_stats

    before = index_stats()
    log.info(
        "start: registry=%d linked=%d unlinked=%d linkage=%.1f%%",
        before.get("registry_size", 0),
        before.get("snapshots_linked", 0),
        before.get("unlinked_snapshots", 0),
        before.get("linkage_pct", 0),
    )

    total = {"resolved": 0, "linked": 0, "skipped": 0, "errors": 0}
    t0 = time.monotonic()

    for batch in range(args.batches):
        stats = backfill_canonical_product_ids(limit=limit, dry_run=args.dry_run)
        log.info(
            "batch %d/%d: resolved=%d linked=%d skipped=%d errors=%d",
            batch + 1,
            args.batches,
            stats.get("resolved", 0),
            stats.get("linked", 0),
            stats.get("skipped", 0),
            stats.get("errors", 0),
        )
        for key in total:
            total[key] += stats.get(key, 0)
        if stats.get("resolved", 0) == 0:
            log.info("no more unlinked rows in window — stopping early")
            break

    after = index_stats()
    elapsed = time.monotonic() - t0
    log.info(
        "done in %.1fs: resolved=%d linked=%d errors=%d | linkage %.1f%% → %.1f%%",
        elapsed,
        total["resolved"],
        total["linked"],
        total["errors"],
        before.get("linkage_pct", 0),
        after.get("linkage_pct", 0),
    )
    return 0 if total["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())