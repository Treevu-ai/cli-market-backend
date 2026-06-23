"""Slack notifications — revenue → #suscripciones-cli-pro, funnel → #funnel-cli-market."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

TIER_LABELS: dict[str, str] = {
    "pro": "CLI Market Pro ($39)",
    "starter": "CLI Market Starter ($24)",
    "procure_starter": "Procure Starter ($29)",
    "procure_pro": "Procure Pro ($79)",
    "procure_builder": "Procure Builder ($149)",
}

_FUNNEL_QUIET_DEFAULT = frozenset({"install", "login", "first_search"})

_FUNNEL_LABELS: dict[str, str] = {
    "install": "pip install / primera ejecución",
    "register": "registro de cuenta",
    "login": "login CLI",
    "first_search": "primera búsqueda",
    "starter_subscribe": "checkout Starter iniciado",
    "starter_request": "solicitud Starter",
    "request_pro": "checkout Pro iniciado",
    "activated": "suscripción activada",
    "procure_subscribe": "checkout Procure iniciado",
    "onboarding_complete": "onboarding completo (market init)",
}


def _subscription_slack_ready() -> bool:
    return bool(
        os.getenv("SLACK_BOT_TOKEN", "").strip()
        or os.getenv("SLACK_WEBHOOK_CLI_MARKET_PRO", "").strip()
    )


def _funnel_slack_ready() -> bool:
    return bool(
        os.getenv("SLACK_BOT_TOKEN", "").strip()
        or os.getenv("SLACK_WEBHOOK_FUNNEL", "").strip()
    )


def _funnel_realtime_enabled() -> bool:
    return os.getenv("SLACK_FUNNEL_REALTIME", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def tier_label(tier: str) -> str:
    key = (tier or "").strip().lower()
    return TIER_LABELS.get(key, key or "suscripción")


def format_subscription_message(
    *,
    tier: str,
    status: str,
    username: str = "",
    email: str = "",
    request_id: str = "",
    source: str = "",
    payment_method: str = "",
    amount_pen: float | None = None,
    amount_usd: float | None = None,
    plan: str = "",
) -> str:
    """Human-readable Slack mrkdwn for #suscripciones-cli-pro."""
    st = (status or "activated").strip().lower()
    label = tier_label(tier)
    if plan:
        label = f"{label} · {plan}"
    user = (username or "—").strip()
    mail = (email or "—").strip()
    ref = (request_id or "—").strip()
    src = (source or "—").strip()
    method = (payment_method or "—").strip()

    if st == "cancelled":
        reason = (source or "paypal_webhook").strip()
        lines = [
            f"❌ *[REVENUE]* {label} — suscripción cancelada",
            f"• usuario: `{user}`",
            f"• email: {mail}",
            f"• ref: `{ref}`",
            f"• evento: {reason}",
            "• tier actualizado → `free`",
        ]
        return "\n".join(lines)

    if st == "pending":
        lines = [
            f"⏳ *[REVENUE]* {label} — pago pendiente",
            f"• ref: `{ref}`",
            f"• usuario CLI: `{user}`",
            f"• email: {mail}",
            f"• método: {method}",
        ]
        if amount_pen is not None:
            lines.append(f"• monto: S/ {amount_pen:.2f}")
        if amount_usd is not None:
            lines.append(f"• plan: ${amount_usd:.0f}/mes")
        if (tier or "").strip().lower() == "pro":
            lines.append(
                f"• activar tras confirmar: `python ops/slack_cli.py activate-pro {ref}`"
            )
        lines.append(
            f"• cliente: `market whoami` cada hora · email hello@cli-market.dev con ref `{ref}`"
        )
        return "\n".join(lines)

    lines = [
        f"✅ *[REVENUE]* {label} — suscripción activa",
        f"• usuario: `{user}`",
        f"• email: {mail}",
        f"• ref: `{ref}`",
        f"• método: {method}",
        f"• fuente: {src}",
        f"• verificar: `market whoami` → tier {(tier or 'pro').strip().lower()}",
    ]
    return "\n".join(lines)


def format_funnel_message(
    *,
    event: str,
    username: str = "",
    meta: dict | None = None,
) -> str:
    """Adoption funnel events (install, register, checkout starts)."""
    ev = (event or "").strip().lower()
    label = _FUNNEL_LABELS.get(ev, ev or "evento")
    user = (username or "—").strip()
    meta = meta or {}
    tier = (meta.get("tier") or meta.get("plan") or "").strip()
    email = (meta.get("email") or "").strip()
    source = (meta.get("source") or "").strip()
    lines = [f"📥 *[FUNNEL]* {label}*", f"• usuario: `{user}`"]
    if tier:
        lines.append(f"• tier: {tier_label(tier) if tier in TIER_LABELS else tier}")
    if email:
        lines.append(f"• email: {email}")
    if source:
        lines.append(f"• fuente: {source}")
    return "\n".join(lines)


