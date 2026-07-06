#!/usr/bin/env python3
"""Find stores that fail collector batch (0 prices / low success)."""
import json
import urllib.request

API = "https://cli-market-api.fly.dev"


def main():
    with urllib.request.urlopen(f"{API}/dashboard/data", timeout=120) as r:
        d = json.load(r)

    sh = {x["store"]: x for x in d.get("store_health", [])}
    kpis = d.get("kpis", {})
    print(f"indexed={kpis.get('total_indexed')} stores_indexed={kpis.get('stores_indexed')} catalog={kpis.get('catalog_stores')}")
    print()

    # Worst by success_pct among stores with requests
    rows = sorted(sh.values(), key=lambda x: (x.get("success_pct", 0), -x.get("consecutive_failures", 0)))
    print("=== All store_health (sorted by success_pct) ===")
    for x in rows:
        print(
            f"  {x['store']:22} {x.get('success_pct',0):5.1f}% "
            f"req={x.get('total_requests',0):4} ok={x.get('total_successes',0):4} "
            f"fails={x.get('consecutive_failures',0):3} cov7d={x.get('coverage_7d_pct',0):5.1f}% "
            f"last_ok={str(x.get('last_success',''))[:19]} err={str(x.get('last_error',''))[:19]}"
        )

    # Stores with 0% or no successes in recent pattern
    zero = [x for x in rows if x.get("success_pct", 100) < 50 or x.get("consecutive_failures", 0) >= 3]
    print("\n=== Candidates: success<50% or 3+ consecutive failures ===")
    for x in zero:
        print(f"  {x['store']}: {x.get('success_pct')}% consecutive={x.get('consecutive_failures')}")


if __name__ == "__main__":
    main()