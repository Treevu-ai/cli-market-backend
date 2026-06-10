#!/usr/bin/env python3
"""Smoke test POST /checkout/validate on production.

Usage (do not commit keys):
  set MARKET_API_KEY=sk-...
  py ops/smoke_checkout_validate_prod.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("MARKET_API_BASE", "https://cli-market-production.up.railway.app")


def main() -> int:
    key = os.getenv("MARKET_API_KEY", "").strip()
    if not key.startswith("sk-"):
        print("Set MARKET_API_KEY=sk-... in your shell first.", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        req = urllib.request.Request(BASE + path, method=method, headers=headers)
        if body is not None:
            req.data = json.dumps(body).encode()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"raw": raw[:500]}
            return exc.code, data

    print(f"Base: {BASE}\n")

    code, cart = call("GET", "/cart")
    print(f"GET /cart -> {code} | items={cart.get('items')} total={cart.get('total')}")

    code, empty_val = call("POST", "/checkout/validate")
    print(f"POST /checkout/validate (empty) -> {code} | {empty_val.get('detail', empty_val)}")

    code, search = call("POST", "/products/search", {"query": "leche", "country": "PE", "limit": 3})
    products = search.get("products") or search.get("results") or [] if code == 200 else []
    print(f"POST /products/search -> {code} | hits={len(products)}")
    if not products:
        print("No products to add — stopping before cart checkout test.")
        return 0

    item = products[0]
    pid = str(item.get("product_id") or item.get("id") or "")
    add_body = {
        "product_id": pid,
        "name": item.get("name") or "producto",
        "price": float(item.get("price") or 0),
        "store": item.get("store") or "",
        "quantity": 1,
        "url": item.get("url") or "",
    }
    code, added = call("POST", "/cart/add", add_body)
    print(f"POST /cart/add -> {code} | items={added.get('items')} total={added.get('total')}")

    code, validated = call("POST", "/checkout/validate")
    if code == 200:
        print(f"POST /checkout/validate -> {code} | ok={validated.get('ok')} total={validated.get('validated_total')}")
        for step in validated.get("trace") or []:
            print(f"  trace: {step.get('step')} = {step.get('status')}")
    elif code == 409:
        detail = validated.get("detail") if isinstance(validated.get("detail"), dict) else validated
        print(f"POST /checkout/validate -> {code} | error={detail.get('error')} action={detail.get('action')}")
        for row in detail.get("items") or []:
            print(f"  item {row.get('product_id')}: {row.get('status')} — {row.get('message', '')}")
    else:
        print(f"POST /checkout/validate -> {code} | {validated}")

    code, yape = call("POST", "/checkout/yape")
    if code == 200:
        print(f"POST /checkout/yape -> {code} | order={yape.get('order_id')} total={yape.get('total')}")
    elif code == 409:
        detail = yape.get("detail") if isinstance(yape.get("detail"), dict) else yape
        print(f"POST /checkout/yape -> {code} | blocked error={detail.get('error')}")
    else:
        print(f"POST /checkout/yape -> {code} | {yape.get('detail', yape)}")

    # Leave cart unchanged if checkout succeeded (cart cleared); else keep for user inspection
    code, final_cart = call("GET", "/cart")
    print(f"\nFinal cart -> {code} | items={final_cart.get('items')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
