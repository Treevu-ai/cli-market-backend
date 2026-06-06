#!/usr/bin/env python3
"""Investigate collector VTEX batch failures from dashboard JSON or DB."""
import json
import sys
import urllib.request

API = "https://cli-market-production.up.railway.app"


def from_dashboard():
    with urllib.request.urlopen(f"{API}/dashboard/data", timeout=120) as r:
        return json.load(r)


def main():
    d = from_dashboard()
    k = d.get("kpis", {})
    print("=== KPIs ===")
    for key in (
        "total_indexed", "stores_indexed", "catalog_stores",
        "last_collected_at", "store_success_pct", "stores_dead",
        "stores_stale", "healthy_stores", "total_runs",
    ):
        print(f"  {key}: {k.get(key)}")

    sh = d.get("store_health", [])
    print("\n=== Zero success stores ===")
    zero = [x for x in sh if x.get("success_pct", 100) == 0]
    for x in sorted(zero, key=lambda i: i["store"]):
        print(f"  {x['store']}: req={x.get('total_requests')} err={x.get('last_error')}")

    print("\n=== Low success (<50%) ===")
    low = sorted(
        [x for x in sh if 0 < x.get("success_pct", 100) < 50],
        key=lambda i: i.get("success_pct", 0),
    )
    for x in low[:20]:
        print(
            f"  {x['store']}: {x['success_pct']}% "
            f"fails={x.get('consecutive_failures')} "
            f"last_ok={x.get('last_success')}"
        )

    print("\n=== Healthy VTEX sample (success>=80%) ===")
    good = [x for x in sh if x.get("success_pct", 0) >= 80][:8]
    for x in good:
        print(f"  {x['store']}: {x['success_pct']}%")


if __name__ == "__main__":
    main()