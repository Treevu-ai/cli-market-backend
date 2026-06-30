"""Helpers for email-verified registration in tests."""

from __future__ import annotations

import uuid

from market_core import get_db


def complete_registration(client, email: str | None = None, ref_code: str | None = None) -> dict:
    """Register + verify OTP; returns verify-email JSON with api_key."""
    from routers.auth import _hash_code

    email = email or f"test-{uuid.uuid4().hex[:8]}@example.com"
    body: dict = {"email": email}
    if ref_code:
        body["ref_code"] = ref_code
    r = client.post("/auth/register", json=body)
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "verification_required"

    db = get_db()
    row = db.execute(
        "SELECT code_hash FROM pending_registrations WHERE email=?",
        (email,),
    ).fetchone()
    db.close()
    assert row, "pending registration missing"

    code = None
    for i in range(1_000_000):
        candidate = f"{i:06d}"
        if _hash_code(candidate) == row["code_hash"]:
            code = candidate
            break
    assert code is not None

    v = client.post("/auth/verify-email", json={"email": email, "code": code})
    assert v.status_code == 200, v.text
    return v.json()
