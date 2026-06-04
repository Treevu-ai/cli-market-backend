#!/usr/bin/env python3
"""
market_core — Shared utilities for CLI Market.

Imports once, used everywhere: server, CLI, MCP server, collector.
Eliminates the 4-way code duplication of api(), product_from_json(),
STORES/LINES, get_token(), and price helpers.
"""

import json
import os
import logging
from pathlib import Path

import httpx

# ── Database backend selection ──────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "")

def _pg_host_reachable(url: str) -> bool:
    """When DATABASE_URL is set, always attempt PostgreSQL.
    
    DNS pre-flight checks are unreliable inside Docker containers
    on Render. Let psycopg2.connect() handle the actual connection
    — market_core.init_db() will fall back to SQLite if it fails.
    """
    return bool(url)

USE_PG = bool(DATABASE_URL) and _pg_host_reachable(DATABASE_URL)

if USE_PG:
    try:
        import psycopg2  # noqa: F401  — availability check; connection lives in market_db
        logger_pg = logging.getLogger("market.pg")
        logger_pg.info("Using PostgreSQL backend")
    except ImportError:
        logging.getLogger("market").error(
            "DATABASE_URL is set but psycopg2 is not installed. "
            "Install: pip install psycopg2-binary. Falling back to SQLite."
        )
        USE_PG = False

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("market")

# ── Paths & config ────────────────────────────────────────────────────────────

