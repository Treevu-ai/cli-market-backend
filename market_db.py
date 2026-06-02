"""Database connection layer and schema DDL (PostgreSQL or SQLite).

State (USE_PG, DATABASE_URL, DB_FILE) and lifecycle (init_db,
ensure_db_initialized) live in market_core. This module owns the connection
abstraction and the table definitions, referenced by market_core at runtime.
"""

from __future__ import annotations

import logging
import sqlite3

import market_core

logger = logging.getLogger("market").getChild("db")


class _PgCursor:
    """Mimics sqlite3.Cursor for psycopg2."""
    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None


class _DB:
    """Unified DB connection: PostgreSQL or SQLite."""
    def __init__(self):
        if market_core.USE_PG:
            import psycopg2
            from urllib.parse import urlparse, parse_qs
            url = market_core.DATABASE_URL.strip()
            parsed = urlparse(url)
            kwargs = {
                "host": parsed.hostname or "localhost",
                "port": parsed.port or 5432,
                "dbname": parsed.path.lstrip("/") or "postgres",
                "user": parsed.username or "",
                "password": parsed.password or "",
                "connect_timeout": 10,
            }
            qs = parse_qs(parsed.query)
            if "sslmode" in qs:
                kwargs["sslmode"] = qs["sslmode"][0]
            else:
                # 'prefer' (not 'require') so it works on Railway private
                # networking (postgres.railway.internal offers no SSL) AND on
                # public URLs (which do). 'require' broke the private path,
                # silently falling back to an empty SQLite and serving 0 data.
                # Override via PG_SSL_MODE env var if a stricter mode is needed.
                import os
                kwargs["sslmode"] = os.getenv("PG_SSL_MODE", "prefer")
            self._conn = psycopg2.connect(**kwargs)
            self._pg = True
        else:
            self._conn = sqlite3.connect(str(market_core.DB_FILE))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._pg = False

    def execute(self, sql, params=None):
        if self._pg:
            import psycopg2.extras
            sql = sql.replace("?", "%s")
            # Replace longer datetime patterns FIRST so the generic datetime('now')
            # doesn't swallow the interval variants.
            sql = sql.replace("datetime('now', '-14 days')", "NOW() - INTERVAL '14 days'")
            sql = sql.replace("datetime('now', '-7 days')", "NOW() - INTERVAL '7 days'")
            sql = sql.replace("datetime('now', '-24 hours')", "NOW() - INTERVAL '24 hours'")
            sql = sql.replace("datetime('now')", "NOW()")
            sql = sql.replace("INSERT OR REPLACE", "INSERT")
            sql = sql.replace("INSERT OR IGNORE", "INSERT")
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            wrapper = _PgCursor(cur)
            # Capture lastrowid from RETURNING clause
            if "RETURNING" in sql.upper():
                row = cur.fetchone()
                if row:
                    wrapper.lastrowid = list(row.values())[0] if row else None
            return wrapper
        else:
            sql = sql.replace("::numeric", "")
            return self._conn.execute(sql, params or ())

    def executescript(self, sql):
        if self._pg:
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    self.execute(stmt)
        else:
            self._conn.executescript(sql)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db() -> _DB:
    import time as _time
    for attempt in range(3):
        try:
            return _DB()
        except Exception:
            if attempt < 2:
                _time.sleep(0.2 * (attempt + 1))
            else:
                raise


def _migrate_price_snapshots_pg(db: _DB) -> None:
    """Ensure upsert target exists on legacy Postgres deployments.

    Older price_snapshots tables were created without UNIQUE(product_id, store).
    Inserts using ON CONFLICT(product_id, store) then fail silently in callers.
    """
    try:
        row = db.execute(
            """
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'price_snapshots'
              AND indexdef ILIKE '%UNIQUE%'
              AND indexdef ILIKE '%product_id%'
              AND indexdef ILIKE '%store%'
            LIMIT 1
            """
        ).fetchone()
        if row:
            return
        db.execute(
            """
            DELETE FROM price_snapshots a
            USING price_snapshots b
            WHERE a.product_id = b.product_id
              AND a.store = b.store
              AND a.id < b.id
            """
        )
        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_price_snapshots_product_store
            ON price_snapshots (product_id, store)
            """
        )
        logger.info("Migrated price_snapshots: added UNIQUE(product_id, store)")
    except Exception as e:
        logger.warning("price_snapshots migration skipped: %s", e)


def _migrate_price_snapshots_v7(db: _DB) -> None:
    """Fase 7: confidence column + query indexes for /v1/prices."""
    if market_core.USE_PG:
        try:
            db.execute(
                "ALTER TABLE price_snapshots ADD COLUMN IF NOT EXISTS confidence TEXT NOT NULL DEFAULT 'ok'"
            )
        except Exception as e:
            logger.warning("price_snapshots confidence column skipped: %s", e)
    else:
        try:
            db.execute("ALTER TABLE price_snapshots ADD COLUMN confidence TEXT NOT NULL DEFAULT 'ok'")
        except Exception:
            pass

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_ps_store_line_queried ON price_snapshots(store, line, queried_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_ps_line_currency ON price_snapshots(line, currency)",
        "CREATE INDEX IF NOT EXISTS idx_ps_confidence ON price_snapshots(confidence) WHERE confidence != 'ok'",
    ]:
        try:
            db.execute(idx_sql)
        except Exception as e:
            logger.warning("price_snapshots v7 index skipped: %s", e)


def price_snapshots_has_confidence(db: _DB) -> bool:
    """True when confidence column exists (post Fase 7 migration)."""
    try:
        if market_core.USE_PG:
            row = db.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'price_snapshots' AND column_name = 'confidence'
                LIMIT 1
                """
            ).fetchone()
            return bool(row)
        row = db.execute("PRAGMA table_info(price_snapshots)").fetchall()
        return any(r["name"] == "confidence" for r in row)
    except Exception:
        return False


