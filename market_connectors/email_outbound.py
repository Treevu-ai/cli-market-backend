"""Outbound email for billing and onboarding (stdlib SMTP)."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

FROM_EMAIL = os.getenv("BILLING_FROM_EMAIL", "hello@cli-market.dev")
FROM_NAME = os.getenv("BILLING_FROM_NAME", "CLI Market")
NOTIFY_EMAIL = os.getenv("BILLING_NOTIFY_EMAIL", "hello@cli-market.dev")
PRO_PAYMENT_URL = os.getenv(
    "PRO_PAYMENT_URL",
    "https://www.paypal.com/ncp/payment/B6YVFTG4MA73J",
)
PRO_PRICE_LABEL = os.getenv("PRO_PRICE_LABEL", "$49/month")


def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))


def send_pro_payment_email(
    *,
    to_email: str,
    username: str,
    request_id: str,
    lang: str = "en",
) -> dict:
    """Email subscriber with Pro plan details and payment link."""
    payment_url = PRO_PAYMENT_URL
    if lang == "es":
        subject = f"CLI Market Pro — link de pago ({PRO_PRICE_LABEL})"
        text = f"""Hola,

Recibimos tu solicitud de CLI Market Pro (ref: {request_id}).

Plan Pro — {PRO_PRICE_LABEL}
• 10,000 consultas API / día
• Checkout automatizado + exportación de precios
• 10 claves API

Paga aquí: {payment_url}

Después de pagar, responde a este correo con:
• Tu usuario CLI (ej. el que usaste en `market login`)
• Referencia: {request_id}

Activamos Pro en 24 h.

— CLI Market
hello@cli-market.dev
"""
    else:
        subject = f"CLI Market Pro — payment link ({PRO_PRICE_LABEL})"
        text = f"""Hi,

We received your CLI Market Pro request (ref: {request_id}).

Pro plan — {PRO_PRICE_LABEL}
• 10,000 API requests / day
• Automated checkout + price export
• 10 API keys

Pay here: {payment_url}

After payment, reply to this email with:
• Your CLI username (from `market login`)
• Reference: {request_id}

We activate Pro within 24 hours.

— CLI Market
hello@cli-market.dev
"""
    html = text.replace("\n", "<br>\n")
    return _send(to_email, subject, text, html)


def send_pro_request_notify(
    *,
    subscriber_email: str,
    username: str,
    request_id: str,
    note: str = "",
) -> dict:
    """Notify hello@cli-market.dev of a new Pro request."""
    subject = f"[Pro request] {username} — {request_id}"
    text = (
        f"New Pro subscription request\n\n"
        f"Request ID: {request_id}\n"
        f"Username: {username}\n"
        f"Email: {subscriber_email}\n"
        f"Payment link sent: {PRO_PAYMENT_URL}\n"
    )
    if note.strip():
        text += f"\nUse case / note:\n{note.strip()}\n"
    text += (
        f"\nAfter payment confirmed, run:\n"
        f"  python3 ops/activate_pro.py {username}\n"
    )
    return _send(NOTIFY_EMAIL, subject, text, f"<pre>{text}</pre>")


def _send(to_email: str, subject: str, text: str, html: str) -> dict:
    if not _smtp_configured():
        logger.warning("SMTP not configured — email not sent to %s", to_email)
        return {"sent": False, "reason": "smtp_not_configured", "to": to_email}

    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Reply-To"] = FROM_EMAIL
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    timeout = int(os.getenv("SMTP_TIMEOUT", "5"))
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return {"sent": True, "to": to_email}
    except Exception as e:
        logger.exception("Failed to send email to %s", to_email)
        return {"sent": False, "reason": str(e), "to": to_email}
