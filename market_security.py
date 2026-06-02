"""Shared security helpers for payment webhooks and outbound URL fetches."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata",
    }
)


def is_production_deploy() -> bool:
    """True when running a live/public deployment (not local sandbox defaults)."""
    if os.getenv("MARKET_ENV", "").lower() == "production":
        return True
    if os.getenv("RAILWAY_ENVIRONMENT", "").lower() == "production":
        return True
    return os.getenv("PAYPAL_SANDBOX", "true").lower() not in ("1", "true", "yes")


def production_payment_config_warnings() -> list[str]:
    """Non-fatal startup warnings for missing production payment secrets."""
    if not is_production_deploy():
        return []
    warnings: list[str] = []
    if not os.getenv("PAYPAL_WEBHOOK_ID", "").strip():
        warnings.append("PAYPAL_WEBHOOK_ID unset — PayPal webhooks will be rejected")
    if not os.getenv("CHECKOUT_WEBHOOK_SECRET", "").strip():
        warnings.append("CHECKOUT_WEBHOOK_SECRET unset — POST /checkout/webhook disabled")
    return warnings


def paypal_allow_unverified_webhooks() -> bool:
    """Local sandbox only: explicit opt-in to accept unsigned PayPal webhooks."""
    if is_production_deploy():
        return False
    return os.getenv("PAYPAL_ALLOW_UNVERIFIED_WEBHOOKS", "").lower() in ("1", "true", "yes")


def validate_public_http_url(url: str) -> str:
    """Validate a URL is safe for server-side HTTP GET (blocks SSRF to private networks)."""
    if not url or not isinstance(url, str):
        raise ValueError("url required")
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http and https URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URL must not embed credentials")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL host is required")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise ValueError("URL host is not allowed")
    if host in ("169.254.169.254", "metadata.google.internal"):
        raise ValueError("URL host is not allowed")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addr_infos = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve host: {host}") from exc

    if not addr_infos:
        raise ValueError(f"Cannot resolve host: {host}")

    for info in addr_infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("URL resolves to a non-public address")

    return cleaned