API = os.environ.get("MARKET_API_URL", "http://127.0.0.1:8765")
DATA_DIR = Path(os.getenv("MARKET_DATA_DIR", Path.home() / ".market"))
# Ensure writable: fall back to cwd if home is not writable (e.g. serverless)
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    DATA_DIR = Path(os.getenv("MARKET_DATA_DIR", Path.cwd() / ".market"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)

SESSION_FILE = DATA_DIR / "session.json"
LANG_FILE = DATA_DIR / "lang"
LAST_SEARCH_FILE = DATA_DIR / "last_search.json"
USERS_FILE = DATA_DIR / "users.json"
CARTS_FILE = DATA_DIR / "carts.json"
ORDERS_FILE = DATA_DIR / "orders.json"
DB_FILE = DATA_DIR / "market.db"

# ── Stores (VTEX retailers) ───────────────────────────────────────────────────

from market_stores import STORES

LINES = {
    "supermercados":   {"name": "Supermercados",          "emoji": "🛒", "description": "Alimentos, bebidas y consumo diario"},
    "farmacias":       {"name": "Farmacias y Salud",      "emoji": "💊", "description": "Medicamentos, bienestar y cuidado personal"},
    "electro":         {"name": "Electro y Tecnología",   "emoji": "📱", "description": "Electrónicos, electrodomésticos y gadgets"},
    "hogar":           {"name": "Hogar y Construcción",   "emoji": "🏠", "description": "Mejoramiento del hogar, muebles, ferretería"},
    "departamentales": {"name": "Tiendas Departamentales", "emoji": "🏬", "description": "Ropa, hogar, electrónicos y más"},
    "moda":            {"name": "Moda y Vestimenta",      "emoji": "👕", "description": "Ropa, calzado y accesorios"},
}

COUNTRIES: dict[str, dict] = {}
for _sk, _sv in STORES.items():
    _cc = _sv["country"]
    if _cc not in COUNTRIES:
        COUNTRIES[_cc] = {"name": _cc, "stores": []}
    COUNTRIES[_cc]["stores"].append(_sk)
# Human-readable country names
_country_names: dict[str, str] = {
    "PE": "Perú", "AR": "Argentina", "BR": "Brasil", "MX": "México", "CO": "Colombia",
    "CL": "Chile", "ES": "España", "FR": "Francia", "IT": "Italia", "DE": "Alemania",
    "GB": "Reino Unido", "PT": "Portugal", "NL": "Países Bajos", "BE": "Bélgica",
    "PL": "Polonia", "SE": "Suecia", "DK": "Dinamarca", "FI": "Finlandia",
    "NO": "Noruega", "AT": "Austria", "CH": "Suiza", "IE": "Irlanda",
    "GR": "Grecia", "CZ": "República Checa", "RO": "Rumania", "HU": "Hungría",
    "SK": "Eslovaquia", "BG": "Bulgaria", "HR": "Croacia", "SI": "Eslovenia",
    "LU": "Luxemburgo", "EE": "Estonia", "LV": "Letonia", "LT": "Lituania",
    "UY": "Uruguay", "EC": "Ecuador", "BO": "Bolivia", "PY": "Paraguay",
    "VE": "Venezuela", "CR": "Costa Rica", "GT": "Guatemala", "SV": "El Salvador",
    "PA": "Panamá", "DO": "República Dominicana", "HN": "Honduras", "NI": "Nicaragua",
    "US": "Estados Unidos", "CA": "Canadá", "AU": "Australia", "NZ": "Nueva Zelanda",
    "JP": "Japón", "KR": "Corea del Sur", "CN": "China", "TW": "Taiwán",
    "HK": "Hong Kong", "SG": "Singapur", "IN": "India", "MY": "Malasia",
    "TH": "Tailandia", "ID": "Indonesia", "PH": "Filipinas", "VN": "Vietnam",
    "TR": "Turquía", "RU": "Rusia", "AE": "Emiratos Árabes Unidos",
    "ZA": "Sudáfrica", "NG": "Nigeria",
}
for _cc in COUNTRIES:
    COUNTRIES[_cc]["name"] = _country_names.get(_cc, _cc)

from store_credentials import get_default_stores, resolve_store_config  # noqa: F401
PAGE_SIZE = 20

# ── Currency ──────────────────────────────────────────────────────────────────

CURRENCY_SYMBOLS: dict[str, str] = {
    "PEN": "S/", "ARS": "ARS", "BRL": "R$", "MXN": "MXN", "COP": "COP",
    "CLP": "CLP", "EUR": "€", "GBP": "£",
}

# PEN value of 1 unit of each currency (static; live rates: /checkout/rates).
FX_PEN_PER_UNIT: dict[str, float] = {
    "PEN": 1.0,
    "ARS": 0.0027,
    "BRL": 1.02,
    "MXN": 0.29,
    "COP": 0.0013,
    "CLP": 0.0053,
    "EUR": 4.05,
    "USD": 3.70,
}


def convert_currency(amount: float, frm: str, to: str) -> float:
    """Convert amount using static PEN-equivalent rates."""
    src = (frm or "PEN").upper()
    dst = (to or "PEN").upper()
    r_src = FX_PEN_PER_UNIT.get(src)
    r_dst = FX_PEN_PER_UNIT.get(dst)
    if r_src is None or r_dst is None:
        raise ValueError(f"Unsupported currency. Supported: {list(FX_PEN_PER_UNIT)}")
    return round(amount * r_src / r_dst, 6)


def price_to_usd(price: float, currency: str) -> float | None:
    if not price or price <= 0:
        return None
    cur = (currency or "").upper()
    if cur not in FX_PEN_PER_UNIT:
        return None
    return round(convert_currency(price, cur, "USD"), 4)


def fmt_price(price: float, currency: str = "PEN") -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    return f"{symbol} {price:,.2f}"

def store_color(store: str) -> str:
    colors: dict[str, str] = {
        "wong": "#3cffd0", "metro": "#5200ff", "plazavea": "#ffe600",
        "carrefour": "#3cffd0", "jumbo_ar": "#00FF88", "carrefour_br": "#3cffd0",
        "chedraui": "#FF6B35", "heb": "#FF6B35",
        "olimpica": "#60A5FA", "exito": "#60A5FA",
        "drogaraia": "#FF6B35", "drogasil": "#FF6B35",
        "magazineluiza": "#A78BFA", "motorola_br": "#A78BFA",
        "renner": "#FFD600", "centauro": "#4ADE80", "homecenter": "#F5F5F0",
        "carrefour_es": "#FFD600", "decathlon_fr": "#4ADE80",
    }
    return colors.get(store, "#e9e9e9")

def store_emoji(store: str) -> str:
    return STORES.get(store, {}).get("emoji", "📦")

# ── Session / auth helpers ────────────────────────────────────────────────────

_AUTH_PUBLIC_PATHS = {"/", "/auth/login", "/auth/register"}


def save_session(username: str, token: str) -> None:
    """Persist bearer token locally for CLI and MCP clients."""
    if not token:
        return
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps({"username": username, "token": token}, indent=2),
        encoding="utf-8",
    )


def get_token() -> str:
    if not SESSION_FILE.exists():
        return ""
    data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    return data.get("token", "")


def get_session_username() -> str:
    if not SESSION_FILE.exists():
        return ""
    data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    return data.get("username", "")

# ── API client (sync — used by CLI and MCP) ───────────────────────────────────

