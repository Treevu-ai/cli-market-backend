"""Pepy.tech PyPI download stats — server-side only (PEPY_API_KEY)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

PEPY_BASE = "https://api.pepy.tech"
_CACHE: dict[str, Any] = {}
_CACHE_AT: float = 0.0
_CACHE_TTL_S = int(os.getenv("PEPY_CACHE_TTL_S", "3600"))


def _api_key() -> str:
    return (os.getenv("PEPY_API_KEY") or os.getenv("PEPYTECH_API_KEY") or "").strip()


def project_name() -> str:
    return (os.getenv("PEPY_PROJECT") or "cli-market").strip().lower()


def pypi_display_project() -> str:
    """PyPI package name shown in API responses (may differ from Pepy project key)."""
    return (os.getenv("PYPI_PACKAGE_NAME") or "cli-market-world").strip().lower()


def _fetch_json(path: str) -> dict[str, Any] | list[Any] | None:
    key = _api_key()
    if not key:
        return None
    url = f"{PEPY_BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "X-API-Key": key,
            "Accept": "application/json",
            "User-Agent": "CLI-Market-Pepy/1 (+https://cli-market.dev)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _sum_downloads(
    downloads: dict[str, dict[str, int]],
    *,
    since: date | None = None,
) -> tuple[int, dict[str, int]]:
    total = 0
    by_version: dict[str, int] = {}
    for day_str, versions in downloads.items():
        if not isinstance(versions, dict):
            continue
        day = _parse_day(day_str)
        if since and day and day < since:
            continue
        for ver, count in versions.items():
            n = int(count or 0)
            total += n
            by_version[ver] = by_version.get(ver, 0) + n
    return total, by_version


def _top_version(by_version: dict[str, int]) -> str | None:
    if not by_version:
        return None
    return max(by_version.items(), key=lambda x: x[1])[0]


def pepy_summary(*, force: bool = False) -> dict[str, Any]:
    """Aggregate Pepy stats for the configured PyPI project."""
    global _CACHE_AT
    now = time.time()
    if not force and _CACHE and (now - _CACHE_AT) < _CACHE_TTL_S:
        return dict(_CACHE)

    project = project_name()
    display = pypi_display_project()
    out: dict[str, Any] = {
        "project": display,
        "pepy_project": project,
        "configured": bool(_api_key()),
        "source": "pepy.tech",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if not out["configured"]:
        out["ok"] = False
        out["message"] = "PEPY_API_KEY not set"
        return out

    base = _fetch_json(f"/api/v2/projects/{project}?includeMetadata=true")
    if not isinstance(base, dict) and project != "cli-market":
        project = "cli-market"
        out["pepy_project"] = project
        base = _fetch_json(f"/api/v2/projects/{project}?includeMetadata=true")
    if not isinstance(base, dict):
        out["ok"] = False
        out["message"] = "pepy fetch failed"
        return out

    downloads = base.get("downloads") if isinstance(base.get("downloads"), dict) else {}
    today = date.today()
    last_7, _ = _sum_downloads(downloads, since=today - timedelta(days=6))
    last_30, by_ver_30 = _sum_downloads(downloads, since=today - timedelta(days=29))
    last_1, _ = _sum_downloads(downloads, since=today)

    pro = _fetch_json(
        f"/service-api/v1/pro/projects/{project}/downloads"
        "?timeRange=THREE_MONTHS&includeCIDownloads=false"
    )
    downloads_no_ci: dict[str, dict[str, int]] = {}
    if isinstance(pro, dict) and isinstance(pro.get("downloads"), dict):
        downloads_no_ci = pro["downloads"]
    last_30_no_ci, _ = _sum_downloads(downloads_no_ci, since=today - timedelta(days=29))

    meta = base.get("metadata") if isinstance(base.get("metadata"), dict) else {}
    out.update(
        {
            "ok": True,
            "total_downloads": int(base.get("total_downloads") or 0),
            "downloads_last_24h": last_1,
            "downloads_last_7d": last_7,
            "downloads_last_30d": last_30,
            "downloads_last_30d_no_ci": last_30_no_ci or None,
            "top_version_30d": _top_version(by_ver_30),
            "latest_version": meta.get("latest_version"),
            "versions_tracked": len(base.get("versions") or []),
        }
    )

    _CACHE.clear()
    _CACHE.update(out)
    _CACHE_AT = now
    return dict(out)