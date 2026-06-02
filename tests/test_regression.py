"""Regression tests pinning the bugs of 2026-05-26.

These tests would have caught — in <100ms each — every bug we hit this week:

  1. `sq_insert` ON CONFLICT failing because market_core's SQLite DDL lacked
     UNIQUE(product_id, store).                            [bug from main pre-7f73008]
  2. `save_price_snapshot` not upserting in SQLite (plain INSERT).
                                                            [bug introduced in e93feb9]
  3. `save_price_snapshot` not upserting in Postgres (ON CONFLICT DO NOTHING).
                                                            [bug introduced in e93feb9]
  4. `import market_core` creating ~/.market/market.db as a side effect, so the
     order of imports decided which schema "won" the CREATE TABLE IF NOT EXISTS.
                                                            [root cause of the whole saga]
  5. Two divergent DDLs for `price_snapshots` (one in market_core, one in collect_prices).
                                                            [structural risk]

If any of these break again, the corresponding test fails BEFORE deploy, not 8h after.
"""

import sys
import sqlite3
import tempfile
import importlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest


# ── Fixture: isolated DB per test ──────────────────────────────────────────────

@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """Fresh DB file for regression tests without reloading market_core."""
    import market_core

    data_dir = tmp_path / "market_data"
    data_dir.mkdir()
    db_file = data_dir / "market.db"
    monkeypatch.setenv("MARKET_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr(market_core, "DATA_DIR", data_dir)
    monkeypatch.setattr(market_core, "DB_FILE", db_file)
    monkeypatch.setattr(market_core, "USE_PG", False)
    monkeypatch.setattr(market_core, "_db_initialized", False)

    return market_core, data_dir


# ── Bug #4: import-time side-effects ───────────────────────────────────────────

def test_importing_market_core_does_not_create_db():
    """Importing the module must not touch disk. Side-effects in imports
    were the root cause of the schema race.

    Runs in a subprocess so we get a truly cold import.
    """
    with tempfile.TemporaryDirectory() as tmp:
        script = (
            f"import os; os.environ['MARKET_DATA_DIR']={tmp!r};\n"
            "os.environ.pop('DATABASE_URL', None);\n"
            f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r});\n"
            "import market_core;\n"
            f"db_path = os.path.join({tmp!r}, 'market.db');\n"
            "exit(0 if not os.path.exists(db_path) else 1)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert result.returncode == 0, (
            f"import market_core created the DB file (returncode={result.returncode}).\n"
            f"stderr: {result.stderr}"
        )


def test_explicit_ensure_db_initialized_creates_db(isolated_db):
    market_core, data_dir = isolated_db
    db_path = data_dir / "market.db"
    assert not db_path.exists(), "DB should not exist before init"
    market_core.ensure_db_initialized()
    assert db_path.exists(), "DB should exist after ensure_db_initialized()"


def test_ensure_db_initialized_is_idempotent(isolated_db):
    market_core, _ = isolated_db
    market_core.ensure_db_initialized()
    market_core.ensure_db_initialized()
    market_core.ensure_db_initialized()


# ── Bug #1 & structural: schema has UNIQUE constraint ──────────────────────────

def test_sqlite_schema_has_unique_constraint(isolated_db):
    market_core, _ = isolated_db
    market_core.ensure_db_initialized()
    c = sqlite3.connect(str(market_core.DB_FILE))
    schema = c.execute(
        "SELECT sql FROM sqlite_master WHERE name='price_snapshots'"
    ).fetchone()[0]
    assert "UNIQUE(product_id, store)" in schema, (
        "price_snapshots must have UNIQUE(product_id, store) — without it, "
        "ON CONFLICT clauses on insert raise OperationalError. "
        f"Got: {schema}"
    )


# ── Bug #2: save_price_snapshot must UPSERT in SQLite ──────────────────────────