def init_db_pg(db: _DB) -> None:
    """PostgreSQL schema."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            chat_id TEXT PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            last_message TEXT,
            created_at TEXT,
            updated_at TEXT DEFAULT NOW()
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id SERIAL PRIMARY KEY,
            product_id TEXT NOT NULL,
            name TEXT,
            brand TEXT,
            price DOUBLE PRECISION,
            list_price DOUBLE PRECISION,
            discount INTEGER,
            store TEXT NOT NULL,
            store_name TEXT,
            currency TEXT,
            line TEXT,
            line_name TEXT,
            category TEXT,
            stock INTEGER,
            url TEXT,
            queried_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            confidence TEXT NOT NULL DEFAULT 'ok',
            UNIQUE(product_id, store)
        )
    """)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_ps_product ON price_snapshots(product_id, store)",
        "CREATE INDEX IF NOT EXISTS idx_ps_store ON price_snapshots(store)",
        "CREATE INDEX IF NOT EXISTS idx_ps_line ON price_snapshots(line)",
        "CREATE INDEX IF NOT EXISTS idx_ps_queried ON price_snapshots(queried_at)",
    ]:
        db.execute(idx_sql)

    _migrate_price_snapshots_pg(db)
    _migrate_price_snapshots_v7(db)

    db.execute("""
        CREATE TABLE IF NOT EXISTS search_queries (
            id SERIAL PRIMARY KEY,
            query TEXT NOT NULL,
            line TEXT,
            country TEXT,
            store_filter TEXT,
            num_results INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_sq_created ON search_queries(created_at)")

    db.execute("""
        CREATE TABLE IF NOT EXISTS app_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            token TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS app_carts (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            product_id TEXT NOT NULL,
            name TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL DEFAULT 0,
            store TEXT NOT NULL,
            store_name TEXT DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 1,
            url TEXT DEFAULT '',
            added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_cart_user ON app_carts(username)")

    db.execute("""
        CREATE TABLE IF NOT EXISTS app_orders (
            order_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            payment_method TEXT DEFAULT 'yape',
            total DOUBLE PRECISION NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_order_user ON app_orders(username)")

    db.execute("""
        CREATE TABLE IF NOT EXISTS app_order_items (
            id SERIAL PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES app_orders(order_id),
            product_id TEXT NOT NULL,
            name TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL DEFAULT 0,
            store TEXT NOT NULL,
            store_name TEXT DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 1,
            url TEXT DEFAULT ''
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_oi_order ON app_order_items(order_id)")

    db.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT NOT NULL,
            window_start DOUBLE PRECISION NOT NULL,
            counter INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (key, window_start)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_rl_key ON rate_limits(key)")

    db.execute("""
        CREATE TABLE IF NOT EXISTS collector_runs (
            id SERIAL PRIMARY KEY,
            started_at TIMESTAMPTZ DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            stores_attempted INT DEFAULT 0,
            stores_succeeded INT DEFAULT 0,
            prices_collected INT DEFAULT 0,
            errors TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS collector_triggers (
            id SERIAL PRIMARY KEY,
            requested_at TIMESTAMPTZ DEFAULT NOW(),
            source TEXT DEFAULT 'dashboard',
            fulfilled_at TIMESTAMPTZ
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS store_health (
            store TEXT PRIMARY KEY,
            last_success TEXT,
            last_error TEXT,
            consecutive_failures INT DEFAULT 0,
            total_requests INT DEFAULT 0,
            total_successes INT DEFAULT 0
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL DEFAULT '',
            scopes TEXT NOT NULL DEFAULT 'read',
            label TEXT DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at TIMESTAMPTZ
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_api_user ON api_keys(username)")

    db.execute("""
        CREATE TABLE IF NOT EXISTS app_favorites (
            username TEXT NOT NULL,
            product_id TEXT NOT NULL,
            name TEXT DEFAULT '',
            store TEXT DEFAULT '',
            PRIMARY KEY (username, product_id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            username TEXT PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'free',
            req_limit_day INTEGER NOT NULL DEFAULT 1000,
            req_limit_min INTEGER NOT NULL DEFAULT 60,
            started_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS retailer_applications (
            id TEXT PRIMARY KEY,
            store_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            country TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            contact_name TEXT DEFAULT '',
            website TEXT DEFAULT '',
            api_token_hint TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_retailer_apply_email ON retailer_applications(contact_email)")

    from market_billing import _migrate_payment_schema
    _migrate_payment_schema(db)
    market_core._migrate_store_credentials(db)
    db.commit()


_SQLITE_DDL = """\
        CREATE TABLE IF NOT EXISTS contacts (
            chat_id TEXT PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            last_message TEXT,
            created_at TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            name TEXT,
            brand TEXT,
            price REAL,
            list_price REAL,
            discount INTEGER,
            store TEXT NOT NULL,
            store_name TEXT,
            currency TEXT,
            line TEXT,
            line_name TEXT,
            category TEXT,
            stock INTEGER,
            url TEXT,
            queried_at TEXT NOT NULL DEFAULT (datetime('now')),
            confidence TEXT NOT NULL DEFAULT 'ok',
            UNIQUE(product_id, store)
        );
        CREATE INDEX IF NOT EXISTS idx_ps_product ON price_snapshots(product_id, store);
        CREATE INDEX IF NOT EXISTS idx_ps_store ON price_snapshots(store);
        CREATE INDEX IF NOT EXISTS idx_ps_line ON price_snapshots(line);
        CREATE INDEX IF NOT EXISTS idx_ps_queried ON price_snapshots(queried_at);

        CREATE TABLE IF NOT EXISTS search_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            line TEXT,
            country TEXT,
            store_filter TEXT,
            num_results INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sq_created ON search_queries(created_at);

        CREATE TABLE IF NOT EXISTS app_users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            token TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS app_carts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            product_id TEXT NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            store TEXT NOT NULL,
            store_name TEXT DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 1,
            url TEXT DEFAULT '',
            added_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_cart_user ON app_carts(username);

        CREATE TABLE IF NOT EXISTS app_orders (
            order_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            payment_method TEXT DEFAULT 'yape',
            total REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_order_user ON app_orders(username);

        CREATE TABLE IF NOT EXISTS app_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL REFERENCES app_orders(order_id),
            product_id TEXT NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            store TEXT NOT NULL,
            store_name TEXT DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 1,
            url TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_oi_order ON app_order_items(order_id);

        CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT NOT NULL,
            window_start REAL NOT NULL,
            counter INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (key, window_start)
        );
        CREATE INDEX IF NOT EXISTS idx_rl_key ON rate_limits(key);

        CREATE TABLE IF NOT EXISTS collector_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            finished_at TEXT,
            stores_attempted INT DEFAULT 0,
            stores_succeeded INT DEFAULT 0,
            prices_collected INT DEFAULT 0,
            errors TEXT
        );

        CREATE TABLE IF NOT EXISTS collector_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requested_at TEXT DEFAULT (datetime('now')),
            source TEXT DEFAULT 'dashboard',
            fulfilled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS store_health (
            store TEXT PRIMARY KEY,
            last_success TEXT,
            last_error TEXT,
            consecutive_failures INT DEFAULT 0,
            total_requests INT DEFAULT 0,
            total_successes INT DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL DEFAULT '',
            scopes TEXT NOT NULL DEFAULT 'read',
            label TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_api_user ON api_keys(username);

        CREATE TABLE IF NOT EXISTS app_favorites (
            username TEXT NOT NULL,
            product_id TEXT NOT NULL,
            name TEXT DEFAULT '',
            store TEXT DEFAULT '',
            PRIMARY KEY (username, product_id)
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            username TEXT PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'free',
            req_limit_day INTEGER NOT NULL DEFAULT 1000,
            req_limit_min INTEGER NOT NULL DEFAULT 60,
            started_at TEXT,
            expires_at TEXT
        );

        CREATE TABLE IF NOT EXISTS retailer_applications (
            id TEXT PRIMARY KEY,
            store_name TEXT NOT NULL,
            platform TEXT NOT NULL,
            country TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            contact_name TEXT DEFAULT '',
            website TEXT DEFAULT '',
            api_token_hint TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_retailer_apply_email ON retailer_applications(contact_email);

        CREATE TABLE IF NOT EXISTS billing_pending (
            external_id TEXT PRIMARY KEY,
            gateway TEXT NOT NULL,
            username TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'subscription',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
"""
