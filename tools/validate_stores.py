#!/usr/bin/env python3
"""Validate VTEX stores against the live public catalog API.

Standalone diagnostic — no project imports, only `httpx`. Reproduces the
direct-HTTP path of market_connectors/vtex.py:VtexConnector.search():

    GET {base}{io}/api/catalog_system/pub/products/search/{term}?_from=0&_to=19

For each store it auto-detects the VTEX IO path, fires a handful of seed
queries for that store's line, and reports a verdict:

    WORKS   — at least one query returned priced products
    EMPTY   — API reachable (200 JSON) but no products for any query
              (likely wrong query set for this catalog, not a failure)
    BLOCKED — non-200 / non-JSON (Cloudflare, geofence, auth required)
    ERROR   — network/exception

Run from a machine with open outbound network (the CI sandbox blocks
retailer domains):

    pip install httpx
    python3 tools/validate_stores.py
    python3 tools/validate_stores.py --store oster_br --verbose

Exit code is non-zero if any store is BLOCKED or ERROR, so it can gate CI.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# The 7 never-validated stores (name, base, line, country). Mirrors
# market_stores.STORES — kept inline so the script stands alone.
STORES: dict[str, dict] = {
    "aramis_br":    {"name": "Aramis",         "base": "https://www.aramis.com.br",                          "line": "moda",            "country": "BR"},
    "decathlon_br": {"name": "Decathlon BR",   "base": "https://decathlonstore.vtexcommercestable.com.br",  "line": "moda",            "country": "BR"},
    "farmatodo_mx": {"name": "Farmatodo MX",   "base": "https://www.farmatodo.com.mx",                       "line": "farmacias",       "country": "MX"},
    "globo_br":     {"name": "Drogaria Globo", "base": "https://www.drogariaglobo.com.br",                   "line": "farmacias",       "country": "BR"},
    "miess_br":     {"name": "Miess",          "base": "https://www.miess.com.br",                           "line": "moda",            "country": "BR"},
    "oster_br":     {"name": "Oster BR",       "base": "https://www.oster.com.br",                           "line": "electro",         "country": "BR"},
    "rihappy_br":   {"name": "Ri Happy",       "base": "https://www.rihappy.com.br",                         "line": "departamentales", "country": "BR"},
}

# A compact, representative seed set per line (subset of SEED_QUERIES in
# collect_prices.py). Includes PT/EN terms since all 7 are BR/MX VTEX stores.
LINE_QUERIES: dict[str, list[str]] = {
    "moda": ["camiseta", "tenis", "jaqueta", "calça", "vestido", "bermuda",
             "tshirt", "shoes", "shorts", "sapato"],
    "farmacias": ["paracetamol", "dipirona", "vitamina c", "shampoo", "protetor solar",
                  "alcohol", "fralda", "sabonete", "ibuprofeno", "omeprazol"],
    "electro": ["liquidificador", "batedeira", "ferro", "panela", "cafeteira",
                "ventilador", "blender", "mixer", "torradeira", "grill"],
    "departamentales": ["brinquedo", "boneca", "lego", "jogo", "bicicleta",
                        "carrinho", "pelúcia", "quebra cabeça", "toy", "patins"],
}


async def detect_io(client: httpx.AsyncClient, base: str) -> str:
    """Return '' or '/io' depending on which catalog tree endpoint answers JSON."""
    for io in ("", "/io"):
        try:
            r = await client.get(
                f"{base}{io}/api/catalog_system/pub/category/tree/10",
                headers={"User-Agent": UA},
                timeout=10.0,
            )
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                return io
        except Exception:
            continue
    return ""


async def search(client: httpx.AsyncClient, base: str, io: str, term: str) -> tuple[str, list]:
    """Return (status, products). status in {ok, empty, blocked, error}."""
    url = f"{base}{io}/api/catalog_system/pub/products/search/{term}"
    try:
        r = await client.get(
            url, params={"_from": "0", "_to": "19"},
            headers={"User-Agent": UA}, timeout=15.0,
        )
        if r.status_code == 429:
            await asyncio.sleep(2.0)
            r = await client.get(
                url, params={"_from": "0", "_to": "19"},
                headers={"User-Agent": UA}, timeout=15.0,
            )
        if r.status_code in (200, 206) and "json" in r.headers.get("content-type", ""):
            data = r.json()
            return ("ok", data) if isinstance(data, list) and data else ("empty", [])
        return (f"blocked:{r.status_code}", [])
    except Exception as exc:
        return (f"error:{type(exc).__name__}", [])


def first_price(products: list) -> str:
    for p in products:
        items = p.get("items", [])
        if not items:
            continue
        sellers = items[0].get("sellers", [])
        if not sellers:
            continue
        offer = sellers[0].get("commertialOffer", {})
        price = offer.get("Price")
        if price:
            name = (p.get("productName") or "")[:48]
            return f"{name} — {price}"
    return "(no priced item)"


async def validate_store(store_id: str, cfg: dict, verbose: bool) -> dict:
    queries = LINE_QUERIES.get(cfg["line"], [])
    async with httpx.AsyncClient(follow_redirects=True) as client:
        io = await detect_io(client, cfg["base"])
        ok = empty = blocked = error = 0
        sample = ""
        details = []
        for term in queries:
            status, products = await search(client, cfg["base"], io, term)
            if status == "ok":
                ok += 1
                if not sample:
                    sample = first_price(products)
            elif status == "empty":
                empty += 1
            elif status.startswith("blocked"):
                blocked += 1
            else:
                error += 1
            details.append(f"    {term:18} {status}" + (f"  → {len(products)}" if products else ""))
            await asyncio.sleep(0.5)

    if ok > 0:
        verdict = "WORKS"
    elif blocked > 0 or error > 0:
        verdict = "BLOCKED" if blocked >= error else "ERROR"
    else:
        verdict = "EMPTY"

    return {
        "store": store_id, "name": cfg["name"], "io": io or "(none)",
        "ok": ok, "empty": empty, "blocked": blocked, "error": error,
        "verdict": verdict, "sample": sample, "details": details,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the 7 unvalidated VTEX stores.")
    ap.add_argument("--store", help="validate only this store id")
    ap.add_argument("--verbose", action="store_true", help="show per-query results")
    args = ap.parse_args()

    targets = {args.store: STORES[args.store]} if args.store else STORES
    if args.store and args.store not in STORES:
        print(f"unknown store '{args.store}'. options: {', '.join(STORES)}")
        return 2

    print(f"Validating {len(targets)} store(s) against live VTEX catalog API\n")
    results = []
    for store_id, cfg in targets.items():
        res = await validate_store(store_id, cfg, args.verbose)
        results.append(res)
        icon = {"WORKS": "✅", "EMPTY": "⚠️ ", "BLOCKED": "⛔", "ERROR": "❌"}[res["verdict"]]
        print(f"{icon} {res['verdict']:8} {store_id:14} io={res['io']:6} "
              f"ok={res['ok']} empty={res['empty']} blocked={res['blocked']} error={res['error']}")
        if res["sample"]:
            print(f"             sample: {res['sample']}")
        if args.verbose:
            print("\n".join(res["details"]))

    works = [r["store"] for r in results if r["verdict"] == "WORKS"]
    empty = [r["store"] for r in results if r["verdict"] == "EMPTY"]
    bad = [r["store"] for r in results if r["verdict"] in ("BLOCKED", "ERROR")]

    print("\n── Summary ──────────────────────────────────────────")
    print(f"WORKS   ({len(works)}): {', '.join(works) or '—'}")
    print(f"EMPTY   ({len(empty)}): {', '.join(empty) or '—'}   (reachable, wrong query set — retune seeds)")
    print(f"BLOCKED ({len(bad)}): {', '.join(bad) or '—'}   (drop from catalog or add credentials/Playwright)")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