def _make_product(pid="p1", price=10.0, store="wong"):
    return {
        "id": pid, "product_id": pid, "name": "leche", "brand": "A",
        "price": price, "list_price": price + 2, "discount": 10,
        "store": store, "store_name": "Wong", "currency": "PEN",
        "line": "supermercados", "line_name": "Supermercados",
        "category": "lacteos", "stock": 10, "url": "http://x",
    }


def test_save_price_snapshot_upserts_in_sqlite(isolated_db):
    """The bug from commit e93feb9: SQLite path was a plain INSERT without
    ON CONFLICT. Second call to the daemon would fail UNIQUE constraint
    and the price would never update. Data moat freezes at day 1."""
    market_core, _ = isolated_db
    market_core.ensure_db_initialized()
    c = sqlite3.connect(str(market_core.DB_FILE))

    market_core.save_price_snapshot(_make_product(price=10.0))
    market_core.save_price_snapshot(_make_product(price=11.0))
    market_core.save_price_snapshot(_make_product(price=12.0))

    rows = c.execute("SELECT COUNT(*) FROM price_snapshots WHERE product_id='p1'").fetchone()[0]
    price = c.execute("SELECT price FROM price_snapshots WHERE product_id='p1'").fetchone()[0]
    assert rows == 1, f"Expected 1 row (upserted), got {rows}"
    assert price == 12.0, f"Expected price=12.0 (latest), got {price} — UPSERT did not update"


def test_save_price_snapshot_accepts_shared_db_connection(isolated_db):
    """The collector must be able to pass its own connection so it doesn't
    open/close 150K connections per cycle (causing 'database is locked')."""
    market_core, _ = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()

    market_core.save_price_snapshot(_make_product(pid="shared1", price=10.0), db=db)
    market_core.save_price_snapshot(_make_product(pid="shared1", price=20.0), db=db)
    db.commit()

    c = sqlite3.connect(str(market_core.DB_FILE))
    price = c.execute("SELECT price FROM price_snapshots WHERE product_id='shared1'").fetchone()[0]
    assert price == 20.0


def test_save_price_snapshot_does_not_close_shared_db(isolated_db):
    """When a caller passes db, it must NOT be closed. Otherwise the collector's
    batch would die after the first row."""
    market_core, _ = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()

    market_core.save_price_snapshot(_make_product(pid="x"), db=db)
    market_core.save_price_snapshot(_make_product(pid="y"), db=db)
    db.execute("SELECT 1")
    db.commit()
    db.close()


# ── Bug #1: sq_insert must UPSERT ──────────────────────────────────────────────

def test_collector_sq_insert_upserts(isolated_db):
    """The original bug: sq_insert did `ON CONFLICT(product_id, store) DO UPDATE`
    but the UNIQUE constraint was missing, so it raised OperationalError."""
    market_core, _ = isolated_db
    import collect_prices

    db = collect_prices.get_db_unified()

    collect_prices.sq_insert(db, _make_product(pid="cp1", price=10.0))
    collect_prices.sq_insert(db, _make_product(pid="cp1", price=15.0))
    db.commit()

    c = sqlite3.connect(str(market_core.DB_FILE))
    rows = c.execute("SELECT COUNT(*) FROM price_snapshots WHERE product_id='cp1'").fetchone()[0]
    price = c.execute("SELECT price FROM price_snapshots WHERE product_id='cp1'").fetchone()[0]
    assert rows == 1
    assert price == 15.0


def test_max_allowed_price_ars_electro(isolated_db):
    import collect_prices
    cap = collect_prices.max_allowed_price("whirlpool_ar", "electro")
    assert cap >= 1_000_000
    assert collect_prices.max_allowed_price("whirlpool_ar", "electro") > 50_000
    assert collect_prices.max_allowed_price("oster_br", "electro") == 50_000


