"""Account summary for customer dashboard (CLI + landing + API)."""

from __future__ import annotations

import time
from typing import Any

from market_core import TIERS, db_get_subscription, db_list_api_keys, get_db


def _rate_count(db, key: str, window_start: float) -> int:
    row = db.execute(
        "SELECT SUM(counter) as n FROM rate_limits WHERE key=? AND window_start >= ?",
        (key, window_start),
    ).fetchone()
    return int(row["n"] or 0)


def _daily_window_start() -> float:
    now = time.time()
    return time.mktime(time.strptime(time.strftime("%Y-%m-%d", time.gmtime(now)), "%Y-%m-%d"))


def upgrade_next_step(tier: str, *, lang: str = "es") -> dict[str, Any]:
    tier_l = (tier or "free").lower()
    es = lang == "es"
    steps = {
        "free": {
            "next_tier": "starter",
            "title_es": "Starter — alertas y export CSV",
            "title_en": "Starter — alerts and CSV export",
            "cli_es": "market register  →  solicitar Starter en cli-market.dev/#pricing",
            "cli_en": "market register  →  request Starter at cli-market.dev/#pricing",
            "url": "https://cli-market.dev/#pricing",
            "cta_es": "Solicitar Starter",
            "cta_en": "Request Starter",
        },
        "starter": {
            "next_tier": "pro",
            "title_es": "Pro — checkout y 10k req/día",
            "title_en": "Pro — checkout and 10k req/day",
            "cli_es": "market upgrade",
            "cli_en": "market upgrade",
            "url": "https://cli-market.dev/#pro-checkout",
            "cta_es": "Activar Pro (PayPal)",
            "cta_en": "Activate Pro (PayPal)",
        },
        "pro": {
            "next_tier": "builder",
            "title_es": "Builder — Intelligence API completa",
            "title_en": "Builder — full Intelligence API",
            "cli_es": "cli-market.dev/#pricing-build",
            "cli_en": "cli-market.dev/#pricing-build",
            "url": "https://cli-market.dev/#pricing-build",
            "cta_es": "Contactar ventas",
            "cta_en": "Contact sales",
        },
    }
    if tier_l in ("builder", "enterprise"):
        return {
            "next_tier": None,
            "title": "Plan máximo" if es else "Top tier",
            "cli": "market doctor",
            "url": None,
            "cta": None,
        }
    step = steps.get(tier_l, steps["free"])
    return {
        "next_tier": step["next_tier"],
        "title": step["title_es"] if es else step["title_en"],
        "cli": step["cli_es"] if es else step["cli_en"],
        "url": step["url"],
        "cta": step["cta_es"] if es else step["cta_en"],
    }


def build_account_summary(username: str, *, lang: str = "es") -> dict[str, Any]:
    sub = db_get_subscription(username)
    tier = sub.get("tier", "free")
    tier_cfg = TIERS.get(tier, TIERS["free"])
    keys = db_list_api_keys(username)
    keys_limit = tier_cfg.get("api_keys", 1)

    now = time.time()
    today_start = _daily_window_start()
    db = get_db()
    daily_used = _rate_count(db, f"{username}:daily", today_start)
    minute_used = _rate_count(db, f"{username}:min", now - 60)
    db.close()

    day_limit = sub.get("req_limit_day", tier_cfg["req_day"])
    min_limit = sub.get("req_limit_min", tier_cfg["req_min"])

    def pct(used: int, limit: int) -> float | None:
        if limit is None or limit < 0:
            return None
        return round(min(used / limit, 1.0) * 100, 1) if limit else 0.0

    return {
        "username": username,
        "tier": tier,
        "limits": {
            "req_day": day_limit if day_limit != -1 else "unlimited",
            "req_min": min_limit if min_limit != -1 else "unlimited",
            "api_keys": keys_limit if keys_limit != -1 else "unlimited",
            "checkout": bool(tier_cfg.get("checkout")),
            "alerts": tier_cfg.get("alerts", 0),
            "export": bool(tier_cfg.get("export")),
            "history_days": tier_cfg.get("history_days", 7),
        },
        "usage": {
            "requests_today": daily_used,
            "requests_last_minute": minute_used,
            "api_keys_used": len(keys),
            "daily_pct": pct(daily_used, day_limit if day_limit != -1 else 0),
            "minute_pct": pct(minute_used, min_limit if min_limit != -1 else 0),
        },
        "upgrade": upgrade_next_step(tier, lang=lang),
    }