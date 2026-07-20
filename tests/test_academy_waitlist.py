"""Academy waitlist endpoint — insert, validation, and ops notification."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    import market_core
    import market_core.market_core as mc

    data_dir = tmp_path / "market_data"
    data_dir.mkdir()
    db_file = data_dir / "market.db"
    monkeypatch.setenv("MARKET_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", "")
    for mod in (mc, market_core):
        monkeypatch.setattr(mod, "DATA_DIR", data_dir, raising=False)
        monkeypatch.setattr(mod, "DB_FILE", db_file, raising=False)
        monkeypatch.setattr(mod, "USE_PG", False, raising=False)
        monkeypatch.setattr(mod, "_db_initialized", False, raising=False)
    import market_core as mcore

    mcore.ensure_db_initialized()
    return market_core, data_dir


def _valid_body(**overrides):
    body = {
        "email": "ana@example.com",
        "rol": "Comercial / Ventas",
        "track": "Intelligence",
        "pais": "PE",
        "empresa": "Acme SAC",
    }
    body.update(overrides)
    return body


def test_waitlist_rejects_invalid_email(isolated_db):
    import routers.academy as academy_module

    with pytest.raises(HTTPException) as exc:
        academy_module.academy_waitlist(_valid_body(email="not-an-email"))
    assert exc.value.status_code == 400


def test_waitlist_rejects_missing_rol(isolated_db):
    import routers.academy as academy_module

    with pytest.raises(HTTPException) as exc:
        academy_module.academy_waitlist(_valid_body(rol=""))
    assert exc.value.status_code == 400


def test_waitlist_rejects_invalid_track(isolated_db):
    import routers.academy as academy_module

    with pytest.raises(HTTPException) as exc:
        academy_module.academy_waitlist(_valid_body(track="Bitcoin"))
    assert exc.value.status_code == 400


def test_waitlist_rejects_missing_pais(isolated_db):
    import routers.academy as academy_module

    with pytest.raises(HTTPException) as exc:
        academy_module.academy_waitlist(_valid_body(pais=""))
    assert exc.value.status_code == 400


def test_waitlist_inserts_row_and_notifies_ops(isolated_db):
    import market_core
    import routers.academy as academy_module

    with patch.object(academy_module, "_send_ops_notification", return_value={"sent": True}) as notify:
        result = academy_module.academy_waitlist(_valid_body())

    assert result["registered"] is True
    assert result["ops_notified"] is True
    notify.assert_called_once()

    db = market_core.get_db()
    row = db.execute(
        "SELECT email, rol, track, pais, empresa FROM academy_waitlist WHERE id = ?",
        (result["id"],),
    ).fetchone()
    db.close()
    row = dict(row)
    assert row["email"] == "ana@example.com"
    assert row["track"] == "Intelligence"
    assert row["pais"] == "PE"
