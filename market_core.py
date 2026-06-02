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


# NOTE: rate limiter is defined further down (line ~1004) — it supports
# tiered limits. An older, less accurate bucket-based version used to live
# here and was silently shadowed by Python's name resolution. Removed.


# ── Subscriptions / Tiered pricing ──────────────────────────────────────────────

def db_get_users() -> dict:
    """Return all users as a dict (for backwards compat with existing code)."""
    db = get_db()
    rows = db.execute("SELECT username, password_hash, token FROM app_users").fetchall()
    db.close()
    return {r["username"]: {"password": r["password_hash"], "token": r["token"]} for r in rows}

def db_save_user(username: str, password_hash: str, token: str | None = None) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO app_users (username, password_hash, token, updated_at) VALUES (?,?,?,datetime('now')) "
        "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, token=excluded.token, updated_at=datetime('now')",
        (username, password_hash, token)
    )
    db.commit()
    db.close()

def db_get_cart(username: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, product_id, name, price, store, store_name, quantity, url FROM app_carts WHERE username=?",
        (username,)
    ).fetchall()
    db.close()
    return [{"cart_id": str(r["id"]), "product_id": r["product_id"], "name": r["name"],
             "price": r["price"], "store": r["store"], "store_name": r["store_name"],
             "quantity": r["quantity"], "url": r["url"]} for r in rows]

def db_add_to_cart(username: str, product_id: str, name: str, price: float,
                   store: str, store_name: str = "", quantity: int = 1, url: str = "") -> int:
    db = get_db()
    if USE_PG:
        sql = "INSERT INTO app_carts (username, product_id, name, price, store, store_name, quantity, url) VALUES (?,?,?,?,?,?,?,?) RETURNING id"
    else:
        sql = "INSERT INTO app_carts (username, product_id, name, price, store, store_name, quantity, url) VALUES (?,?,?,?,?,?,?,?)"
    c = db.execute(sql, (username, product_id, name, price, store, store_name, quantity, url))
    cart_id = c.lastrowid
    db.commit()
    db.close()
    return cart_id

def db_update_cart_item(username: str, cart_id: int, quantity: int) -> bool:
    db = get_db()
    if quantity <= 0:
        db.execute("DELETE FROM app_carts WHERE id=? AND username=?", (cart_id, username))
    else:
        db.execute("UPDATE app_carts SET quantity=? WHERE id=? AND username=?", (quantity, cart_id, username))
    db.commit()
    db.close()
    return True

def db_remove_cart_item(username: str, cart_id: int) -> bool:
    db = get_db()
    db.execute("DELETE FROM app_carts WHERE id=? AND username=?", (cart_id, username))
    db.commit()
    db.close()
    return True

def db_clear_cart(username: str) -> None:
    db = get_db()
    db.execute("DELETE FROM app_carts WHERE username=?", (username,))
    db.commit()
    db.close()

def db_get_orders(username: str) -> list[dict]:
    db = get_db()
    orders = db.execute(
        "SELECT order_id, username, payment_method, total, status, created_at FROM app_orders WHERE username=? ORDER BY created_at DESC",
        (username,)
    ).fetchall()
    result = []
    for o in orders:
        items = db.execute(
            "SELECT product_id, name, price, store, store_name, quantity, url FROM app_order_items WHERE order_id=?",
            (o["order_id"],)
        ).fetchall()
        result.append({
            "order_id": o["order_id"],
            "username": o["username"],
            "payment_method": o["payment_method"],
            "total": o["total"],
            "status": o["status"],
            "created_at": o["created_at"],
            "items": [dict(i) for i in items],
        })
    db.close()
    return result

def db_create_order(username: str, items: list[dict], payment_method: str, total: float,
                    status: str = "completed", order_id: str | None = None,
                    gateway_ref: str = "") -> dict:
    import uuid
    if order_id is None:
        order_id = str(uuid.uuid4())[:8]
    db = get_db()
    db.execute(
        "INSERT INTO app_orders (order_id, username, payment_method, total, status, gateway_ref) "
        "VALUES (?,?,?,?,?,?)",
        (order_id, username, payment_method, total, status, gateway_ref or "")
    )
    for item in items:
        db.execute(
            "INSERT INTO app_order_items (order_id, product_id, name, price, store, store_name, quantity, url) VALUES (?,?,?,?,?,?,?,?)",
            (order_id, item.get("product_id", ""), item.get("name", ""), item.get("price", 0),
             item.get("store", ""), item.get("store_name", ""), item.get("quantity", 1), item.get("url", ""))
        )
    db.commit()
    db.close()
    return {"order_id": order_id, "username": username, "payment_method": payment_method, "total": total, "status": status}