def api(method: str, path: str, json_data: dict | None = None) -> dict:
    token = None
    if path not in _AUTH_PUBLIC_PATHS:
        token = get_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        if method == "GET":
            resp = httpx.get(f"{API}{path}", headers=headers, timeout=30)
        elif method == "POST":
            resp = httpx.post(f"{API}{path}", headers=headers, json=json_data, timeout=30)
        elif method == "PUT":
            resp = httpx.put(f"{API}{path}", headers=headers, json=json_data, timeout=30)
        elif method == "DELETE":
            resp = httpx.delete(f"{API}{path}", headers=headers, timeout=30)
        else:
            raise ValueError(f"Unknown method: {method}")
        if resp.status_code >= 400:
            detail = resp.json().get("detail", resp.text)
            return {"error": detail, "status": resp.status_code}
        data = resp.json()
        if path == "/auth/login" and data.get("token"):
            save_session(data.get("username", ""), data["token"])
        elif path == "/auth/register":
            key = data.get("api_key") or data.get("key")
            if key:
                save_session(data.get("username", ""), key)
        return data
    except httpx.ConnectError:
        return {"error": "Server not running. Start: python market_server.py"}

# ── Multi-platform store access ────────────────────────────────────────────

async def fetch_store(store: str, term: str, page: int = 1, limit: int = PAGE_SIZE) -> list[dict]:
    """Search a store's catalog API. Platform-agnostic."""
    store_config = resolve_store_config(store)
    platform = store_config.get("platform", "vtex")
    from market_connectors import get_connector
    connector = get_connector(platform)
    return await connector.search(store_config, term, page, limit)

def product_from_json(p: dict, store: str) -> dict:
    """Normalize a product JSON into a flat dict. Platform-agnostic."""
    if not isinstance(p, dict):
        return {"id": "", "name": str(p)[:80], "price": 0, "store": store, "store_name": store, "currency": "USD"}
    store_config = resolve_store_config(store)
    platform = store_config.get("platform", "vtex")
    from market_connectors import get_connector
    connector = get_connector(platform)
    return connector.normalize(p, store, store_config)

# ── Last-search cache (for CLI auto-fill via table #) ─────────────────────────

def save_last_search(results: list[dict]) -> None:
    slim: list[dict] = []
    for p in results[:50]:
        slim.append({
            "product_id": p.get("id", p.get("product_id", "")),
            "name": p.get("name", ""),
            "price": p.get("price", 0),
            "store": p.get("store", ""),
            "store_name": p.get("store_name", ""),
            "currency": p.get("currency", "PEN"),
            "brand": p.get("brand", ""),
        })
    LAST_SEARCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_SEARCH_FILE.write_text(json.dumps(slim))

