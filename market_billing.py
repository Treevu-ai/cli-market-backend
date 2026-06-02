"""Billing, subscriptions, and payment schema migrations."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import market_core

logger = market_core.logger

TIERS = {
    "free": {"req_min": 60, "req_day": 1000, "api_keys": 1, "checkout": False},
    "pro": {"req_min": 300, "req_day": 10000, "api_keys": 10, "checkout": True},
    "enterprise": {"req_min": 0, "req_day": 0, "api_keys": 0, "checkout": True},
}


def _migrate_payment_schema(db) -> None:
    """Add payment columns/tables on existing deployments."""
    if market_core.USE_PG:
        db.execute("""
            CREATE TABLE IF NOT EXISTS subscription_requests (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                email TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payment_link TEXT DEFAULT '',
                email_sent INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sub_req_email ON subscription_requests(email)"
        )
        db.execute("""
            CREATE TABLE IF NOT EXISTS billing_pending (
                external_id TEXT PRIMARY KEY,
                gateway TEXT NOT NULL,
                username TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'subscription',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        for stmt in (
            "ALTER TABLE app_orders ADD COLUMN IF NOT EXISTS gateway_ref TEXT DEFAULT ''",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS paypal_subscription_id TEXT DEFAULT ''",
        ):
            try:
                db.execute(stmt)
            except Exception as e:
                logger.warning("PG migration skipped: %s", e)
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS billing_pending (
            external_id TEXT PRIMARY KEY,
            gateway TEXT NOT NULL,
            username TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'subscription',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    for stmt in (
        "ALTER TABLE app_orders ADD COLUMN gateway_ref TEXT DEFAULT ''",
        "ALTER TABLE subscriptions ADD COLUMN paypal_subscription_id TEXT DEFAULT ''",
    ):
        try:
            db.execute(stmt)
        except Exception:
            pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS subscription_requests (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_link TEXT DEFAULT '',
            email_sent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sub_req_email ON subscription_requests(email)"
    )


def db_get_subscription(username: str) -> dict:
    """Get user subscription. Falls back to free tier defaults."""
    db = market_core.get_db()
    row = db.execute(
        "SELECT tier, req_limit_day, req_limit_min FROM subscriptions WHERE username=?",
        (username,),
    ).fetchone()
    db.close()
    if row:
        return dict(row)
    return {
        "tier": "free",
        "req_limit_day": TIERS["free"]["req_day"],
        "req_limit_min": TIERS["free"]["req_min"],
    }


def db_set_subscription(
    username: str,
    tier: str,
    req_day: int | None = None,
    req_min: int | None = None,
    expires_days: int | None = None,
    paypal_subscription_id: str | None = None,
) -> dict:
    db = market_core.get_db()
    t = TIERS.get(tier, TIERS["free"])
    day = req_day if req_day is not None else t["req_day"]
    mn = req_min if req_min is not None else t["req_min"]
    pp_sub = paypal_subscription_id or ""
    db.execute(
        "INSERT INTO subscriptions (username, tier, req_limit_day, req_limit_min, paypal_subscription_id) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(username) DO UPDATE SET tier=?, req_limit_day=?, req_limit_min=?, "
        "paypal_subscription_id=CASE WHEN excluded.paypal_subscription_id != '' "
        "THEN excluded.paypal_subscription_id ELSE subscriptions.paypal_subscription_id END",
        (username, tier, day, mn, pp_sub, tier, day, mn),
    )
    db.commit()
    db.close()
    return {"username": username, "tier": tier, "req_limit_day": day, "req_limit_min": mn}


def db_update_order_status(order_id: str, status: str) -> bool:
    db = market_core.get_db()
    cur = db.execute("UPDATE app_orders SET status=? WHERE order_id=?", (status, order_id))
    db.commit()
    affected = cur.rowcount
    db.close()
    return affected > 0


def db_set_order_gateway_ref(order_id: str, gateway_ref: str) -> None:
    db = market_core.get_db()
    db.execute("UPDATE app_orders SET gateway_ref=? WHERE order_id=?", (gateway_ref, order_id))
    db.commit()
    db.close()


def db_find_order_by_gateway_ref(gateway_ref: str) -> dict | None:
    db = market_core.get_db()
    row = db.execute(
        "SELECT order_id, username, payment_method, total, status FROM app_orders WHERE gateway_ref=?",
        (gateway_ref,),
    ).fetchone()
    db.close()
    return dict(row) if row else None


def db_find_order_by_id(order_id: str) -> dict | None:
    db = market_core.get_db()
    row = db.execute(
        "SELECT order_id, username, payment_method, total, status, gateway_ref FROM app_orders "
        "WHERE order_id=?",
        (order_id,),
    ).fetchone()
    db.close()
    return dict(row) if row else None


def db_save_billing_pending(
    external_id: str, gateway: str, username: str, kind: str = "subscription"
) -> None:
    db = market_core.get_db()
    if market_core.USE_PG:
        db.execute(
            "INSERT INTO billing_pending (external_id, gateway, username, kind) VALUES (?,?,?,?) "
            "ON CONFLICT (external_id) DO UPDATE SET username=excluded.username, kind=excluded.kind",
            (external_id, gateway, username, kind),
        )
    else:
        db.execute(
            "INSERT OR REPLACE INTO billing_pending (external_id, gateway, username, kind) "
            "VALUES (?,?,?,?)",
            (external_id, gateway, username, kind),
        )
    db.commit()
    db.close()


def db_get_billing_pending(external_id: str) -> dict | None:
    db = market_core.get_db()
    row = db.execute(
        "SELECT external_id, gateway, username, kind FROM billing_pending WHERE external_id=?",
        (external_id,),
    ).fetchone()
    db.close()
    return dict(row) if row else None


def db_delete_billing_pending(external_id: str) -> None:
    db = market_core.get_db()
    db.execute("DELETE FROM billing_pending WHERE external_id=?", (external_id,))
    db.commit()
    db.close()


def user_can_checkout(username: str) -> bool:
    """True if user tier allows checkout or legacy bypass is enabled."""
    if os.getenv("MARKET_LEGACY_CHECKOUT", "").lower() in ("1", "true", "yes"):
        return True
    sub = db_get_subscription(username)
    return bool(TIERS.get(sub.get("tier", "free"), TIERS["free"]).get("checkout"))


def db_create_subscription_request(username: str, email: str, payment_link: str) -> dict:
    req_id = f"PRO-{uuid.uuid4().hex[:8].upper()}"
    db = market_core.get_db()
    db.execute(
        """
        INSERT INTO subscription_requests (id, username, email, status, payment_link, email_sent)
        VALUES (?, ?, ?, 'pending', ?, 0)
        """,
        (req_id, username, email.strip().lower(), payment_link),
    )
    db.commit()
    db.close()
    return {
        "id": req_id,
        "username": username,
        "email": email.strip().lower(),
        "payment_link": payment_link,
    }


def db_mark_subscription_request_emailed(request_id: str) -> None:
    db = market_core.get_db()
    db.execute(
        "UPDATE subscription_requests SET email_sent=1 WHERE id=?",
        (request_id,),
    )
    db.commit()
    db.close()


def db_recent_subscription_request(email: str, hours: int = 24) -> dict | None:
    db = market_core.get_db()
    row = db.execute(
        """
        SELECT id, username, email, status, payment_link, email_sent, created_at
        FROM subscription_requests
        WHERE email=?
        ORDER BY created_at DESC LIMIT 1
        """,
        (email.strip().lower(),),
    ).fetchone()
    db.close()
    if not row:
        return None
    row = dict(row)
    created = row.get("created_at")
    if created and hours > 0:
        try:
            if isinstance(created, str):
                ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            else:
                ts = created
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - ts > timedelta(hours=hours):
                return None
        except (ValueError, TypeError):
            pass
    return row


def db_find_subscription_request(*, request_id: str = "", email: str = "") -> dict | None:
    db = market_core.get_db()
    if request_id:
        row = db.execute(
            "SELECT id, username, email, status, payment_link, email_sent, created_at "
            "FROM subscription_requests WHERE id=?",
            (request_id,),
        ).fetchone()
    elif email:
        row = db.execute(
            "SELECT id, username, email, status, payment_link, email_sent, created_at "
            "FROM subscription_requests WHERE email=? ORDER BY created_at DESC LIMIT 1",
            (email.strip().lower(),),
        ).fetchone()
    else:
        db.close()
        return None
    db.close()
    return dict(row) if row else None


def db_mark_subscription_request_activated(request_id: str, username: str = "") -> bool:
    db = market_core.get_db()
    if username:
        cur = db.execute(
            "UPDATE subscription_requests SET status='activated', username=? WHERE id=?",
            (username, request_id),
        )
    else:
        cur = db.execute(
            "UPDATE subscription_requests SET status='activated' WHERE id=?",
            (request_id,),
        )
    db.commit()
    updated = cur.rowcount > 0
    db.close()
    return updated