def pro_pending_slack_blocks(
    *,
    username: str = "",
    email: str = "",
    request_id: str = "",
    payment_method: str = "",
    amount_pen: float | None = None,
) -> list:
    """Slack blocks with Activar button (Pro manual Yape/Plin only)."""
    ref = (request_id or "—").strip()
    user = (username or "—").strip()
    mail = (email or "—").strip()
    method = (payment_method or "—").strip()
    lines = [
        "⏳ *[REVENUE]* CLI Market Pro ($39) — pago pendiente",
        f"• ref: `{ref}` · usuario: `{user}`",
        f"• email: {mail} · método: {method}",
    ]
    if amount_pen is not None:
        lines.append(f"• monto: S/ {amount_pen:.2f}")
    lines.append(f"• CLI fallback: `python ops/slack_cli.py activate-pro {ref}`")
    lines.append(
        f"• cliente: `market whoami` cada hora · email hello@cli-market.dev con ref `{ref}`"
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "activate_pro_request",
                    "text": {"type": "plain_text", "text": "Activar Pro"},
                    "style": "primary",
                    "value": ref,
                }
            ],
        },
    ]


def notify_subscription(
    *,
    tier: str,
    status: str = "activated",
    username: str = "",
    email: str = "",
    request_id: str = "",
    source: str = "",
    payment_method: str = "",
    amount_pen: float | None = None,
    amount_usd: float | None = None,
    plan: str = "",
) -> bool:
    """Post subscription event to #suscripciones-cli-pro. Never raises."""
    if not _subscription_slack_ready():
        logger.debug("Slack not configured; skip subscription notify")
        return False
    try:
        from slack_notify import deliver_to_cli_market_pro

        text = format_subscription_message(
            tier=tier,
            status=status,
            username=username,
            email=email,
            request_id=request_id,
            source=source,
            payment_method=payment_method,
            amount_pen=amount_pen,
            amount_usd=amount_usd,
            plan=plan,
        )
        blocks = None
        if (
            (status or "").strip().lower() == "pending"
            and (tier or "").strip().lower() == "pro"
            and os.getenv("SLACK_BOT_TOKEN", "").strip()
        ):
            blocks = pro_pending_slack_blocks(
                username=username,
                email=email,
                request_id=request_id,
                payment_method=payment_method,
                amount_pen=amount_pen,
            )
        deliver_to_cli_market_pro(text, blocks=blocks)
        return True
    except Exception as exc:
        logger.warning("Subscription Slack notify failed: %s", exc)
        return False


def _funnel_slack_enabled(event: str) -> bool:
    ev = (event or "").strip().lower()
    if os.getenv("SLACK_FUNNEL_VERBOSE", "").strip().lower() in ("1", "true", "yes"):
        return True
    return ev not in _FUNNEL_QUIET_DEFAULT


def notify_funnel_event(
    *,
    event: str,
    username: str = "",
    meta: dict | None = None,
) -> bool:
    """Post adoption funnel events to #funnel-cli-market when realtime mode is on."""
    ev = (event or "").strip().lower()
    if ev not in _FUNNEL_LABELS:
        return False
    if not _funnel_slack_enabled(ev):
        return False
    if not _funnel_realtime_enabled():
        return False
    if not _funnel_slack_ready():
        return False
    try:
        from slack_notify import deliver_to_funnel

        deliver_to_funnel(
            format_funnel_message(event=ev, username=username, meta=meta),
        )
        return True
    except Exception as exc:
        logger.warning("Funnel Slack notify failed: %s", exc)
        return False


