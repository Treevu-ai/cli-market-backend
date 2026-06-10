"""Onboarding funnel events — P3 instrumentation."""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from market_core import USE_PG, get_db

_TEST_EMAIL_DOMAINS = frozenset({"cli-market.dev", "example.com", "test.com"})
_TEST_USERNAME_PREFIXES = (
    "smoke+",
    "pam+",
    "smoke-test",
    "pam-user",
    "deploy-check",
    "quick-check",
    "now-check",
    "final-check",
    "slow-check",
    "pro-smtp-test",
    "smtp-debug",
)
_PAM_AUTO_USER = re.compile(r"^user-[a-f0-9]{12}$", re.IGNORECASE)

FUNNEL_EVENTS = frozenset(
    {
        "install",
        "login",
        "register",
        "first_search",
        "starter_subscribe",
        "starter_request",
        "request_pro",
        "activated",
    }
)

_DIGEST_EVENTS = frozenset(
    {
        "register",
        "first_search",
        "starter_subscribe",
        "starter_request",
        "request_pro",
        "activated",
    }
)

_FUNNEL_DDL_PG = """
CREATE TABLE IF NOT EXISTS funnel_events (
    id SERIAL PRIMARY KEY,
    event TEXT NOT NULL,
    username TEXT,
    session_id TEXT,
    meta TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_FUNNEL_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS funnel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    username TEXT,
    session_id TEXT,
    meta TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _parse_meta(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def is_test_funnel_traffic(
    username: str | None,
    meta: dict[str, Any] | None = None,
) -> bool:
    """True for smoke/PAM/CI traffic and other internal test patterns."""
    meta = meta or {}
    if meta.get("source") == "test":
        return True

    email = (meta.get("email") or "").strip().lower()
    if email:
        local, _, domain = email.partition("@")
        if domain in _TEST_EMAIL_DOMAINS:
            return True
        if local.startswith(("smoke+", "pam+")) or local in {"smoke-test", "deploy-check", "test"}:
            return True

    user = (username or "").strip().lower()
    if not user:
        return False
    if user == "test" or user.startswith(_TEST_USERNAME_PREFIXES):
        return True
    return bool(_PAM_AUTO_USER.match(user))


def ensure_funnel_schema() -> None:
    db = get_db()
    db.execute(_FUNNEL_DDL_PG if USE_PG else _FUNNEL_DDL_SQLITE)
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_funnel_event ON funnel_events(event)",
        "CREATE INDEX IF NOT EXISTS idx_funnel_user ON funnel_events(username)",
        "CREATE INDEX IF NOT EXISTS idx_funnel_created ON funnel_events(created_at)",
    ):
        db.execute(idx_sql)
    db.commit()
    db.close()


def record_funnel_event(
    event: str,
    *,
    username: str | None = None,
    session_id: str | None = None,
    meta: dict[str, Any] | None = None,
    dedupe: bool = False,
) -> dict[str, Any]:
    """Persist one funnel event. dedupe=True skips if same user+event exists."""
    event = (event or "").strip().lower()
    if event not in FUNNEL_EVENTS:
        return {"ok": False, "error": f"unknown event: {event}"}

    ensure_funnel_schema()
    user = (username or "").strip() or None
    sid = (session_id or "").strip() or None
    payload = dict(meta or {})
    if is_test_funnel_traffic(user, payload):
        payload.setdefault("source", "test")

    if dedupe:
        db = get_db()
        row = None
        if user:
            row = db.execute(
                "SELECT id FROM funnel_events WHERE event=? AND username=? LIMIT 1",
                (event, user),
            ).fetchone()
        elif sid and event == "install":
            row = db.execute(
                "SELECT id FROM funnel_events WHERE event=? AND session_id=? LIMIT 1",
                (event, sid),
            ).fetchone()
        db.close()
        if row:
            return {"ok": True, "deduped": True, "event": event}

    db = get_db()
    db.execute(
        """
        INSERT INTO funnel_events (event, username, session_id, meta)
        VALUES (?, ?, ?, ?)
        """,
        (event, user, sid, json.dumps(payload, ensure_ascii=False)),
    )
    db.commit()
    db.close()
    return {"ok": True, "event": event, "username": user}


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _median_minutes(pairs: list[float]) -> float | None:
    if not pairs:
        return None
    return round(statistics.median(pairs), 1)


def funnel_summary(*, days: int = 30, include_test: bool = False) -> dict[str, Any]:
    ensure_funnel_schema()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    rows = db.execute(
        "SELECT event, username, meta, created_at FROM funnel_events WHERE created_at >= ?",
        (since,),
    ).fetchall()
    db.close()

    events: dict[str, int] = {e: 0 for e in FUNNEL_EVENTS}
    by_user: dict[str, dict[str, datetime]] = {}
    excluded_test_events = 0

    for row in rows:
        meta = _parse_meta(row["meta"])
        user = row["username"]
        if not include_test and is_test_funnel_traffic(user, meta):
            excluded_test_events += 1
            continue

        ev = row["event"]
        events[ev] = events.get(ev, 0) + 1
        if not user:
            continue
        ts = _parse_ts(row["created_at"])
        if not ts:
            continue
        by_user.setdefault(user, {})[ev] = ts

    register_n = events.get("register", 0) or len(
        {u for u, evs in by_user.items() if "register" in evs}
    )
    search_users = {u for u, evs in by_user.items() if "first_search" in evs}
    starter_sub_users = {u for u, evs in by_user.items() if "starter_subscribe" in evs}
    pro_users = {u for u, evs in by_user.items() if "request_pro" in evs}
    activated_users = {u for u, evs in by_user.items() if "activated" in evs}

    ttfv: list[float] = []
    for evs in by_user.values():
        if "register" in evs and "first_search" in evs:
            delta = (evs["first_search"] - evs["register"]).total_seconds() / 60
            if delta >= 0:
                ttfv.append(delta)

    ttc: list[float] = []
    for evs in by_user.values():
        if "request_pro" in evs and "activated" in evs:
            delta = (evs["activated"] - evs["request_pro"]).total_seconds() / 3600
            if delta >= 0:
                ttc.append(delta)

    def conv(num: int, den: int) -> float | None:
        if den <= 0:
            return None
        return round(num / den, 3)

    starter_sub_n = max(events.get("starter_subscribe", 0), len(starter_sub_users))
    steps = [
        ("install", events.get("install", 0)),
        ("register", max(register_n, events.get("register", 0))),
        ("first_search", len(search_users)),
        ("starter_subscribe", starter_sub_n),
        ("request_pro", len(pro_users)),
        ("activated", len(activated_users)),
    ]
    funnel_steps = []
    prev = None
    for name, count in steps:
        drop = None
        if prev is not None and prev > 0:
            drop = round((1 - count / prev) * 100, 1) if count <= prev else 0.0
        funnel_steps.append({"step": name, "count": count, "drop_off_pct": drop})
        if count > 0:
            prev = count

    return {
        "window_days": days,
        "events": events,
        "unique_users": {
            "with_search": len(search_users),
            "with_starter_subscribe": len(starter_sub_users),
            "with_pro_request": len(pro_users),
            "activated": len(activated_users),
        },
        "conversion": {
            "register_to_search": conv(len(search_users), register_n),
            "search_to_starter": conv(starter_sub_n, len(search_users)),
            "search_to_pro": conv(len(pro_users), len(search_users)),
            "pro_to_activated": conv(len(activated_users), len(pro_users)),
        },
        "ttfv_median_minutes": _median_minutes(ttfv),
        "ttc_median_hours": _median_minutes(ttc),
        "funnel_steps": funnel_steps,
        "excluded_test_events": excluded_test_events,
        "includes_test_traffic": include_test,
    }


def funnel_recent_events(
    *,
    hours: int = 24,
    exclude_test: bool = True,
    exclude_noise: bool | None = None,
) -> list[dict[str, Any]]:
    """Recent funnel rows for Slack digest (newest first)."""
    if exclude_noise is not None:
        exclude_test = exclude_noise
    ensure_funnel_schema()
    hours = max(1, min(hours, 168))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    db = get_db()
    rows = db.execute(
        """
        SELECT event, username, meta, created_at
        FROM funnel_events
        WHERE created_at >= ? AND event IN ({})
        ORDER BY created_at DESC
        """.format(",".join("?" * len(_DIGEST_EVENTS))),
        (since, *_DIGEST_EVENTS),
    ).fetchall()
    db.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        user = row["username"] or ""
        meta = _parse_meta(row["meta"])
        if exclude_test and is_test_funnel_traffic(user, meta):
            continue
        out.append(
            {
                "event": row["event"],
                "username": user,
                "meta": meta,
                "created_at": row["created_at"],
            }
        )
    return out


def funnel_digest_counts(
    *,
    hours: int = 24,
    exclude_test: bool = True,
    exclude_noise: bool | None = None,
) -> dict[str, int]:
    """Event counts in the digest window."""
    if exclude_noise is not None:
        exclude_test = exclude_noise
    counts = {e: 0 for e in _DIGEST_EVENTS}
    for row in funnel_recent_events(hours=hours, exclude_test=exclude_test):
        ev = row.get("event", "")
        if ev in counts:
            counts[ev] += 1
    return counts


def maybe_first_search(username: str, *, query: str = "") -> None:
    record_funnel_event(
        "first_search",
        username=username,
        meta={"query": query[:80]} if query else None,
        dedupe=True,
    )