"""market_crud — CRUD data-access layer for CLI Market.

All functions that read/write the database (users, cart, orders, API keys,
rate limiting, price snapshots) live here.  market_core.py re-exports
everything for backward compatibility.

Circular-import note: this module must NOT import market_core at module level.
USE_PG and logger are accessed lazily (inside function bodies) to allow
market_core to import and re-export from here without a cycle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time as _time
import uuid

logger = logging.getLogger("market")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_db():
    from market_db import get_db
    return get_db()


def _use_pg() -> bool:
    import market_core
    return market_core.USE_PG


def _stores() -> dict:
    from market_stores import STORES
    return STORES


# ── Users ─────────────────────────────────────────────────────────────────────

def db_get_users() -> dict:
    db = _get_db()
    rows = db.execute("SELECT username, password_hash, token FROM app_users").fetchall()
    db.close()
    return {r["username"]: {"password": r["password_hash"], "token": r["token"]} for r in rows}


def db_save_user(username: str, password_hash: str, token: str | None = None) -> None:
    db = _get_db()
    db.execute(
        "INSERT INTO app_users (username, password_hash, token, updated_at) VALUES (?,?,?,datetime('now')) "
        "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, token=excluded.token, updated_at=datetime('now')",
        (username, password_hash, token),
    )
    db.commit()
    db.close()


# ── Cart ──────────────────────────────────────────────────────────────────────

def db_get_cart(username: str) -> list[dict]:
    db = _get_db()
    rows = db.execute(
        "SELECT id, product_id, name, price, store, store_name, quantity, url FROM app_carts WHERE username=?",
        (username,),
    ).fetchall()
    db.close()
    return [
        {
            "cart_id": str(r["id"]),
            "product_id": r["product_id"],
            "name": r["name"],
            "price": r["price"],
            "store": r["store"],
            "store_name": r["store_name"],
            "quantity": r["quantity"],
            "url": r["url"],
        }
        for r in rows
    ]


def db_add_to_cart(
    username: str,
    product_id: str,
    name: str,
    price: float,
    store: str,
    store_name: str = "",
    quantity: int = 1,
    url: str = "",
) -> int:
    db = _get_db()
    if _use_pg():
        sql = (
            "INSERT INTO app_carts (username, product_id, name, price, store, store_name, quantity, url) "
            "VALUES (?,?,?,?,?,?,?,?) RETURNING id"
        )
    else:
        sql = (
            "INSERT INTO app_carts (username, product_id, name, price, store, store_name, quantity, url) "
            "VALUES (?,?,?,?,?,?,?,?)"
        )
    c = db.execute(sql, (username, product_id, name, price, store, store_name, quantity, url))
    cart_id = c.lastrowid
    db.commit()
    db.close()
    return cart_id


def db_update_cart_item(username: str, cart_id: int, quantity: int) -> bool:
    db = _get_db()
    if quantity <= 0:
        db.execute("DELETE FROM app_carts WHERE id=? AND username=?", (cart_id, username))
    else:
        db.execute("UPDATE app_carts SET quantity=? WHERE id=? AND username=?", (quantity, cart_id, username))
    db.commit()
    db.close()
    return True


def db_remove_cart_item(username: str, cart_id: int) -> bool:
    db = _get_db()
    db.execute("DELETE FROM app_carts WHERE id=? AND username=?", (cart_id, username))
    db.commit()
    db.close()
    return True


def db_clear_cart(username: str) -> None:
    db = _get_db()
    db.execute("DELETE FROM app_carts WHERE username=?", (username,))
    db.commit()
    db.close()


# ── Orders ────────────────────────────────────────────────────────────────────

def db_get_orders(username: str) -> list[dict]:
    db = _get_db()
    orders = db.execute(
        "SELECT order_id, username, payment_method, total, status, created_at "
        "FROM app_orders WHERE username=? ORDER BY created_at DESC",
        (username,),
    ).fetchall()
    result = []
    for o in orders:
        items = db.execute(
            "SELECT product_id, name, price, store, store_name, quantity, url "
            "FROM app_order_items WHERE order_id=?",
            (o["order_id"],),
        ).fetchall()
        result.append(
            {
                "order_id": o["order_id"],
                "username": o["username"],
                "payment_method": o["payment_method"],
                "total": o["total"],
                "status": o["status"],
                "created_at": o["created_at"],
                "items": [dict(i) for i in items],
            }
        )
    db.close()
    return result


def db_create_order(
    username: str,
    items: list[dict],
    payment_method: str,
    total: float,
    status: str = "completed",
    order_id: str | None = None,
    gateway_ref: str = "",
) -> dict:
    if order_id is None:
        order_id = str(uuid.uuid4())[:8]
    db = _get_db()
    db.execute(
        "INSERT INTO app_orders (order_id, username, payment_method, total, status, gateway_ref) "
        "VALUES (?,?,?,?,?,?)",
        (order_id, username, payment_method, total, status, gateway_ref or ""),
    )
    for item in items:
        db.execute(
            "INSERT INTO app_order_items "
            "(order_id, product_id, name, price, store, store_name, quantity, url) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                order_id,
                item.get("product_id", ""),
                item.get("name", ""),
                item.get("price", 0),
                item.get("store", ""),
                item.get("store_name", ""),
                item.get("quantity", 1),
                item.get("url", ""),
            ),
        )
    db.commit()
    db.close()
    return {
        "order_id": order_id,
        "username": username,
        "payment_method": payment_method,
        "total": total,
        "status": status,
    }


# ── JSON migration (one-time, idempotent) ─────────────────────────────────────

def db_migrate_from_json(
    users_file=None,
    carts_file=None,
    orders_file=None,
) -> None:
    """Import legacy JSON data into DB tables. Idempotent — safe to re-run."""
    from pathlib import Path
    import market_core as _mc

    _users_file = users_file or _mc.USERS_FILE
    _carts_file = carts_file or _mc.CARTS_FILE
    _orders_file = orders_file or _mc.ORDERS_FILE

    def _pg_ignore(sql: str) -> str:
        return sql.replace("INSERT OR IGNORE INTO", "INSERT INTO") + " ON CONFLICT DO NOTHING"

    use_pg = _use_pg()

    if _users_file.exists():
        try:
            users = json.loads(Path(_users_file).read_text())
            db = _get_db()
            for username, data in users.items():
                sql = (
                    _pg_ignore("INSERT OR IGNORE INTO app_users (username, password_hash, token) VALUES (?,?,?)")
                    if use_pg
                    else "INSERT OR IGNORE INTO app_users (username, password_hash, token) VALUES (?,?,?)"
                )
                db.execute(sql, (username, data.get("password", ""), data.get("token", "")))
            db.commit()
            db.close()
            logger.info("Migrated %d users from JSON", len(users))
        except Exception as e:
            logger.warning("User migration skipped: %s", e)

    if _carts_file.exists():
        try:
            carts = json.loads(Path(_carts_file).read_text())
            db = _get_db()
            count = 0
            for username, items in carts.items():
                for item in items:
                    db.execute(
                        "INSERT OR IGNORE INTO app_carts "
                        "(username, product_id, name, price, store, store_name, quantity, url) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            username,
                            item.get("product_id", ""),
                            item.get("name", ""),
                            item.get("price", 0),
                            item.get("store", ""),
                            item.get("store_name", ""),
                            item.get("quantity", 1),
                            item.get("url", ""),
                        ),
                    )
                    count += 1
            db.commit()
            db.close()
            logger.info("Migrated %d cart items from JSON", count)
        except Exception as e:
            logger.warning("Cart migration skipped: %s", e)

    if _orders_file.exists():
        try:
            orders = json.loads(Path(_orders_file).read_text())
            db = _get_db()
            for o in orders:
                oid = o.get("order_id", "")
                if use_pg:
                    db.execute(
                        "INSERT INTO app_orders (order_id, username, payment_method, total, status, created_at) "
                        "VALUES (?,?,?,?,?,?) ON CONFLICT(order_id) DO NOTHING",
                        (oid, o.get("username", ""), o.get("payment_method", "yape"),
                         o.get("total", 0), o.get("status", "completed"), o.get("created_at", "")),
                    )
                else:
                    db.execute(
                        "INSERT OR IGNORE INTO app_orders "
                        "(order_id, username, payment_method, total, status, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (oid, o.get("username", ""), o.get("payment_method", "yape"),
                         o.get("total", 0), o.get("status", "completed"), o.get("created_at", "")),
                    )
                for item in o.get("items", []):
                    db.execute(
                        "INSERT OR IGNORE INTO app_order_items "
                        "(order_id, product_id, name, price, store, store_name, quantity, url) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (oid, item.get("product_id", ""), item.get("name", ""),
                         item.get("price", 0), item.get("store", ""), item.get("store_name", ""),
                         item.get("quantity", 1), item.get("url", "")),
                    )
            db.commit()
            db.close()
            logger.info("Migrated %d orders from JSON", len(orders))
        except Exception as e:
            logger.warning("Order migration skipped: %s", e)


# ── Price snapshots ───────────────────────────────────────────────────────────

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
            "INSERT INTO price_history (product_id, store, price, list_price, discount) VALUES (?, ?, ?, ?, ?)",
            (product_id, store, price, list_price, discount),
        )
    except Exception as e:
        logger.debug("price_history append skipped: %s", e)


def save_price_snapshot(p: dict, db=None) -> None:
    """Upsert one price snapshot.

    If ``db`` is None, opens and closes its own connection (used by /search).
    If ``db`` is provided (collector batch), reuses it without commit/close.
    """
    owns_db = db is None
    price = float(p.get("price", 0) or 0)
    list_price_raw = p.get("list_price", 0)
    list_price = float(list_price_raw) if list_price_raw else None
    from price_confidence import compute_snapshot_confidence

    confidence = p.get("confidence") or compute_snapshot_confidence(price, list_price)
    stores = _stores()
    params = (
        p.get("id", p.get("product_id", "")),
        p.get("name", ""),
        p.get("brand", ""),
        price,
        list_price_raw or 0,
        p.get("discount"),
        p.get("store", ""),
        p.get("store_name", ""),
        p.get("currency", stores.get(p.get("store", ""), {}).get("currency", "")),
        stores.get(p.get("store", ""), {}).get("line", ""),
        p.get("line_name", ""),
        p.get("category", ""),
        p.get("stock", 0),
        p.get("url", ""),
        confidence,
    )
    try:
        if owns_db:
            db = _get_db()
        if _use_pg():
            db.execute(
                """
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
                """,
                params,
            )
        else:
            db.execute(
                """
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
                """,
                params,
            )
        append_price_history(db, params[0], params[6], float(params[3] or 0), float(params[4] or 0), params[5])
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
        db = _get_db()
        db.execute(
            "INSERT INTO search_queries (query, line, store_filter, num_results) VALUES (?,?,?,?)",
            (query, line, store, num_results),
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.warning("save_search_query failed: %s", e)


# ── API keys ──────────────────────────────────────────────────────────────────

def db_create_api_key(username: str, scopes: str = "read", label: str = "") -> dict:
    """Generate an API key. Returns {key, prefix, scopes, id}. Raw key shown once only."""
    raw = "sk-" + secrets.token_urlsafe(32)
    prefix = raw[:10] + "..."
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    db = _get_db()
    if _use_pg():
        db.execute(
            "INSERT INTO api_keys (username, key_hash, key_prefix, scopes, label) VALUES (?,?,?,?,?) RETURNING id",
            (username, key_hash, prefix, scopes, label),
        )
        key_id = db.execute("SELECT id FROM api_keys WHERE key_hash=?", (key_hash,)).fetchone()["id"]
    else:
        db.execute(
            "INSERT INTO api_keys (username, key_hash, key_prefix, scopes, label) VALUES (?,?,?,?,?)",
            (username, key_hash, prefix, scopes, label),
        )
        key_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    db.close()
    return {"id": key_id, "key": raw, "prefix": prefix, "scopes": scopes, "label": label}


def db_list_api_keys(username: str) -> list[dict]:
    db = _get_db()
    rows = db.execute(
        "SELECT id, key_prefix, scopes, label, created_at, last_used_at "
        "FROM api_keys WHERE username=? ORDER BY created_at DESC",
        (username,),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def db_revoke_api_key(username: str, key_id: int) -> bool:
    db = _get_db()
    if _use_pg():
        row = db.execute(
            "DELETE FROM api_keys WHERE id=? AND username=? RETURNING id",
            (key_id, username),
        ).fetchone()
        db.commit()
        db.close()
        return row is not None
    db.execute("DELETE FROM api_keys WHERE id=? AND username=?", (key_id, username))
    affected = db.execute("SELECT changes()").fetchone()[0]
    db.commit()
    db.close()
    return affected > 0


def db_validate_api_key(key: str) -> dict | None:
    """Validate a raw API key. Returns {username, scopes, key_id} or None."""
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    db = _get_db()
    row = db.execute(
        "SELECT username, scopes, id FROM api_keys WHERE key_hash=?",
        (key_hash,),
    ).fetchone()
    if row:
        db.execute("UPDATE api_keys SET last_used_at=datetime('now') WHERE id=?", (row["id"],))
        db.commit()
    db.close()
    return dict(row) if row else None


# ── Rate limiting ─────────────────────────────────────────────────────────────

def check_rate_limit_sqlite(
    ip: str,
    window_secs: int = 60,
    max_req: int = 10,
    daily_max: int = 100,
) -> None:
    """DB-backed rate limiter (survives restarts, works across processes)."""
    from fastapi import HTTPException

    now = _time.time()
    db = _get_db()
    today_start = _time.mktime(_time.strptime(_time.strftime("%Y-%m-%d", _time.gmtime(now)), "%Y-%m-%d"))
    daily_key = f"{ip}:daily"
    db.execute("DELETE FROM rate_limits WHERE key=? AND window_start < ?", (daily_key, today_start))
    daily_row = db.execute(
        "SELECT SUM(counter) as total FROM rate_limits WHERE key=? AND window_start = ?",
        (daily_key, today_start),
    ).fetchone()
    if (daily_row["total"] or 0) >= daily_max:
        db.close()
        raise HTTPException(status_code=429, detail=f"Daily limit reached ({daily_max} req/day).")
    window_key = f"{ip}:min"
    db.execute("DELETE FROM rate_limits WHERE key=? AND window_start < ?", (window_key, now - window_secs))
    min_count = (
        db.execute(
            "SELECT SUM(counter) as total FROM rate_limits WHERE key=? AND window_start >= ?",
            (window_key, now - window_secs),
        ).fetchone()["total"]
        or 0
    )
    if min_count >= max_req:
        db.close()
        raise HTTPException(status_code=429, detail=f"Rate limit reached ({max_req} req/{window_secs}s).")
    for key, ts in ((daily_key, today_start), (window_key, now)):
        db.execute(
            "INSERT INTO rate_limits (key, window_start, counter) VALUES (?,?,1) "
            "ON CONFLICT(key, window_start) DO UPDATE SET counter = rate_limits.counter + 1",
            (key, ts),
        )
    db.commit()
    db.close()


# ── Auth brute-force ──────────────────────────────────────────────────────────

def db_check_auth_brute_force(username: str, max_attempts: int = 5, window_secs: int = 300) -> None:
    """Raise 429 if username exceeded max_attempts in the last window_secs.

    Persisted in rate_limits table — survives restarts and scales across processes.
    """
    from fastapi import HTTPException

    now = _time.time()
    key = f"auth:{username}"
    cutoff = now - window_secs
    db = _get_db()
    db.execute("DELETE FROM rate_limits WHERE key=? AND window_start < ?", (key, cutoff))
    count = (
        db.execute(
            "SELECT SUM(counter) as total FROM rate_limits WHERE key=? AND window_start >= ?",
            (key, cutoff),
        ).fetchone()["total"]
        or 0
    )
    db.commit()
    db.close()
    if count >= max_attempts:
        raise HTTPException(status_code=429, detail="Demasiados intentos. Esperá 5 minutos.")


def db_record_auth_failure(username: str) -> None:
    """Persist a failed auth attempt in the rate_limits table."""
    db = _get_db()
    key = f"auth:{username}"
    now = _time.time()
    db.execute(
        "INSERT INTO rate_limits (key, window_start, counter) VALUES (?,?,1) "
        "ON CONFLICT(key, window_start) DO UPDATE SET counter = rate_limits.counter + 1",
        (key, now),
    )
    db.commit()
    db.close()
