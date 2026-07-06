#!/usr/bin/env python3
"""Production smoke test for pricing/billing endpoints (expect HTTP 200)."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API = "https://cli-market-api.fly.dev"
EMAIL = "billing-e2e@cli-market.dev"


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {"detail": str(e)}
        except json.JSONDecodeError:
            payload = {"detail": raw or str(e)}
        return e.code, payload


def main() -> int:
    failures: list[str] = []

    status, data = req("GET", "/billing/pricing-stats")
    print(f"GET /billing/pricing-stats -> {status}")
    print(json.dumps(data, indent=2))
    if status != 200:
        failures.append("pricing-stats")
    else:
        for key in ("founding_seats_remaining", "founding_available", "founding_promo_code"):
            if key not in data:
                failures.append(f"pricing-stats missing {key}")

    plans = [
        ("starter", {"plan": "starter", "email": EMAIL, "payment_method": "paypal", "lang": "es"}),
        ("pro", {"plan": "pro", "email": EMAIL, "payment_method": "paypal", "lang": "es"}),
        (
            "pro_founding",
            {
                "plan": "pro_founding",
                "promo_code": "founding100",
                "email": EMAIL,
                "payment_method": "paypal",
                "lang": "es",
            },
        ),
        ("pro_annual", {"plan": "pro_annual", "email": EMAIL, "payment_method": "paypal", "lang": "es"}),
    ]

    for name, body in plans:
        status, data = req("POST", "/billing/build-checkout", body)
        ok = status == 200 and data.get("ok") and data.get("approve_url")
        print(f"POST /billing/build-checkout [{name}] -> {status} ok={data.get('ok')} approve={bool(data.get('approve_url'))}")
        if not ok:
            print(json.dumps(data, indent=2))
            failures.append(f"build-checkout:{name}")
        else:
            print(f"  plan_slug={data.get('plan_slug')} amount={data.get('amount')}")

    status, data = req("POST", "/billing/starter-subscribe", {"email": EMAIL, "lang": "es"})
    ok = status == 200 and data.get("ok") and data.get("approve_url")
    print(f"POST /billing/starter-subscribe -> {status} ok={ok}")
    if not ok:
        print(json.dumps(data, indent=2))
        failures.append("starter-subscribe")

    if failures:
        print("\nFAILED:", ", ".join(failures))
        return 1
    print("\nAll billing smoke checks passed (200 + approve_url).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
