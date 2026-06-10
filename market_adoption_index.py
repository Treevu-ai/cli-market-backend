"""CLI Market Adoption Index — composite score from real product signals."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from market_adoption import adoption_summary
from market_core import USE_PG, get_db
from market_funnel import ensure_funnel_schema, funnel_summary, is_test_funnel_traffic

INDEX_SCOPE = "cli-market-world"
DEFAULT_GITHUB_REPO = "Treevu-ai/cli-market-world"

_WEIGHTS_V1 = {
    "downloads": 0.30,
    "real_usage": 0.25,
    "growth": 0.20,
    "activation": 0.15,
    "revenue_intent": 0.10,
}

_SNAPSHOT_DDL_PG = """
CREATE TABLE IF NOT EXISTS adoption_index_snapshots (
    id SERIAL PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT 'cli-market-world',
    score REAL NOT NULL,
    signals TEXT NOT NULL,
    breakdown TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_SNAPSHOT_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS adoption_index_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'cli-market-world',
    score REAL NOT NULL,
    signals TEXT NOT NULL,
    breakdown TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _target(name: str, default: float) -> float:
    raw = os.getenv(f"ADOPTION_TARGET_{name}", "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def ensure_adoption_index_schema() -> None:
    ensure_funnel_schema()
    db = get_db()
    db.execute(_SNAPSHOT_DDL_PG if USE_PG else _SNAPSHOT_DDL_SQLITE)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_adoption_index_scope_created "
        "ON adoption_index_snapshots(scope, created_at)"
    )
    db.commit()
    db.close()


def _norm_log(value: float, *, target: float) -> float:
    if value <= 0 or target <= 0:
        return 0.0
    ratio = math.log1p(value) / math.log1p(target * 2)
    return round(min(100.0, max(0.0, ratio * 100)), 1)


def _norm_rate(rate: float | None, *, target: float) -> float:
    if rate is None:
        return 0.0
    return round(min(100.0, max(0.0, (rate / target) * 100)), 1)


def _observatory_maa(days: int) -> tuple[int | None, str | None]:
    try:
        from market_observatory import count_maa, observatory_enabled

        if observatory_enabled():
            return count_maa(days=days, exclude_noise=True), "maa"
    except Exception:
        pass
    return None, None


def _growth_score(downloads_7d: int | None, downloads_30d: int | None) -> tuple[float, float | None]:
    if not downloads_7d or not downloads_30d or downloads_30d <= downloads_7d:
        pct = 100.0 if (downloads_7d or 0) > 0 and (downloads_30d or 0) <= (downloads_7d or 0) else 0.0
        return round(min(100.0, 50.0 + pct * 0.5), 1), pct if downloads_30d else None
    prior_23d = downloads_30d - downloads_7d
    if prior_23d <= 0:
        return 75.0, None
    weekly_baseline = prior_23d / 23.0 * 7.0
    if weekly_baseline <= 0:
        return 75.0, None
    pct = round((downloads_7d - weekly_baseline) / weekly_baseline * 100, 1)
    score = min(100.0, max(0.0, 50.0 + pct))
    return round(score, 1), pct


def funnel_retention_summary(*, days: int = 30, return_within_days: int = 7) -> dict[str, Any]:
    ensure_funnel_schema()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    db = get_db()
    rows = db.execute(
        "SELECT event, username, created_at FROM funnel_events WHERE created_at >= ?",
        (since.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()
    db.close()

    by_user: dict[str, list[tuple[str, datetime]]] = {}
    for row in rows:
        user = (row["username"] or "").strip()
        if not user or is_test_funnel_traffic(user):
            continue
        ts = _parse_ts(row["created_at"])
        if not ts:
            continue
        by_user.setdefault(user, []).append((row["event"], ts))

    follow_up_events = frozenset(
        {"login", "starter_subscribe", "request_pro", "activated", "onboarding_complete"}
    )
    cohort = 0
    retained = 0
    for events in by_user.values():
        first_search_at = None
        for ev, ts in events:
            if ev == "first_search":
                first_search_at = ts if first_search_at is None else min(first_search_at, ts)
        if first_search_at is None:
            continue
        cohort += 1
        window_end = first_search_at + timedelta(days=return_within_days)
        for ev, ts in events:
            if ev in follow_up_events and first_search_at < ts <= window_end:
                retained += 1
                break

    rate = round(retained / cohort, 4) if cohort else None
    return {
        "window_days": days,
        "return_within_days": return_within_days,
        "cohort_first_search": cohort,
        "retained": retained,
        "retention_rate": rate,
    }


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text.replace("+00:00", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_github_signals(
    repo: str | None = None,
    *,
    releases_days: int = 90,
) -> dict[str, Any]:
    slug = (repo or os.getenv("ADOPTION_GITHUB_REPO") or DEFAULT_GITHUB_REPO).strip()
    if "/" not in slug:
        return {"ok": False, "repo": slug, "message": "invalid repo slug"}
    owner, name = slug.split("/", 1)
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CLI-Market-Adoption-Index/1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def _get(path: str) -> Any:
        url = f"https://api.github.com{path}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return None

    meta = _get(f"/repos/{owner}/{name}")
    if not isinstance(meta, dict):
        return {"ok": False, "repo": slug, "message": "github fetch failed"}

    contributors = _get(f"/repos/{owner}/{name}/contributors?per_page=100&anon=1")
    contributor_count = len(contributors) if isinstance(contributors, list) else None

    releases = _get(f"/repos/{owner}/{name}/releases?per_page=30")
    release_count_90d = 0
    if isinstance(releases, list):
        cutoff = datetime.now(timezone.utc) - timedelta(days=releases_days)
        for rel in releases:
            published = _parse_ts((rel or {}).get("published_at"))
            if published and published >= cutoff:
                release_count_90d += 1

    pushed_at = _parse_ts(meta.get("pushed_at"))
    days_since_push = None
    if pushed_at:
        days_since_push = round((datetime.now(timezone.utc) - pushed_at).total_seconds() / 86400, 1)

    return {
        "ok": True,
        "repo": slug,
        "stars": int(meta.get("stargazers_count") or 0),
        "forks": int(meta.get("forks_count") or 0),
        "open_issues": int(meta.get("open_issues_count") or 0),
        "contributors": contributor_count,
        "releases_90d": release_count_90d,
        "last_push_at": pushed_at.isoformat() if pushed_at else None,
        "days_since_push": days_since_push,
        "default_branch": meta.get("default_branch"),
    }


def compute_adoption_index(
    *,
    days: int = 30,
    include_github: bool = False,
) -> dict[str, Any]:
    days = max(1, min(days, 90))
    adoption = adoption_summary(days=days)
    funnel = funnel_summary(days=days, include_test=False)
    retention = funnel_retention_summary(days=days)

    pypi = adoption["pypi"]
    f = adoption["funnel"]
    conv = adoption["comparison"]
    funnel_conv = funnel.get("conversion", {})

    downloads_30d = pypi.get("downloads_last_30d") if pypi.get("ok") else None
    downloads_7d = pypi.get("downloads_last_7d") if pypi.get("ok") else None
    first_search = int(funnel["unique_users"].get("with_search", 0) or 0)
    register = int(f.get("register", 0) or 0)
    pro_req = int(funnel["unique_users"].get("with_pro_request", 0) or 0)
    activated = int(funnel["unique_users"].get("activated", 0) or 0)

    maa, maa_source = _observatory_maa(days)
    if maa_source == "maa":
        usage_count = int(maa or 0)
        usage_label = "MAA (agent_events)"
        usage_note = "Observatory telemetry active"
        usage_target = _target("MAA", 30.0)
    else:
        usage_count = first_search
        usage_label = "first_search users (noise-filtered)"
        usage_note = "MCP telemetry inactive — funnel proxy"
        usage_target = _target("FIRST_SEARCH", 30.0)

    dl_score = _norm_log(
        float(downloads_30d or 0),
        target=_target("DOWNLOADS_30D", 5000.0),
    )
    usage_score = _norm_log(float(usage_count), target=usage_target)
    growth_score, growth_pct = _growth_score(
        int(downloads_7d) if isinstance(downloads_7d, int) else None,
        int(downloads_30d) if isinstance(downloads_30d, int) else None,
    )

    register_to_search = funnel_conv.get("register_to_search") or conv.get("funnel_register_to_search")
    retention_rate = retention.get("retention_rate")
    activation_score = round(
        (
            _norm_rate(register_to_search, target=_target("REGISTER_TO_SEARCH", 0.5)) * 0.6
            + _norm_rate(retention_rate, target=_target("RETENTION_7D", 0.35)) * 0.4
        ),
        1,
    )

    revenue_intent_score = round(
        (
            _norm_log(float(pro_req), target=_target("PRO_REQUESTS", 5.0)) * 0.55
            + _norm_log(float(activated), target=_target("ACTIVATED", 3.0)) * 0.45
        ),
        1,
    )

    breakdown = {
        "downloads": {"weight": _WEIGHTS_V1["downloads"], "score": dl_score},
        "real_usage": {"weight": _WEIGHTS_V1["real_usage"], "score": usage_score},
        "growth": {"weight": _WEIGHTS_V1["growth"], "score": growth_score},
        "activation": {"weight": _WEIGHTS_V1["activation"], "score": activation_score},
        "revenue_intent": {"weight": _WEIGHTS_V1["revenue_intent"], "score": revenue_intent_score},
    }
    score = round(sum(b["weight"] * b["score"] for b in breakdown.values()), 1)

    by_proj = pypi.get("by_project") or {}
    core_30d = (by_proj.get("cli-market-core") or {}).get("downloads_last_30d") if isinstance(by_proj.get("cli-market-core"), dict) else None
    world_30d = (by_proj.get("cli-market-world") or {}).get("downloads_last_30d") if isinstance(by_proj.get("cli-market-world"), dict) else None

    signals: dict[str, Any] = {
        "window_days": days,
        "pypi": {
            "ok": bool(pypi.get("ok")),
            "projects": pypi.get("projects"),
            "combined": pypi.get("combined") or {
                "downloads_last_30d": downloads_30d,
                "downloads_last_7d": downloads_7d,
                "total_downloads": pypi.get("total_downloads"),
            },
            "by_project": by_proj,
            "downloads_core_30d": core_30d,
            "downloads_world_30d": world_30d,
            "downloads_combined_30d": downloads_30d,
            "downloads_30d": downloads_30d,
            "downloads_7d": downloads_7d,
            "downloads_30d_raw": pypi.get("downloads_last_30d_raw"),
            "downloads_7d_raw": pypi.get("downloads_last_7d_raw"),
            "downloads_30d_no_ci": pypi.get("downloads_last_30d_no_ci"),
            "downloads_7d_no_ci": pypi.get("downloads_last_7d_no_ci"),
            "ci_share_pct_30d": pypi.get("ci_share_pct_30d"),
            "daily_series_14d": pypi.get("daily_series_14d") or [],
            "top_versions_30d": pypi.get("top_versions_30d") or [],
            "windows_source": pypi.get("windows_source"),
            "pro_time_range": pypi.get("pro_time_range"),
            "total_downloads": pypi.get("total_downloads"),
            "growth_pct_7d_vs_baseline": growth_pct,
        },
        "funnel": {
            "install": f.get("install"),
            "register": register,
            "first_search": first_search,
            "request_pro": pro_req,
            "activated": activated,
            "starter_subscribe": f.get("starter_subscribe"),
            "register_to_search": register_to_search,
            "search_to_pro": funnel_conv.get("search_to_pro"),
            "pro_to_activated": funnel_conv.get("pro_to_activated"),
            "ttfv_median_minutes": funnel.get("ttfv_median_minutes"),
        },
        "retention_7d": retention,
        "maa": maa if maa_source == "maa" else None,
        "agent_usage_proxy": {
            "label": usage_label,
            "value": usage_count,
            "note": usage_note,
            "source": maa_source or "funnel",
        },
    }

    if include_github:
        signals["github"] = fetch_github_signals()

    return {
        "scope": INDEX_SCOPE,
        "version": "v1",
        "score": score,
        "grade": score_grade(score),
        "weights": _WEIGHTS_V1,
        "breakdown": breakdown,
        "signals": signals,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def score_grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def persist_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_adoption_index_schema()
    db = get_db()
    signals_json = json.dumps(payload.get("signals") or {})
    breakdown_json = json.dumps(payload.get("breakdown") or {})
    db.execute(
        "INSERT INTO adoption_index_snapshots (scope, score, signals, breakdown) VALUES (?, ?, ?, ?)",
        (payload.get("scope", INDEX_SCOPE), payload["score"], signals_json, breakdown_json),
    )
    row = db.execute(
        "SELECT id, created_at FROM adoption_index_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.commit()
    db.close()
    return {
        "id": row["id"] if row else None,
        "created_at": str(row["created_at"]) if row else None,
    }


def list_snapshots(*, scope: str = INDEX_SCOPE, limit: int = 30) -> list[dict[str, Any]]:
    ensure_adoption_index_schema()
    limit = max(1, min(limit, 180))
    db = get_db()
    rows = db.execute(
        "SELECT id, scope, score, signals, breakdown, created_at "
        "FROM adoption_index_snapshots WHERE scope=? ORDER BY created_at DESC LIMIT ?",
        (scope, limit),
    ).fetchall()
    db.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        signals = row["signals"]
        breakdown = row["breakdown"]
        if isinstance(signals, str):
            signals = json.loads(signals)
        if isinstance(breakdown, str):
            breakdown = json.loads(breakdown)
        out.append(
            {
                "id": row["id"],
                "scope": row["scope"],
                "score": row["score"],
                "signals": signals,
                "breakdown": breakdown,
                "created_at": str(row["created_at"]),
            }
        )
    return out


def latest_snapshot(*, scope: str = INDEX_SCOPE) -> dict[str, Any] | None:
    rows = list_snapshots(scope=scope, limit=1)
    return rows[0] if rows else None
