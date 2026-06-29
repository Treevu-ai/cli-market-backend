#!/usr/bin/env python3
"""Daily grace period job for Procure one-shot MP subscriptions.

Sends renewal reminder emails and downgrades expired accounts:
  D-3  → reminder "vence en 3 días"
  D-0  → reminder "vence hoy" (runs day of expiry)
  D+3  → reminder "venció hace 3 días, 4 días restantes"
  D+7  → downgrade to free + notification email

Usage:
  py ops/procure_grace_period_job.py [--dry-run]

Env vars required (same as backend):
  DATABASE_URL or RAILWAY_PRIVATE_DOMAIN  — Postgres connection
  SMTP_HOST / SMTP_USER / SMTP_PASSWORD   — email sending
  PROCURE_APP_URL                         — override dashboard URL (optional)

Schedule: daily cron at 09:00 UTC (Railway cron or GitHub Actions).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GRACE_DAYS = 7
REMIND_DAYS_BEFORE = [3, 0]
REMIND_DAYS_AFTER = [3]  # D+7 is downgrade, not a reminder
PROCURE_TIERS = frozenset({"procure_starter", "procure_pro", "procure_builder"})
RENEWAL_BASE_URL = os.getenv("PROCURE_APP_URL", "https://procurecopilot.com") + "/#pricing"


def _db():
    from market_core import get_db
    return get_db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(val) -> datetime | None:
    if not val:
        return None
    try:
        s = str(val).replace("Z", "+00:00")
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _fetch_procure_subscriptions(db) -> list[dict]:
    """Return all rows with a procure_* tier and a non-null expires_at."""
    rows = db.execute(
        "SELECT username, tier, expires_at FROM subscriptions "
        "WHERE tier IN ({}) AND expires_at IS NOT NULL".format(
            ",".join("?" * len(PROCURE_TIERS))
        ),
        tuple(PROCURE_TIERS),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_email(db, username: str) -> str:
    from market_core import db_get_user_email
    return (db_get_user_email(username) or "").strip()


def _already_sent_today(db, username: str, event_key: str) -> bool:
    """Deduplicate: skip if we already sent this event today."""
    today = _now().date().isoformat()
    key = f"procure_grace:{username}:{event_key}:{today}"
    try:
        from market_core import db_claim_webhook_event
        return not db_claim_webhook_event(key, source="procure_grace_period_job")
    except Exception:
        return False


def process_subscription(row: dict, *, dry_run: bool, db) -> str:
    username = row["username"]
    tier = row["tier"]
    expires_dt = _parse_dt(row["expires_at"])
    if not expires_dt:
        return f"skip:{username} (unparseable expires_at)"

    now = _now()
    grace_end = expires_dt + timedelta(days=GRACE_DAYS)
    # Use date-based delta so the job behaves consistently regardless of the
    # exact time-of-day stored in expires_at (avoids ±1-day drift on daily runs).
    today = now.date()
    expires_date = expires_dt.date()
    grace_end_date = grace_end.date()

    email = _get_email(db, username)
    renewal_url = f"{RENEWAL_BASE_URL}"
    grace_ends_on = grace_end.strftime("%Y-%m-%d")

    # ── D+7 downgrade ────────────────────────────────────────────────────────
    if today >= grace_end_date:
        event_key = "downgrade"
        if _already_sent_today(db, username, event_key):
            return f"skip:{username} (downgrade already sent today)"
        logger.info("DOWNGRADE %s tier=%s expired=%s", username, tier, expires_date)
        if not dry_run:
            from market_core import db_set_subscription
            db_set_subscription(username, "free")
            if email:
                from market_connectors.email_outbound import send_procure_downgrade_email
                send_procure_downgrade_email(
                    to_email=email,
                    username=username,
                    plan=tier,
                    renewal_url=renewal_url,
                )
        return f"downgraded:{username}"

    # ── In grace period (D+1 to D+6) ────────────────────────────────────────
    if today > expires_date:
        days_over = (today - expires_date).days
        if days_over not in REMIND_DAYS_AFTER:
            return f"skip:{username} (D+{days_over} not a reminder day)"
        event_key = f"remind_D+{days_over}"
        if _already_sent_today(db, username, event_key):
            return f"skip:{username} ({event_key} already sent)"
        logger.info("REMIND %s D+%d tier=%s", username, days_over, tier)
        if not dry_run and email:
            from market_connectors.email_outbound import send_procure_renewal_reminder_email
            send_procure_renewal_reminder_email(
                to_email=email,
                username=username,
                plan=tier,
                days_until_expiry=-days_over,
                renewal_url=renewal_url,
                grace_ends_on=grace_ends_on,
            )
        return f"reminded:{username}:D+{days_over}"

    # ── Before expiry ────────────────────────────────────────────────────────
    days_before = (expires_date - today).days
    if days_before not in REMIND_DAYS_BEFORE:
        return f"skip:{username} (D-{days_before} not a reminder day)"
    event_key = f"remind_D-{days_before}"
    if _already_sent_today(db, username, event_key):
        return f"skip:{username} ({event_key} already sent)"
    logger.info("REMIND %s D-%d tier=%s", username, days_before, tier)
    if not dry_run and email:
        from market_connectors.email_outbound import send_procure_renewal_reminder_email
        send_procure_renewal_reminder_email(
            to_email=email,
            username=username,
            plan=tier,
            days_until_expiry=days_before,
            renewal_url=renewal_url,
            grace_ends_on=grace_ends_on,
        )
    return f"reminded:{username}:D-{days_before}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Log actions without sending emails or downgrading")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN — no emails sent, no downgrades applied")

    try:
        db = _db()
    except Exception as exc:
        logger.error("DB connection failed: %s", exc)
        return 1

    rows = _fetch_procure_subscriptions(db)
    logger.info("Found %d Procure subscriptions with expires_at", len(rows))

    results: list[str] = []
    for row in rows:
        try:
            result = process_subscription(row, dry_run=args.dry_run, db=db)
            results.append(result)
            logger.info(result)
        except Exception as exc:
            logger.exception("Error processing %s: %s", row.get("username"), exc)
            results.append(f"error:{row.get('username')}")

    db.close()

    downgrades = sum(1 for r in results if r.startswith("downgraded:"))
    reminders = sum(1 for r in results if r.startswith("reminded:"))
    errors = sum(1 for r in results if r.startswith("error:"))
    logger.info("Done. downgrades=%d reminders=%d errors=%d", downgrades, reminders, errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