def db_migrate_from_json() -> None:
    """One-time migration: import existing JSON data into SQLite tables."""
    import json as _json
    # Migrate users
    if USERS_FILE.exists():
        try:
            users = _json.loads(USERS_FILE.read_text())
            db = get_db()
            for username, data in users.items():
                if USE_PG:
                    db.execute(
                        "INSERT INTO app_users (username, password_hash, token) VALUES (?,?,?) ON CONFLICT(username) DO NOTHING",
                        (username, data.get("password", ""), data.get("token", ""))
                    )
                else:
                    db.execute(
                        "INSERT OR IGNORE INTO app_users (username, password_hash, token) VALUES (?,?,?)",
                        (username, data.get("password", ""), data.get("token", ""))
                    )
            db.commit()
            db.close()
            logger.info("Migrated %d users from JSON", len(users))
        except Exception as e:
            logger.warning("User migration skipped: %s", e)
    # Migrate carts
    if CARTS_FILE.exists():
        try:
            carts = _json.loads(CARTS_FILE.read_text())
            db = get_db()
            count = 0
            for username, items in carts.items():
                for item in items:
                    db.execute(
                        "INSERT OR IGNORE INTO app_carts (username, product_id, name, price, store, store_name, quantity, url) VALUES (?,?,?,?,?,?,?,?)",
                        (username, item.get("product_id", ""), item.get("name", ""), item.get("price", 0),
                         item.get("store", ""), item.get("store_name", ""), item.get("quantity", 1), item.get("url", ""))
                    )
                    count += 1
            db.commit()
            db.close()
            logger.info("Migrated %d cart items from JSON", count)
        except Exception as e:
            logger.warning("Cart migration skipped: %s", e)
    # Migrate orders
    if ORDERS_FILE.exists():
        try:
            orders = _json.loads(ORDERS_FILE.read_text())
            db = get_db()
            for o in orders:
                if USE_PG:
                    db.execute(
                        "INSERT INTO app_orders (order_id, username, payment_method, total, status, created_at) VALUES (?,?,?,?,?,?) ON CONFLICT(order_id) DO NOTHING",
                        (o.get("order_id", ""), o.get("username", ""), o.get("payment_method", "yape"),
                         o.get("total", 0), o.get("status", "completed"), o.get("created_at", ""))
                    )
                else:
                    db.execute(
                        "INSERT OR IGNORE INTO app_orders (order_id, username, payment_method, total, status, created_at) VALUES (?,?,?,?,?,?)",
                        (o.get("order_id", ""), o.get("username", ""), o.get("payment_method", "yape"),
                         o.get("total", 0), o.get("status", "completed"), o.get("created_at", ""))
                    )
                for item in o.get("items", []):
                    db.execute(
                        "INSERT OR IGNORE INTO app_order_items (order_id, product_id, name, price, store, store_name, quantity, url) VALUES (?,?,?,?,?,?,?,?)",
                        (o.get("order_id", ""), item.get("product_id", ""), item.get("name", ""), item.get("price", 0),
                         item.get("store", ""), item.get("store_name", ""), item.get("quantity", 1), item.get("url", ""))
                    )
            db.commit()
            db.close()
            logger.info("Migrated %d orders from JSON", len(orders))
        except Exception as e:
            logger.warning("Order migration skipped: %s", e)

def save_price_snapshot(p: dict, db: "_DB | None" = None) -> None:
    """Upsert one price snapshot.

    If `db` is None, opens its own connection (used by `/search`).
    If `db` is provided (collector batch), reuses it and does NOT commit/close —
    that's the caller's responsibility.
    """
    owns_db = db is None
    price = float(p.get("price", 0) or 0)
    list_price_raw = p.get("list_price", 0)
    list_price = float(list_price_raw) if list_price_raw else None
    from price_confidence import compute_snapshot_confidence

    confidence = p.get("confidence") or compute_snapshot_confidence(price, list_price)
    params = (
        p.get("id", p.get("product_id", "")),
        p.get("name", ""),
        p.get("brand", ""),
        price,
        list_price_raw or 0,
        p.get("discount"),
        p.get("store", ""),
        p.get("store_name", ""),
        p.get("currency", STORES.get(p.get("store", ""), {}).get("currency", "")),
        STORES.get(p.get("store", ""), {}).get("line", ""),
        p.get("line_name", ""),
        p.get("category", ""),
        p.get("stock", 0),
        p.get("url", ""),
        confidence,
    )
    try:
        if owns_db:
            db = get_db()
        if USE_PG:
            db.execute("""
                INSERT INTO price_snapshots
                    (product_id, name, brand, price, list_price, discount,
                     store, store_name, currency, line, line_name, category, stock, url, confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(product_id, store) DO UPDATE SET
                    price=EXCLUDED.price,
                    list_price=EXCLUDED.list_price,
                    discount=EXCLUDED.discount,
                    stock=EXCLUDED.stock,
                    confidence=EXCLUDED.confidence,
                    queried_at=NOW()
            """, params)
        else:
            db.execute("""
                INSERT INTO price_snapshots
                    (product_id, name, brand, price, list_price, discount,
                     store, store_name, currency, line, line_name, category, stock, url, confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(product_id, store) DO UPDATE SET
                    price=excluded.price,
                    list_price=excluded.list_price,
                    discount=excluded.discount,
                    stock=excluded.stock,
                    confidence=excluded.confidence,
                    queried_at=datetime('now')
            """, params)
        append_price_history(
            db,
            params[0],
            params[6],
            float(params[3] or 0),
            float(params[4] or 0),
            params[5],
        )
        if owns_db:
            db.commit()
            db.close()
    except Exception as e:
        logger.error("save_price_snapshot failed: %s", e)
        if owns_db and db is not None:
            try:
                db.close()
            except Exception:
                pass

