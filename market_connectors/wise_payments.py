"""
Wise (TransferWise) payment integration for CLI Market.
Sandbox-ready. Requires WISE_API_TOKEN, WISE_PROFILE_ID.
"""

import os, hashlib, hmac, httpx

WISE_API_URL = os.getenv("WISE_API_URL", "https://api.sandbox.transferwise.tech")
WISE_API_TOKEN = os.getenv("WISE_API_TOKEN", "")
WISE_PROFILE_ID = os.getenv("WISE_PROFILE_ID", "")
WISE_WEBHOOK_SECRET = os.getenv("WISE_WEBHOOK_SECRET", "")


async def create_quote(src: str, tgt: str, amount: float) -> dict:
    if not WISE_API_TOKEN: return {"error": "WISE_API_TOKEN not set"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{WISE_API_URL}/v1/quotes",
            json={"sourceCurrency": src, "targetCurrency": tgt, "sourceAmount": amount},
            headers={"Authorization": f"Bearer {WISE_API_TOKEN}"})
        return resp.json() if resp.status_code == 200 else {"error": resp.text}


def verify_signature(body: bytes, sig: str) -> bool:
    if not WISE_WEBHOOK_SECRET: return False
    exp = hmac.new(WISE_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, sig)


async def get_rates(base: str = "PEN") -> dict:
    targets = ["USD", "EUR", "ARS", "BRL", "MXN", "COP", "CLP", "PEN"]
    rates = {}
    for t in targets:
        if t == base: rates[t] = 1.0
        else:
            q = await create_quote(base, t, 100)
            rates[t] = q.get("rate", 1.0) if "rate" in q else 1.0
    return rates
