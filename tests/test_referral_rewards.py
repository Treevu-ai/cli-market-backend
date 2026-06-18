"""End-to-end test for the referral reward credit on /auth/register.

Uses explicit env-var isolation (MARKET_DATA_DIR + DATABASE_URL) before
any imports, then calls init_db() directly to guarantee table creation.
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

# Must run BEFORE importing market_core / market_server
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="market_ref_test_"))
os.environ["MARKET_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["DATABASE_URL"] = ""

sys.path.insert(0, str(Path(__file__).parent.parent))


def _init_isolated_client():
    """Create a TestClient backed by a fresh, isolated SQLite DB.

    Calls init_db() directly — bypassing the _db_initialized guard entirely —
    and verifies the referral_codes table exists before returning.
    """
    import market_core
    import market_core.market_core as mc

    # Force SQLite mode + fresh init state on both package and inner module
    for mod in (market_core, mc):
        mod.USE_PG = False
        mod._db_initialized = False
        mod.DATA_DIR = _TEST_DATA_DIR
        mod.DB_FILE = _TEST_DATA_DIR / "market.db"

    market_core.init_db()

    # Verify the table exists before handing the client back
    import sqlite3
    conn = sqlite3.connect(str(market_core.DB_FILE))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='referral_codes'"
    ).fetchall()
    conn.close()
    assert tables, "referral_codes table was not created by init_db()"

    from fastapi.testclient import TestClient
    from market_server import app
    return TestClient(app)


def test_referred_signup_credits_referrer_bonus():
    client = _init_isolated_client()

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
    client = _init_isolated_client()

    r = client.post("/auth/register", json={"ref_code": "does-not-exist"})
    assert r.status_code == 200
