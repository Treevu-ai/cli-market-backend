#!/usr/bin/env python3
"""Evaluate WooCommerce integration fit for CLI Market."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = Path(__file__).resolve().parent.parent.parent / "Projects" / "cli-market-core"
for p in (REPO_ROOT, CORE_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

DEFAULT_STORE = {
    "name": "WooCommerce.com (extensions catalog)",
    "base": "https://woocommerce.com",
    "country": "US",
    "currency": "USD",
    "line": "software",
    "platform": "woocommerce",
}


async def evaluate(store_cfg: dict, query: str, limit: int) -> dict:
    from market_connectors.woocommerce import WooCommerceConnector

    connector = WooCommerceConnector()
    store_key = "woo_eval"

    raw_items = await connector.search(store_cfg, query, page=1, limit=limit)
    normalized = [connector.normalize(r, store_key, store_cfg) for r in raw_items]

    index_hits = 0
    index_results = []
    try:
        from services.index_service import IndexService

        index = IndexService()
        for item in normalized[: min(10, len(normalized))]:
            snap = {
                "store": item["store"],
                "sku": item["product_id"],
                "name": item["name"],
                "brand": item.get("brand", ""),
                "price": item.get("price", 0),
                "currency": item.get("currency", "USD"),
                "url": item.get("url", ""),
            }
            result = index.resolve_snapshot(snap)
            ok = result.product is not None and result.confidence > 0
            if ok:
                index_hits += 1
            index_results.append(
                {
                    "name": item["name"][:60],
                    "price": item["price"],
                    "brand": item.get("brand"),
                    "resolved": ok,
                    "match_type": result.match_type,
                    "confidence": round(result.confidence, 2),
                }
            )
    except ImportError:
        index_results = [{"note": "cli-market-index not installed locally"}]

    with_price = sum(1 for n in normalized if n.get("price", 0) > 0)
    with_brand = sum(1 for n in normalized if n.get("brand") not in ("—", "", None))
    with_url = sum(1 for n in normalized if n.get("url"))

    return {
        "store": store_cfg.get("base"),
        "query": query,
        "api": "wc/store/v1 (public Store API)",
        "fetched": len(raw_items),
        "normalized": len(normalized),
        "with_price": with_price,
        "with_brand": with_brand,
        "with_url": with_url,
        "price_coverage_pct": round(with_price / len(normalized) * 100, 1) if normalized else 0,
        "brand_coverage_pct": round(with_brand / len(normalized) * 100, 1) if normalized else 0,
        "index_sample": index_results,
        "index_resolved_sample": index_hits,
        "samples": normalized[:3],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate WooCommerce → CLI Market")
    parser.add_argument("--base", default=DEFAULT_STORE["base"], help="Shop base URL")
    parser.add_argument("--query", default="payment", help="Search term")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cfg = {**DEFAULT_STORE, "base": args.base.rstrip("/")}
    report = asyncio.run(evaluate(cfg, args.query, args.limit))

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"Store:     {report['store']}")
    print(f"API:       {report['api']}")
    print(f"Query:     {report['query']!r}")
    print(f"Fetched:   {report['fetched']} products")
    print(f"Prices:    {report['with_price']}/{report['normalized']} ({report['price_coverage_pct']}%)")
    print(f"Brands:    {report['with_brand']}/{report['normalized']} ({report['brand_coverage_pct']}%)")
    print(f"URLs:      {report['with_url']}/{report['normalized']}")
    if report.get("index_sample"):
        print("\nIndex resolution (sample):")
        for row in report["index_sample"]:
            print(f"  - {row}")
    print("\nSample normalized:")
    for s in report["samples"]:
        print(f"  {s['name'][:50]:50} | {s['price']:>8} {s['currency']} | brand={s.get('brand')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())