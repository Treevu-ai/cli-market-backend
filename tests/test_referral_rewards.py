"""End-to-end test for the referral reward credit on /auth/register."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from market_core import ensure_db_initialized
from market_server import app

ensure_db_initialized()
client = TestClient(app)


def test_referred_signup_credits_referrer_bonus():
    referrer = client.post("/auth/register")
    assert referrer.status_code == 200
    referrer_key = referrer.json()["api_key"]
    headers = {"Authorization": f"Bearer {referrer_key}"}

    code = f"ref-{uuid.uuid4().hex[:8]}"
    claim = client.post("/auth/referral", json={"ref_code": code}, headers=headers)
    assert claim.status_code == 200

    before = client.get("/auth/subscription", headers=headers).json()["subscription"]

    referred = client.post("/auth/register", json={"ref_code": code})
    assert referred.status_code == 200

    after = client.get("/auth/subscription", headers=headers).json()["subscription"]
    assert after["req_limit_day"] == before["req_limit_day"] + 500


def test_register_with_unknown_ref_code_is_a_noop():
    r = client.post("/auth/register", json={"ref_code": "does-not-exist"})
    assert r.status_code == 200