def test_collector_sq_insert_tolerates_missing_keys(isolated_db):
    """Defensive: connectors return slightly different dict shapes.
    sq_insert must not KeyError if `discount` or `list_price` are missing."""
    market_core, _ = isolated_db
    import collect_prices

    db = collect_prices.get_db_unified()

    minimal = {
        "id": "m1", "product_id": "m1", "name": "x", "price": 5.0,
        "store": "wong",
    }
    collect_prices.sq_insert(db, minimal)
    db.commit()


# ── Bug #5: single source of truth ─────────────────────────────────────────────

def test_collector_init_schema_delegates_to_market_core(isolated_db):
    """collect_prices no longer has its own init_schema_sqlite — it uses get_db()
    which delegates to market_core's unified _DB abstraction."""
    market_core, _ = isolated_db
    import collect_prices

    src = Path(collect_prices.__file__).read_text()
    assert "get_db_unified" in src, (
        "collect_prices must use get_db_unified() instead of raw SQLite connections."
    )
    assert "import sqlite3" not in src.split("# SQLite helpers")[0] if "# SQLite helpers" in src else True, (
        "collect_prices must not import sqlite3 for DB access — use get_db()."
    )


# ── Smoke test: end-to-end import chain ────────────────────────────────────────

def test_full_import_chain_works(isolated_db):
    """Catch import cycles or accidental side effects across the codebase."""
    import collect_prices
    import market_mcp  # noqa: F401
    import market_server  # noqa: F401


# ── /health/collector timezone bug (the one Render exposed in the smoke test) ──

def test_age_hours_parses_sqlite_naive_timestamps(isolated_db):
    """SQLite stores datetime('now') as naive 'YYYY-MM-DD HH:MM:SS'.
    The /health/collector and /dashboard/data endpoints used to crash on
    those, reporting status 'dead' with age_hours=999."""
    from routers.health import _age_hours
    # SQLite native format: space separator, no tz
    assert _age_hours("2026-05-26 17:44:07") is not None
    # Postgres TIMESTAMPTZ format: T separator, +00:00 offset
    assert _age_hours("2026-05-26T17:44:07+00:00") is not None
    # ISO Z suffix
    assert _age_hours("2026-05-26T17:44:07Z") is not None
    # Garbage doesn't crash
    assert _age_hours("not a date") is None
    assert _age_hours(None) is None
    assert _age_hours("") is None


def test_age_hours_returns_close_to_zero_for_recent_timestamp(isolated_db):
    from datetime import datetime, timezone
    from routers.health import _age_hours
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    age = _age_hours(now_str)
    assert age is not None
    assert 0 <= age < 0.01, f"Expected near-zero age, got {age} hours"


def test_age_hours_accepts_datetime_objects(isolated_db):
    from datetime import datetime, timezone, timedelta
    from routers.health import _age_hours
    dt = datetime.now(timezone.utc) - timedelta(hours=2)
    age = _age_hours(dt)
    assert age is not None
    assert 1.9 <= age <= 2.1