def save_search_query(query: str, line: str | None, store: str | None, num_results: int) -> None:
    try:
        db = get_db()
        db.execute(
            "INSERT INTO search_queries (query, line, store_filter, num_results) VALUES (?,?,?,?)",
            (query, line, store, num_results)
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.warning("save_search_query failed: %s", e)

# ── API Keys ────────────────────────────────────────────────────────────────────

def db_create_api_key(username: str, scopes: str = "read", label: str = "") -> dict:
    """Generate a new API key. Returns {key, prefix, scopes, id}. The raw key is only shown once."""
    import secrets, hashlib
    raw = "sk-" + secrets.token_urlsafe(32)
    prefix = raw[:10] + "..."
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    db = get_db()
    if USE_PG:
        db.execute(
            "INSERT INTO api_keys (username, key_hash, key_prefix, scopes, label) VALUES (?,?,?,?,?) RETURNING id",
            (username, key_hash, prefix, scopes, label)
        )
        key_id = db.execute("SELECT id FROM api_keys WHERE key_hash=?", (key_hash,)).fetchone()["id"]
    else:
        db.execute(
            "INSERT INTO api_keys (username, key_hash, key_prefix, scopes, label) VALUES (?,?,?,?,?)",
            (username, key_hash, prefix, scopes, label)
        )
        key_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    db.close()
    return {"id": key_id, "key": raw, "prefix": prefix, "scopes": scopes, "label": label}


def db_list_api_keys(username: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, key_prefix, scopes, label, created_at, last_used_at FROM api_keys WHERE username=? ORDER BY created_at DESC",
        (username,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def db_revoke_api_key(username: str, key_id: int) -> bool:
    db = get_db()
    db.execute("DELETE FROM api_keys WHERE id=? AND username=?", (key_id, username))
    affected = db.execute("SELECT changes()").fetchone()[0] if not USE_PG else db._conn.cursor().rowcount
    db.commit()
    db.close()
    return affected > 0


def db_validate_api_key(key: str) -> dict | None:
    """Validate an API key. Returns {username, scopes, key_id} or None."""
    import hashlib
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    db = get_db()
    row = db.execute(
        "SELECT username, scopes, id FROM api_keys WHERE key_hash=?",
        (key_hash,)
    ).fetchone()
    if row:
        db.execute("UPDATE api_keys SET last_used_at=datetime('now') WHERE id=?", (row["id"],))
        db.commit()
    db.close()
    return dict(row) if row else None


def check_rate_limit_sqlite(ip: str, window_secs: int = 60, max_req: int = 10,
                            daily_max: int = 100) -> None:
    """Rate limiter. Persists across restarts. Updated to support tiered limits."""
    import time as _time
    now = _time.time()
    db = get_db()
    # Daily cap
    today_start = _time.mktime(_time.strptime(
        _time.strftime("%Y-%m-%d", _time.gmtime(now)), "%Y-%m-%d"
    ))
    daily_key = f"{ip}:daily"
    db.execute("DELETE FROM rate_limits WHERE key=? AND window_start < ?", (daily_key, today_start))
    daily_row = db.execute(
        "SELECT SUM(counter) as total FROM rate_limits WHERE key=? AND window_start = ?",
        (daily_key, today_start)
    ).fetchone()
    daily_count = daily_row["total"] or 0
    if daily_count >= daily_max:
        db.close()
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail=f"Daily limit reached ({daily_max} req/day).")
    # Per-minute cap
    window_key = f"{ip}:min"
    db.execute("DELETE FROM rate_limits WHERE key=? AND window_start < ?", (window_key, now - window_secs))
    min_count = db.execute(
        "SELECT SUM(counter) as total FROM rate_limits WHERE key=? AND window_start >= ?",
        (window_key, now - window_secs)
    ).fetchone()["total"] or 0
    if min_count >= max_req:
        db.close()
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail=f"Rate limit reached ({max_req} req/{window_secs}s).")
    db.execute(
        "INSERT INTO rate_limits (key, window_start, counter) VALUES (?,?,1) "
        "ON CONFLICT(key, window_start) DO UPDATE SET counter = rate_limits.counter + 1",
        (daily_key, today_start),
    )
    db.execute(
        "INSERT INTO rate_limits (key, window_start, counter) VALUES (?,?,1) "
        "ON CONFLICT(key, window_start) DO UPDATE SET counter = rate_limits.counter + 1",
        (window_key, now),
    )
    db.commit()
    db.close()


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