def format_funnel_digest_message(*, hours: int = 24) -> str:
    """Daily adoption summary for #funnel-cli-market."""
    from market_funnel import funnel_digest_counts, funnel_recent_events, funnel_summary

    hours = max(1, min(hours, 168))
    counts = funnel_digest_counts(hours=hours, exclude_noise=True)
    rows = funnel_recent_events(hours=hours, exclude_noise=True)
    summary = funnel_summary(days=1, include_test=False)
    conv = summary.get("conversion") or {}

    lines = [
        f"📊 *[FUNNEL DIGEST]* últimas {hours}h",
        "",
        "*Conteos*",
    ]
    for ev, label in (
        ("register", "registros"),
        ("first_search", "primeras búsquedas"),
        ("starter_subscribe", "checkout Starter"),
        ("request_pro", "checkout Pro"),
        ("procure_subscribe", "checkout Procure"),
        ("onboarding_complete", "market init completado"),
        ("activated", "activaciones (evento)"),
    ):
        n = counts.get(ev, 0)
        if n:
            lines.append(f"• {label}: {n}")

    if not any(counts.values()):
        lines.append("• sin eventos de adopción en la ventana")

    lines.extend(["", "*Conversión (24h)*"])
    for key, label in (
        ("register_to_search", "registro → búsqueda"),
        ("search_to_pro", "búsqueda → checkout Pro"),
        ("pro_to_activated", "checkout Pro → activado"),
    ):
        val = conv.get(key)
        if val is not None:
            lines.append(f"• {label}: {val * 100:.1f}%")

    ttfv = summary.get("ttfv_median_minutes")
    if ttfv is not None:
        lines.append(f"• TTFV mediana: {ttfv:.0f} min")

    if rows:
        lines.extend(["", "*Últimos eventos*"])
        for row in rows[:12]:
            ev = row.get("event", "")
            user = (row.get("username") or "—").strip() or "—"
            label = _FUNNEL_LABELS.get(ev, ev)
            src = (row.get("meta") or {}).get("source", "")
            extra = f" · {src}" if src else ""
            lines.append(f"• {label}: `{user}`{extra}")

    return "\n".join(lines)


def notify_funnel_digest(*, hours: int = 24) -> bool:
    """Post funnel digest to #funnel-cli-market. Never raises."""
    if not _funnel_slack_ready():
        logger.debug("Funnel Slack not configured; skip digest")
        return False
    try:
        from slack_notify import deliver_to_funnel

        deliver_to_funnel(format_funnel_digest_message(hours=hours))
        return True
    except Exception as exc:
        logger.warning("Funnel digest Slack notify failed: %s", exc)
        return False


def notify_new_registration(
    *,
    username: str,
    email: str = "",
    ref_code: str = "",
    api_key_prefix: str = "",
) -> bool:
    """Send outreach-ready registration card to #funnel-cli-market.

    Fires whenever Slack is configured — not gated by SLACK_FUNNEL_REALTIME.
    """
    if not _funnel_slack_ready():
        return False
    try:
        import datetime

        from slack_notify import deliver_to_funnel

        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            "🆕 *[REGISTRO]* nuevo usuario",
            f"• usuario: `{username}`",
        ]
        if email:
            lines.append(f"• email: {email}")
        else:
            lines.append("• email: —  _(no proporcionado)_")
        if ref_code:
            lines.append(f"• ref_code: `{ref_code}`")
        if api_key_prefix:
            lines.append(f"• api_key: `{api_key_prefix}…`")
        lines.append(f"• fecha: {now}")
        deliver_to_funnel("\n".join(lines))
        return True
    except Exception as exc:
        logger.warning("notify_new_registration Slack failed: %s", exc)
        return False


# Backward-compatible aliases
def format_pro_subscription_message(**kwargs) -> str:
    kwargs.setdefault("tier", "pro")
    return format_subscription_message(**kwargs)


def notify_pro_subscription(**kwargs) -> bool:
    kwargs.setdefault("tier", "pro")
    return notify_subscription(**kwargs)


def notify_subscription_cancelled(
    *,
    tier: str,
    username: str,
    email: str = "",
    request_id: str = "",
    event_type: str = "",
    payment_method: str = "paypal",
) -> bool:
    """Churn alert when PayPal downgrades a paid tier to free."""
    t = (tier or "").strip().lower()
    if t not in TIER_LABELS:
        return False
    return notify_subscription(
        tier=t,
        status="cancelled",
        username=username,
        email=email,
        request_id=request_id,
        source=event_type or "paypal_webhook",
        payment_method=payment_method,
    )


def notify_build_pro_tier_activated(
    *,
    tier: str,
    username: str,
    email: str = "",
    request_id: str = "",
    source: str = "",
    payment_method: str = "",
) -> bool:
    """Activated subscription — any paid tier."""
    t = (tier or "").strip().lower()
    if t not in TIER_LABELS:
        return False
    return notify_subscription(
        tier=t,
        status="activated",
        username=username,
        email=email,
        request_id=request_id,
        source=source,
        payment_method=payment_method,
    )