#!/usr/bin/env python3
"""Smoke test for Cost of Living OS endpoints.

Usage:
  export MARKET_API_KEY=sk-...
  py ops/smoke_cost_of_living.py [--country PE]

Steps:
  1. GET /v1/intel/affordability?country=PE  → ok + score 0–100 + band
  2. POST /v1/missions/optimize-purchase     → ok + recommendation + action_links
  3. GET /v1/household                       → 200 (may be empty if not set)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("MARKET_API_BASE", "https://cli-market-production.up.railway.app")


def req(method: str, path: str, body: dict | None = None, api_key: str = "") -> tuple[int, dict]:
    h: dict = {"Accept": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {"detail": str(e)}
        except json.JSONDecodeError:
            payload = {"detail": raw[:500]}
        return e.code, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", default="PE", help="Country code (default: PE)")
    args = parser.parse_args()

    key = os.getenv("MARKET_API_KEY", "").strip()
    if not key.startswith("sk-"):
        print("ERROR: set MARKET_API_KEY=sk-... first.", file=sys.stderr)
        return 1

    country = args.country.upper()
    failures: list[str] = []

    print(f"\n=== Cost of Living OS smoke — country={country} ===\n")

    # ── Step 1: affordability score ───────────────────────────────────────────
    status, data = req("GET", f"/v1/intel/affordability?country={country}&days=30", api_key=key)
    body = data.get("data") or data
    score = body.get("affordability_score")
    band = body.get("affordability_band")
    ok = status == 200 and score is not None and band in ("comfortable", "moderate", "strained", "critical")
    print(f"GET /v1/intel/affordability [{country}] -> {status}")
    if ok:
        print(f"  score : {score}")
        print(f"  band  : {band}")
        canasta = (body.get("components") or {}).get("canasta_min")
        if canasta:
            currency = (body.get("components") or {}).get("canasta_currency", "PEN")
            print(f"  canasta mín: {currency} {canasta:.2f}")
        print(f"  headline: {body.get('headline_es') or body.get('headline_en') or '—'}")
    else:
        print(json.dumps(data, indent=2))
        failures.append("affordability")

    # ── Step 2: optimize purchase ─────────────────────────────────────────────
    payload = {
        "country": country,
        "items": [{"name": "leche", "qty": 2}, {"name": "arroz", "qty": 1}],
        "constraints": {"include_tco": True, "allow_substitutes": True, "include_action_links": True},
        "include_intel": False,
    }
    status, data = req("POST", "/v1/missions/optimize-purchase", payload, api_key=key)
    body = data.get("data") or data
    rec = body.get("recommendation") or {}
    ok = status == 200 and body.get("status") == "ok" and rec.get("primary_store")
    print(f"\nPOST /v1/missions/optimize-purchase -> {status}")
    if ok:
        print(f"  action        : {rec.get('action')}")
        print(f"  primary_store : {rec.get('primary_store_name') or rec.get('primary_store')}")
        print(f"  shelf_total   : {rec.get('currency')} {rec.get('shelf_total')}")
        print(f"  tco_total     : {rec.get('currency')} {rec.get('tco_total')}")
        links = body.get("action_links") or []
        print(f"  action_links  : {len(links)} links")
    else:
        print(json.dumps(data, indent=2))
        failures.append("optimize-purchase")

    # ── Step 3: household GET ─────────────────────────────────────────────────
    status, data = req("GET", "/v1/household", api_key=key)
    ok = status in (200, 404)
    print(f"\nGET /v1/household -> {status}")
    if ok:
        if status == 200:
            print(f"  size          : {(data.get('data') or data).get('size')}")
            print(f"  budget_monthly: {(data.get('data') or data).get('budget_monthly')}")
        else:
            print("  (no household profile — expected for new users)")
    else:
        print(json.dumps(data, indent=2))
        failures.append("household-get")

    # ── Step 4: household PUT (upsert) then summary ───────────────────────────
    patch = {
        "size": 2, "country": country, "currency": "PEN",
        "budget_monthly": 800, "budget_period_start_day": 1,
        "restrictions": {}, "default_stores": [], "staple_list": ["leche", "arroz"],
        "goals": ["ahorrar"],
    }
    status, data = req("PUT", "/v1/household", patch, api_key=key)
    ok = status == 200
    print(f"\nPUT /v1/household -> {status}")
    if ok:
        d = data.get("data") or data
        print(f"  size          : {d.get('size')}")
        print(f"  budget_monthly: {d.get('budget_monthly')}")
    else:
        print(json.dumps(data, indent=2))
        failures.append("household-put")

    status, data = req("GET", "/v1/household/summary", api_key=key)
    ok = status == 200 and "suggested_action" in (data.get("data") or data)
    print(f"\nGET /v1/household/summary -> {status}")
    if ok:
        d = data.get("data") or data
        print(f"  suggested_action    : {d.get('suggested_action')}")
        print(f"  budget_remaining    : {d.get('budget_remaining')}")
        print(f"  days_left_in_period : {d.get('days_left_in_period')}")
    else:
        print(json.dumps(data, indent=2))
        failures.append("household-summary")

    # ── Step 5: receipts list ─────────────────────────────────────────────────
    status, data = req("GET", "/v1/receipts?limit=5", api_key=key)
    ok = status == 200 and "receipts" in (data.get("data") or data)
    print(f"\nGET /v1/receipts -> {status}")
    if ok:
        d = data.get("data") or data
        print(f"  count: {d.get('count', 0)} receipts")
    else:
        print(json.dumps(data, indent=2))
        failures.append("receipts-list")

    # ── Report ────────────────────────────────────────────────────────────────
    if failures:
        print(f"\nFAILED: {', '.join(failures)}")
        return 1
    print("\nAll Cost of Living OS smoke checks passed ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