def load_last_search() -> list[dict]:
    if LAST_SEARCH_FILE.exists():
        try:
            return json.loads(LAST_SEARCH_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []

# ── Database layer (connection + DDL) lives in market_db.py ─────────────────
# State (USE_PG, DATABASE_URL, DB_FILE) and lifecycle (init_db,
# ensure_db_initialized) stay here; the connection abstraction and schema
# definitions are imported from market_db and re-exported for compatibility.
from market_db import (  # noqa: E402, F401
    _DB,
    _PgCursor,
    _migrate_price_snapshots_pg,
    _migrate_price_snapshots_v7,
    _SQLITE_DDL,
    get_db,
    init_db_pg,
    price_snapshots_has_confidence,
)


from market_billing import (  # noqa: E402, F401
    TIERS,
    _migrate_payment_schema,
    db_create_subscription_request,
    db_delete_billing_pending,
    db_find_order_by_gateway_ref,
    db_find_order_by_id,
    db_find_subscription_request,
    db_get_billing_pending,
    db_get_subscription,
    db_mark_subscription_request_activated,
    db_mark_subscription_request_emailed,
    db_recent_subscription_request,
    db_save_billing_pending,
    db_set_order_gateway_ref,
    db_set_subscription,
    db_update_order_status,
    user_can_checkout,
)


def _migrate_store_credentials(db) -> None:
    """Store credentials + retailer application review columns."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS store_credentials (
            store_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            store_name TEXT DEFAULT '',
            base TEXT DEFAULT '',
            country TEXT DEFAULT '',
            currency TEXT DEFAULT '',
            line TEXT DEFAULT 'supermercados',
            magento_token TEXT DEFAULT '',
            storefront_token TEXT DEFAULT '',
            vtex_app_key TEXT DEFAULT '',
            vtex_app_token TEXT DEFAULT '',
            application_id TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_store_cred_active ON store_credentials(active)"
    )
    for col, typedef in (
        ("api_token", "TEXT DEFAULT ''"),
        ("store_id", "TEXT DEFAULT ''"),
        ("reviewed_at", "TEXT"),
        ("review_notes", "TEXT DEFAULT ''"),
    ):
        try:
            db.execute(f"ALTER TABLE retailer_applications ADD COLUMN {col} {typedef}")
        except Exception:
            pass


def _migrate_indicator_schema(db) -> None:
    """Indicator moat tables — safe to run on existing deployments."""
    if USE_PG:
        db.execute("""
            CREATE TABLE IF NOT EXISTS indicator_definitions (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                unit TEXT DEFAULT '',
                refresh_hours INTEGER NOT NULL DEFAULT 24,
                description TEXT DEFAULT '',
                formula TEXT DEFAULT ''
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS indicator_values (
                id SERIAL PRIMARY KEY,
                indicator_key TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                country TEXT DEFAULT '',
                line TEXT DEFAULT '',
                value DOUBLE PRECISION,
                metadata_json TEXT DEFAULT '{}',
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_iv_key_time ON indicator_values(indicator_key, recorded_at DESC)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_iv_scope ON indicator_values(scope, country, line)"
        )
        db.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id SERIAL PRIMARY KEY,
                product_id TEXT NOT NULL,
                store TEXT NOT NULL,
                price DOUBLE PRECISION,
                list_price DOUBLE PRECISION,
                discount INTEGER,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ph_product_store ON price_history(product_id, store, recorded_at DESC)"
        )
        db.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_cache (
                cache_key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}',
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_enrich_cache_at ON enrichment_cache(recorded_at DESC)")
    else:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS indicator_definitions (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                unit TEXT DEFAULT '',
                refresh_hours INTEGER NOT NULL DEFAULT 24,
                description TEXT DEFAULT '',
                formula TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS indicator_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicator_key TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                country TEXT DEFAULT '',
                line TEXT DEFAULT '',
                value REAL,
                metadata_json TEXT DEFAULT '{}',
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_iv_key_time ON indicator_values(indicator_key, recorded_at);
            CREATE INDEX IF NOT EXISTS idx_iv_scope ON indicator_values(scope, country, line);
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                store TEXT NOT NULL,
                price REAL,
                list_price REAL,
                discount INTEGER,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_ph_product_store ON price_history(product_id, store, recorded_at);
            CREATE TABLE IF NOT EXISTS enrichment_cache (
                cache_key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}',
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_enrich_cache_at ON enrichment_cache(recorded_at);
        """)
    try:
        from market_indicators import seed_indicator_definitions

        seed_indicator_definitions(db)
    except Exception as e:
        logger.warning("Indicator definition seed skipped: %s", e)


def append_price_history(db, product_id: str, store: str, price: float, list_price: float, discount) -> None:
    """Append to price_history when price changes vs last recorded point."""
    try:
        row = db.execute(
            "SELECT price FROM price_history WHERE product_id = ? AND store = ? ORDER BY recorded_at DESC LIMIT 1",
            (product_id, store),
        ).fetchone()
        if row and row["price"] is not None and price == row["price"]:
            return
        db.execute(
            """
            INSERT INTO price_history (product_id, store, price, list_price, discount)
            VALUES (?, ?, ?, ?, ?)
            """,
            (product_id, store, price, list_price, discount),
        )
    except Exception as e:
        logger.debug("price_history append skipped: %s", e)


def init_db() -> None:
    db = get_db()
    if USE_PG:
        init_db_pg(db)
        _migrate_indicator_schema(db)
    else:
        db.executescript(_SQLITE_DDL)
        _migrate_payment_schema(db)
        _migrate_store_credentials(db)
        _migrate_price_snapshots_v7(db)
        _migrate_indicator_schema(db)
    db.commit()
    db.close()


# ── CRUD data layer (functions live in market_crud.py) ───────────────────────
# Re-exported here so that existing `from market_core import ...` calls
# continue to work without modification.
from market_crud import (  # noqa: F401, E402
    append_price_history,
    check_rate_limit_sqlite,
    db_check_auth_brute_force,
    db_clear_cart,
    db_create_api_key,
    db_create_order,
    db_get_cart,
    db_get_orders,
    db_get_users,
    db_list_api_keys,
    db_migrate_from_json,
    db_record_auth_failure,
    db_remove_cart_item,
    db_revoke_api_key,
    db_save_user,
    db_update_cart_item,
    db_validate_api_key,
    db_add_to_cart,
    save_price_snapshot,
    save_search_query,
)

# Keep a local alias so legacy callers that did `market_core.db_get_users()`
# still resolve — the import above handles `from market_core import` form.

# SENTINEL — all CRUD functions now live in market_crud.py (re-exported above).

# ── Explicit init helper ──────────────────────────────────────────────────────
# NOTE: init_db() is NO LONGER called at import time. Each entrypoint
# (market_server lifespan, collect_prices.main, tests) MUST call
# ensure_db_initialized() before performing DB operations. This eliminates the
# race condition where the import order decided which schema "won".

_db_initialized = False
# True when DATABASE_URL is set but we are currently serving from the SQLite
# fallback because Postgres was unreachable. Enables runtime self-healing.
_pg_fell_back = False
_last_pg_recovery_attempt = 0.0

# Startup connection resilience: Postgres (e.g. Railway) often boots a few
# seconds after the app container, so a single attempt would wrongly fall back
# to an empty SQLite for the whole process lifetime. Retry with backoff.
PG_CONNECT_RETRIES = int(os.getenv("PG_CONNECT_RETRIES", "8"))
PG_CONNECT_BACKOFF = float(os.getenv("PG_CONNECT_BACKOFF", "2.0"))
# When in fallback mode, retry Postgres at most this often (seconds).
PG_RECOVERY_INTERVAL = float(os.getenv("PG_RECOVERY_INTERVAL", "30"))


def _try_init_pg_with_retries() -> bool:
    """Attempt init_db() against Postgres, retrying with capped linear backoff.

    Returns True on success, False after exhausting retries. USE_PG is left
    untouched so the caller decides whether to fall back.
    """
    import time as _time
    for attempt in range(1, PG_CONNECT_RETRIES + 1):
        try:
            init_db()
            if attempt > 1:
                logger.info("PostgreSQL connected on attempt %d", attempt)
            return True
        except Exception as e:
            if attempt >= PG_CONNECT_RETRIES:
                logger.error(
                    "PostgreSQL init failed after %d attempts: %s",
                    PG_CONNECT_RETRIES, str(e)[:160],
                )
                return False
            wait = min(PG_CONNECT_BACKOFF * attempt, 10.0)
            logger.warning(
                "PostgreSQL init attempt %d/%d failed: %s — retrying in %.0fs",
                attempt, PG_CONNECT_RETRIES, str(e)[:160], wait,
            )
            _time.sleep(wait)
    return False


def recover_pg_if_needed() -> None:
    """Self-heal: when we fell back to SQLite but DATABASE_URL is set, retry
    Postgres (throttled). On success, switch back so the process recovers
    without a manual restart. Cheap to call repeatedly — no-op until the
    throttle window elapses.
    """
    global USE_PG, _pg_fell_back, _last_pg_recovery_attempt
    if not _pg_fell_back:
        return
    import time as _time
    now = _time.monotonic()
    if now - _last_pg_recovery_attempt < PG_RECOVERY_INTERVAL:
        return
    _last_pg_recovery_attempt = now
    USE_PG = True
    try:
        init_db()
        _pg_fell_back = False
        logger.warning("PostgreSQL recovered — switched back from SQLite fallback")
    except Exception as e:
        USE_PG = False  # stay on SQLite until the next attempt
        logger.debug("PostgreSQL still unavailable: %s", str(e)[:120])


def ensure_db_initialized() -> None:
    """Idempotent DB init. Safe to call many times; only runs init_db() once.

    Handles the PG→SQLite fallback (with startup retries) and runtime
    self-healing back to Postgres. Always applies payment schema migrations.
    """
    global _db_initialized, USE_PG, _pg_fell_back
    if not _db_initialized:
        if USE_PG:
            if _try_init_pg_with_retries():
                _db_initialized = True
            else:
                logger.warning("PostgreSQL unavailable — falling back to SQLite")
                USE_PG = False
                _pg_fell_back = bool(DATABASE_URL)
                try:
                    init_db()
                    _db_initialized = True
                except Exception as e2:
                    logger.error("SQLite fallback also failed: %s", e2)
                    raise
        else:
            init_db()
            _db_initialized = True
    else:
        # Already initialized — opportunistically try to climb back to Postgres.
        recover_pg_if_needed()
    try:
        db = get_db()
        _migrate_payment_schema(db)
        _migrate_price_snapshots_v7(db)
        db.commit()
        db.close()
    except Exception as e:
        logger.warning("Payment schema migration skipped: %s", e)
