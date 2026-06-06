#!/usr/bin/env python3
"""FMCG pilot metrics for nunaorganica_pe (Woo Store API)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT.parent / "Projects" / "cli-market-core"
for p in (REPO_ROOT, CORE_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

STORE = "nunaorganica_pe"
FMCG_QUERIES = [
    "arroz", "leche", "aceite", "quinoa", "avena", "miel", "cafe",
    "yogur", "pasta", "fideos", "atun", "galleta", "chocolate",
]


async def run(full_catalog: bool) -> dict:
    from market_connectors.woocommerce import WooCommerceConnector
    from market_core import product_from_json, resolve_store_config

    cfg = resolve_store_config(STORE)
    connector = WooCommerceConnector()

    query_hits: dict[str, int] = {}
    query_priced: dict[str, int] = {}
    for q in FMCG_QUERIES:
        raw = await connector.search(cfg, q, limit=10)
        prods = [product_from_json(p, STORE) for p in raw]
        query_hits[q] = len(raw)
        query_priced[q] = sum(1 for p in prods if p.get("price", 0) > 0)

    catalog_size = 0
    brand_coverage = 0.0
    price_coverage = 0.0
    index_linked = 0
    index_sample = 0
    if full_catalog:
        raw_all = await connector.fetch_all_products(cfg, max_pages=10)
        catalog_size = len(raw_all)
        normalized = [connector.normalize(p, STORE, cfg) for p in raw_all]
        with_brand = sum(1 for p in normalized if p.get("brand") not in ("—", "", None))
        with_price = sum(1 for p in normalized if p.get("price", 0) > 0)
        brand_coverage = round(with_brand / catalog_size * 100, 1) if catalog_size else 0.0
        price_coverage = round(with_price / catalog_size * 100, 1) if catalog_size else 0.0
        try:
            from services.index_service import IndexService

            index = IndexService()
            for item in normalized[:50]:
                snap = {
                    "store": STORE,
                    "sku": item["product_id"],
                    "name": item["name"],
                    "brand": item.get("brand", ""),
                    "price": item.get("price", 0),
                    "currency": item.get("currency", "PEN"),
                    "url": item.get("url", ""),
                }
                result = index.resolve_snapshot(snap)
                index_sample += 1
                if result.product is not None and result.confidence > 0:
                    index_linked += 1
        except ImportError:
            pass

    active_queries = sum(1 for q in FMCG_QUERIES if query_hits.get(q, 0) > 0)
    return {
        "store": STORE,
        "base": cfg.get("base"),
        "platform": cfg.get("platform"),
        "fmcg_queries_tested": len(FMCG_QUERIES),
        "fmcg_queries_with_hits": active_queries,
        "query_hits": query_hits,
        "query_priced": query_priced,
        "catalog_products": catalog_size,
        "catalog_price_coverage_pct": price_coverage,
        "catalog_brand_coverage_pct": brand_coverage,
        "index_linkage_sample_pct": round(index_linked / index_sample * 100, 1) if index_sample else None,
        "index_linked_sample": index_linked,
        "index_sample_size": index_sample,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-catalog", action="store_true", help="Paginate Store API catalog")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(run(args.full_catalog))
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(f"Store: {report['base']} ({report['platform']})")
    print(f"FMCG queries with hits: {report['fmcg_queries_with_hits']}/{report['fmcg_queries_tested']}")
    if report["catalog_products"]:
        print(
            f"Catalog: {report['catalog_products']} products | "
            f"prices {report['catalog_price_coverage_pct']}% | "
            f"brands {report['catalog_brand_coverage_pct']}%"
        )
        if report["index_linkage_sample_pct"] is not None:
            print(
                f"Index linkage (sample {report['index_sample_size']}): "
                f"{report['index_linkage_sample_pct']}%"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())