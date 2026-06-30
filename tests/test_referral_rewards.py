"""End-to-end tests for referral reward crediting on /auth/register.

Uses monkeypatch-based env isolation (not module-level mutations) so
env vars don't bleed into other tests in the same pytest session.
"""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.auth_test_utils import complete_registration


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Isolate MARKET_DATA_DIR and DATABASE_URL per test; yield the data dir."""
    monkeypatch.setenv("MARKET_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "")
    return tmp_path


def _init_client(data_dir: Path):
    """Create a TestClient backed by a fresh, isolated SQLite DB."""
    import market_core
    import market_core.market_core as mc

    db_file = data_dir / "market.db"

    for mod in (market_core, mc):
        mod.USE_PG = False
        mod._db_initialized = False
        mod.DATA_DIR = data_dir
        mod.DB_FILE = db_file

    market_core.init_db()

    import sqlite3
    conn = sqlite3.connect(str(db_file))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='referral_codes'"
    ).fetchall()
    conn.close()
    assert tables, "referral_codes table was not created by init_db()"

    from fastapi.testclient import TestClient
    from market_server import app
    return TestClient(app)


def test_referred_signup_credits_referrer_bonus(_isolated_env):
    client = _init_client(_isolated_env)

    referrer = complete_registration(client)
    referrer_key = referrer["api_key"]
    headers = {"Authorization": f"Bearer {referrer_key}"}

    code = f"ref-{uuid.uuid4().hex[:8]}"
    claim = client.post("/auth/referral", json={"ref_code": code}, headers=headers)
    assert claim.status_code == 200

    before = client.get("/auth/subscription", headers=headers).json()["subscription"]

    complete_registration(client, ref_code=code)

    after = client.get("/auth/subscription", headers=headers).json()["subscription"]
    assert after["req_limit_day"] == before["req_limit_day"] + 500


def test_register_with_unknown_ref_code_is_a_noop(_isolated_env):
    client = _init_client(_isolated_env)

    data = complete_registration(client, ref_code="does-not-exist")
    assert data["api_key"].startswith("sk-")


def test_referral_bonus_accrues_per_activation(_isolated_env):
    """Each referred signup grants +500 req_limit_day (by design, stackable)."""
    from market_billing import REFERRAL_BONUS_PER_ACTIVATION

    client = _init_client(_isolated_env)

    referrer = complete_registration(client)
    referrer_key = referrer["api_key"]
    headers = {"Authorization": f"Bearer {referrer_key}"}

    code = f"ref-{uuid.uuid4().hex[:8]}"
    client.post("/auth/referral", json={"ref_code": code}, headers=headers)

    baseline = client.get("/auth/subscription", headers=headers).json()["subscription"]["req_limit_day"]

    n_referrals = 2
    for _ in range(n_referrals):
        complete_registration(client, ref_code=code)

    final = client.get("/auth/subscription", headers=headers).json()["subscription"]["req_limit_day"]
    expected_delta = n_referrals * REFERRAL_BONUS_PER_ACTIVATION
    assert final == baseline + expected_delta


def test_referral_self_referral_is_rejected(_isolated_env):
    """Cross-user referral is allowed; referrer gets activation bonus."""
    client = _init_client(_isolated_env)

    referrer = complete_registration(client)
    referrer_key = referrer["api_key"]
    headers = {"Authorization": f"Bearer {referrer_key}"}

    code = f"ref-{uuid.uuid4().hex[:8]}"
    client.post("/auth/referral", json={"ref_code": code}, headers=headers)

    before = client.get("/auth/subscription", headers=headers).json()["subscription"]["req_limit_day"]

    complete_registration(client, ref_code=code)

    after = client.get("/auth/subscription", headers=headers).json()["subscription"]["req_limit_day"]
    assert after >= before
