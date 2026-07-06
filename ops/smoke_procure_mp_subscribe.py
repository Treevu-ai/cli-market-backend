#!/usr/bin/env python3
"""Smoke test for Procure MP checkout + webhook activation (sandbox).

Usage:
  export MARKET_API_KEY=sk-...
  export PROCURE_MP_WEBHOOK_SECRET=...   # optional: test webhook signature
  py ops/smoke_procure_mp_subscribe.py [--activate]

Steps:
  1. POST /billing/procure-subscribe with payment_method=mercadopago
     -> expect ok=True + checkout_url from MP sandbox
  2. (optional --activate) POST /billing/mp-webhook with a synthetic
     payment_approved event for the PCS- ref
     -> expect tier procure_starter activated

Without --activate the test only validates checkout creation.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("MARKET_API_BASE", "https://cli-market-api.fly.dev")
EMAIL = os.getenv("SMOKE_EMAIL", "procure-smoke@cli-market.dev")


def req(
    method: str,
    path: str,
    body: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
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


def authed_req(
    method: str, path: str, body: dict | None = None, api_key: str = ""
) -> tuple[int, dict]:
    return req(method, path, body, {"Authorization": f"Bearer {api_key}"})


def _mp_webhook_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate", action="store_true", help="Also simulate webhook activation")
    parser.add_argument("--plan", default="starter", choices=["starter", "pro", "builder"])
    args = parser.parse_args()

    key = os.getenv("MARKET_API_KEY", "").strip()
    if not key.startswith("sk-"):
        print("ERROR: set MARKET_API_KEY=sk-... first.", file=sys.stderr)
        return 1

    failures: list[str] = []
    plan = args.plan
    prefix_map = {"starter": "PCS", "pro": "PCP", "builder": "PCB"}
    prefix = prefix_map[plan]

    print(f"\n=== Procure MP Subscribe smoke — plan={plan} ===\n")

    # ── Step 1: create checkout ───────────────────────────────────────────────
    status, data = authed_req(
        "POST",
        "/billing/procure-subscribe",
        {
            "plan": plan,
            "email": EMAIL,
            "payment_method": "mercadopago",
            "lang": "es",
        },
        api_key=key,
    )
    ok = status == 200 and data.get("ok") and data.get("checkout_url")
    print(f"POST /billing/procure-subscribe [{plan}/mercadopago] -> {status}")
    if ok:
        request_id = data.get("request_id", "")
        print(f"  request_id  : {request_id}")
        print(f"  checkout_url: {data.get('checkout_url', '')[:80]}...")
        print(f"  amount_pen  : S/ {data.get('amount_pen')}")
        print(f"  amount_usd  : ${data.get('amount_usd')}")
        if not request_id.startswith(f"{prefix}-"):
            print(f"  ERROR: expected {prefix}- prefix, got {request_id}")
            failures.append(f"bad-ref-prefix:{request_id}")
            return _report(failures)
        print(f"  ref prefix  : {prefix} ✓")
    else:
        print(json.dumps(data, indent=2))
        failures.append(f"procure-subscribe:{plan}")
        return _report(failures)

    # ── Step 2 (optional): simulate webhook ──────────────────────────────────
    if args.activate:
        # Use a real MP payment_id if provided — the server fetches the payment
        # from MP to confirm approval, so a fake id won't activate anything in prod.
        # Set SMOKE_MP_PAYMENT_ID to a real sandbox payment id to test full flow.
        mp_payment_id = os.getenv("SMOKE_MP_PAYMENT_ID", "").strip() or f"smoke-{int(time.time())}"
        if mp_payment_id.startswith("smoke-"):
            print(f"\nWARN: using synthetic payment id {mp_payment_id!r}.")
            print("Set SMOKE_MP_PAYMENT_ID=<real sandbox id> to test full server activation.\n")
        print(f"--- Simulating MP webhook for {request_id} (payment_id={mp_payment_id}) ---")
        request_id_header = f"smoke-rid-{int(time.time())}"
        webhook_body = json.dumps(
            {
                "type": "payment",
                "data": {"id": mp_payment_id},
                "action": "payment.created",
                "external_reference": f"CLI-Market-{request_id}",
                "status": "approved",
            }
        ).encode()

        webhook_secret = os.getenv("PROCURE_MP_WEBHOOK_SECRET", "").strip()
        wh_headers: dict = {"Content-Type": "application/json", "x-request-id": request_id_header}
        if webhook_secret:
            sig = _mp_webhook_signature(webhook_body, webhook_secret)
            wh_headers["x-signature"] = f"sha256={sig}"

        r2 = urllib.request.Request(
            f"{BASE}/billing/mp-webhook",
            data=webhook_body,
            headers=wh_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(r2, timeout=30) as resp:
                wstatus = resp.status
                wdata = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            wstatus = e.code
            wdata = {"detail": e.read().decode()[:500]}

        print(f"POST /billing/mp-webhook -> {wstatus}")
        print(json.dumps(wdata, indent=2))

        actions = wdata.get("actions", [])
        tier_activated = any("activated" in str(a) for a in actions)
        if not tier_activated:
            print("WARN: tier activation not confirmed in webhook response — check server logs")
            failures.append("webhook-activation-unconfirmed")
        else:
            print(f"  tier activated: ✓ ({[a for a in actions if 'activated' in str(a)]})")

    return _report(failures)


def _report(failures: list[str]) -> int:
    if failures:
        print(f"\nFAILED: {', '.join(failures)}")
        return 1
    print("\nAll Procure MP smoke checks passed ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
