#!/usr/bin/env python3
"""Patch collect_prices.py: automotriz queries, force catalog, xray fix."""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "collect_prices.py"
t = p.read_text(encoding="utf-8")

if '"automotriz": 50_000' not in t:
    t = t.replace(
        '    "departamentales": 10_000,\n}',
        '    "departamentales": 10_000,\n    "automotriz": 50_000,\n}',
        1,
    )

automotriz_block = """
    # ═══════════════════════════════════════════════════════════════════════════
    # 🚗 Automotriz (WooCommerce: Xray Chipped PE)
    # ═══════════════════════════════════════════════════════════════════════════
    ("ecu","automotriz"),("chip","automotriz"),("reprogramacion","automotriz"),
    ("diagnostico","automotriz"),("remap","automotriz"),("stage","automotriz"),
    ("performance","automotriz"),("tuning","automotriz"),("obd","automotriz"),
    ("centralita","automotriz"),("mapa","automotriz"),("potencia","automotriz"),
"""

if '("ecu","automotriz")' not in t:
    t = t.replace(
        '    ("notebook","departamentales"),("auriculares","departamentales"),\n]',
        '    ("notebook","departamentales"),("auriculares","departamentales"),\n'
        + automotriz_block
        + "]",
        1,
    )

if "async def force_catalog_stores" not in t:
    t = t.replace(
        "async def run_full_catalog_pg(pool, stores: list[str]) -> int:",
        "async def run_full_catalog_pg(pool, stores: list[str], *, force: bool = False) -> int:",
        1,
    )
    t = t.replace(
        "    if now - _last_catalog_pull < CATALOG_INTERVAL_MINS * 60:\n        return 0",
        "    if not force and now - _last_catalog_pull < CATALOG_INTERVAL_MINS * 60:\n        return 0",
        1,
    )
    insert = '''

async def force_catalog_stores(stores: list[str]) -> dict:
    """Bypass catalog interval and upsert full catalog for given stores."""
    if not USE_PG:
        raise RuntimeError("force_catalog_stores requires PostgreSQL (DATABASE_URL)")
    pool = await get_pool()
    await init_schema()
    total = 0
    per_store: dict[str, int] = {}
    for store in stores:
        n = await collect_full_catalog_pg(pool, store)
        per_store[store] = n
        total += n
        print(f"    📦 {store}: {n:,} products (forced catalog)")
    return {"stores": per_store, "prices_collected": total}
'''
    t = t.replace(
        "# ── Collector core ──────────────────────────────────────────────────────────",
        insert + "\n# ── Collector core ──────────────────────────────────────────────────────────",
        1,
    )

if "queries_for_line" not in t:
    t = t.replace(
        "    line = _store_line(store)\n    collected = 0",
        "    line = _store_line(store)\n"
        "    queries_for_line = sum(1 for _q, lf in queries if not lf or lf == line)\n"
        "    if queries_for_line == 0:\n"
        '        logger.info("store %s: no seed queries for line=%s — skipping", store, line)\n'
        "        return 0\n"
        "    collected = 0",
        1,
    )

if "--catalog-store" not in t:
    t = t.replace(
        '    ap.add_argument("--parallel", type=int, default=50)\n    args = ap.parse_args()',
        '    ap.add_argument("--parallel", type=int, default=50)\n'
        '    ap.add_argument("--catalog-store", action="append", default=[], metavar="STORE",\n'
        '                    help="Force full catalog pull for store(s); bypasses 60-min interval")\n'
        "    args = ap.parse_args()",
        1,
    )
    t = t.replace(
        "    if args.report: do_report(); return\n    stores = get_default_stores()",
        "    if args.report: do_report(); return\n"
        "    if args.catalog_store:\n"
        "        if not USE_PG:\n"
        '            print("✗ --catalog-store requires PostgreSQL (DATABASE_URL)")\n'
        "            return\n"
        "        r = await force_catalog_stores(args.catalog_store)\n"
        '        print(f"  ✓ Forced catalog: {r[\'prices_collected\']:,} prices across {len(r[\'stores\'])} store(s)")\n'
        '        for sk, n in r["stores"].items():\n'
        '            print(f"    {sk}: {n:,}")\n'
        "        do_status()\n"
        "        return\n"
        "    stores = get_default_stores()",
        1,
    )

p.write_text(t, encoding="utf-8")
print("patched collect_prices.py")