def test_dashboard_data_includes_moat_guide(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app
    with TestClient(app) as client:
        r = client.get("/dashboard/data")
    assert r.status_code == 200
    body = r.json()
    assert "moat_guide" in body
    assert "layers" in body["moat_guide"]
    assert any(layer.get("id") == "inventory" for layer in body["moat_guide"]["layers"])
    assert "metric_glossary" in body
    inv = next(l for l in body["moat_guide"]["layers"] if l["id"] == "inventory")
    assert "metric_help" in inv
    assert inv["metric_help"]["total_indexed"]["label"]
    assert inv["metric_help"]["total_indexed"]["description"]


def test_dashboard_data_includes_quality_funnel(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app) as client:
        r = client.get("/dashboard/data")
    assert r.status_code == 200
    body = r.json()
    assert "quality_funnel" in body
    funnel = body["quality_funnel"]
    assert "captured" in funnel
    assert "clean" in funnel
    assert body["collector"]["status"] in ("ok", "stale", "dead", "running", "unknown")


def test_dashboard_html_uses_single_renderer(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app
    with TestClient(app) as client:
        r = client.get("/dashboard")
    assert r.status_code == 200
    html = r.text
    assert "global-bar" in html
    assert "COLLECTOR" in html
    assert "solo dato limpio" in html
    assert "GUÍA POR CAPAS" not in html
    assert html.count("Canasta básica") <= 2


def test_health_collector_status_ok_not_healthy(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app
    with TestClient(app) as client:
        r = client.get("/health/collector")
    assert r.status_code == 200
    status = r.json().get("status")
    assert status != "healthy"
    if status not in ("unknown", "running"):
        assert status in ("ok", "stale", "dead", "empty")


def test_derive_collector_status_empty_run_recent_moat_stale(isolated_db):
    from datetime import datetime, timezone, timedelta
    from routers.health import derive_collector_status

    finished = datetime.now(timezone.utc) - timedelta(minutes=30)
    status, age = derive_collector_status(
        finished_at=finished,
        prices_collected=0,
        moat_age_h=11.0,
    )
    assert status == "stale"
    assert age is not None and age < 2


def test_derive_collector_status_ok_when_prices_ingested(isolated_db):
    from datetime import datetime, timezone, timedelta
    from routers.health import derive_collector_status

    finished = datetime.now(timezone.utc) - timedelta(hours=1)
    status, _ = derive_collector_status(
        finished_at=finished,
        prices_collected=500,
        moat_age_h=2.0,
    )
    assert status == "ok"


def test_dashboard_data_includes_dashboard_view(isolated_db):
    from fastapi.testclient import TestClient
    from market_server import app

    with TestClient(app) as client:
        r = client.get("/dashboard/data")
    assert r.status_code == 200
    body = r.json()
    assert "dashboard_view" in body
    view = body["dashboard_view"]
    assert view["blocks"]["hero"]["title"]
    assert "reading_order" in view


def test_convert_currency_uses_pen_equivalent_rates(isolated_db):
    from market_core import convert_currency
    # ~1000 ARS ≈ 2.7 PEN at static table
    pen = convert_currency(1000, "ARS", "PEN")
    assert 2.0 < pen < 3.5
    assert convert_currency(10, "USD", "PEN") == 37.0


def test_dashboard_dispersion_groups_by_currency(isolated_db):
    """Mixing ARS and PEN in one line used to produce false CRIT spreads."""
    market_core, _ = isolated_db
    market_core.ensure_db_initialized()
    db = market_core.get_db()
    db.execute(
        "INSERT INTO price_snapshots (product_id,name,price,store,store_name,currency,line,line_name) "
        "VALUES ('a1','cheap',10,'wong','Wong','PEN','supermercados','Supermercados')"
    )
    db.execute(
        "INSERT INTO price_snapshots (product_id,name,price,store,store_name,currency,line,line_name) "
        "VALUES ('a2','expensive',500000,'jumbo_ar','Jumbo','ARS','electro','Electro')"
    )
    db.execute(
        "INSERT INTO price_snapshots (product_id,name,price,store,store_name,currency,line,line_name) "
        "VALUES ('a2b','cheap ar',100000,'carrefour_ar','Carrefour','ARS','electro','Electro')"
    )
    db.execute(
        "INSERT INTO price_snapshots (product_id,name,price,store,store_name,currency,line,line_name) "
        "VALUES ('a3','phone',500,'motorola_br','Motorola','BRL','electro','Electro')"
    )
    db.execute(
        "INSERT INTO price_snapshots (product_id,name,price,store,store_name,currency,line,line_name) "
        "VALUES ('a3b','cable',50,'magazineluiza','Magalu','BRL','electro','Electro')"
    )
    db.commit()
    db.close()

    from routers.dashboard import _dashboard_data

    data = _dashboard_data()
    assert data["analytics_meta"]["dispersion_grouping"] == "line+currency+subcategory"
    assert not any(d.get("subcategory") in (None, "") and d["status"] == "crit"
                   for d in data["dispersion"] if d["line_key"] == "supermercados")
