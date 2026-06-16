"""Shared retry helper for outbound HTTP calls to third-party/internal APIs.

Retries on timeouts, connection errors, and retryable 5xx/429 status codes
with exponential backoff. Single source of truth so each caller doesn't
reimplement its own retry loop.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("market.server").getChild("http_retry")

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def request_with_retry(
    method: str,
    url: str,
    *,
    retries: int = 2,
    backoff: float = 0.5,
    **kwargs,
) -> httpx.Response:
    """Like httpx.request(), but retries transient failures with backoff."""
    last_exc: Exception | None = None
    resp: httpx.Response | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt == retries:
                raise
        else:
            if resp.status_code not in RETRYABLE_STATUS_CODES or attempt == retries:
                return resp
            last_exc = None
        logger.debug("retrying %s %s (attempt %d/%d)", method, url, attempt + 1, retries)
        time.sleep(backoff * (2**attempt))
    if last_exc:
        raise last_exc
    return resp
