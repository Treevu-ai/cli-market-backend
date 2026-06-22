"""PyPI (Pepy) vs onboarding funnel — adoption comparison."""

from __future__ import annotations

from typing import Any

from market_funnel import funnel_summary
from market_pepy import pepy_summary


def _conv(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(num / den, 4)


def _pct(rate: float | None) -> str:
    if rate is None:
        return "—"
    return f"{rate * 100:.1f}%"


def _fmt(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def _pepy_as_multi() -> dict[str, Any]:
    """Wrap single-project Pepy stats in multi-project shape for adoption scoring."""
    data = pepy_summary()
    project = str(data.get("pepy_project") or data.get("project") or "cli-market-world")
    combined = {
        "ok": bool(data.get("ok")),
        "total_downloads": data.get("total_downloads"),
        "downloads_last_7d": data.get("downloads_last_7d"),
        "downloads_last_30d": data.get("downloads_last_30d"),
        "downloads_last_7d_raw": data.get("downloads_last_7d_raw"),
        "downloads_last_30d_raw": data.get("downloads_last_30d_raw"),
        "downloads_last_7d_no_ci": data.get("downloads_last_7d_no_ci"),
        "downloads_last_30d_no_ci": data.get("downloads_last_30d_no_ci"),
        "ci_share_pct_30d": data.get("ci_share_pct_30d"),
        "daily_series_14d": data.get("daily_series_14d") or [],
        "top_versions_30d": data.get("top_versions_30d") or [],
        "windows_source": data.get("windows_source"),
        "pro_time_range": data.get("pro_time_range"),
        "fetched_at": data.get("fetched_at"),
    }
    return {
        "ok": bool(data.get("ok")),
        "projects": [project],
        "combined": combined,
        "packages": {project: data},
        "fetched_at": data.get("fetched_at"),
    }


def adoption_summary(*, days: int = 30) -> dict[str, Any]:
    """Merge Pepy PyPI stats with funnel aggregates (no PII)."""
    days = max(1, min(days, 90))
    pypi_multi = _pepy_as_multi()
    combined = pypi_multi.get("combined", {}) or {}
    by_project = pypi_multi.get("packages", {}) or {}
    projects = pypi_multi.get("projects") or ["cli-market-world"]

    funnel = funnel_summary(days=days, include_test=False)

    install = int(funnel["events"].get("install", 0) or 0)
    register = int(
        funnel["funnel_steps"][1]["count"]
        if len(funnel.get("funnel_steps", [])) > 1
        else funnel["events"].get("register", 0) or 0
    )
    first_search = int(funnel["unique_users"].get("with_search", 0) or 0)
    starter = int(funnel["unique_users"].get("with_starter_subscribe", 0) or 0)
    pro_req = int(funnel["unique_users"].get("with_pro_request", 0) or 0)
    activated = int(funnel["unique_users"].get("activated", 0) or 0)

    pypi_30d = combined.get("downloads_last_30d") if combined.get("ok") else None
    pypi_7d = combined.get("downloads_last_7d") if combined.get("ok") else None
    pypi_total = combined.get("total_downloads") if combined.get("ok") else None

    register_per_pypi = _conv(register, pypi_30d) if isinstance(pypi_30d, int) else None
    register_per_install = _conv(register, install)
    search_per_register = _conv(first_search, register)
    install_per_pypi = _conv(install, pypi_30d) if isinstance(pypi_30d, int) else None

    notes: list[str] = []
    if combined.get("ok") and isinstance(pypi_30d, int) and pypi_30d > 0:
        if install == 0:
            notes.append(
                "Sin eventos install en el embudo; Pepy cuenta `pip install`, no telemetría CLI."
            )
        elif install < pypi_30d * 0.1:
            notes.append(
                f"install (embudo) << Pepy 30d ({install:,} vs {pypi_30d:,}) — "
                "la mayoría de descargas PyPI no reportan evento install."
            )
    if not combined.get("ok"):
        notes.append("PyPI (Pepy) sin datos.")
    if register > 0 and first_search == 0:
        notes.append("Hay registros sin first_search en la ventana.")

    funnel_conv = funnel.get("conversion", {})
    pricing_health = _conv(activated, first_search)

    pypi_flat: dict[str, Any] = {
        "ok": bool(combined.get("ok") or pypi_multi.get("ok")),
        "project": " + ".join(projects) if projects else "combined",
        "projects": projects,
        "total_downloads": pypi_total,
        "downloads_last_7d": pypi_7d,
        "downloads_last_30d": pypi_30d,
        "downloads_last_7d_raw": combined.get("downloads_last_7d_raw"),
        "downloads_last_30d_raw": combined.get("downloads_last_30d_raw"),
        "downloads_last_7d_no_ci": combined.get("downloads_last_7d_no_ci"),
        "downloads_last_30d_no_ci": combined.get("downloads_last_30d_no_ci"),
        "ci_share_pct_30d": combined.get("ci_share_pct_30d"),
        "daily_series_14d": combined.get("daily_series_14d") or [],
        "top_versions_30d": combined.get("top_versions_30d") or [],
        "top_version_30d": None,
        "latest_version": None,
        "windows_source": combined.get("windows_source"),
        "pro_time_range": combined.get("pro_time_range"),
        "combined": combined,
        "by_project": {
            name: {
                "ok": bool(pkg.get("ok")),
                "total_downloads": pkg.get("total_downloads"),
                "downloads_last_7d": pkg.get("downloads_last_7d"),
                "downloads_last_30d": pkg.get("downloads_last_30d"),
            }
            for name, pkg in by_project.items()
            if isinstance(pkg, dict)
        },
    }

    return {
        "window_days": days,
        "pypi": pypi_flat,
        "funnel": {
            "install": install,
            "register": register,
            "first_search": first_search,
            "starter_subscribe": starter,
            "request_pro": pro_req,
            "activated": activated,
        },
        "comparison": {
            "register_per_pypi_30d": register_per_pypi,
            "register_per_install": register_per_install,
            "search_per_register": search_per_register,
            "install_per_pypi_30d": install_per_pypi,
            "funnel_register_to_search": funnel_conv.get("register_to_search"),
            "funnel_search_to_starter": funnel_conv.get("search_to_starter"),
            "funnel_search_to_pro": funnel_conv.get("search_to_pro"),
            "funnel_pro_to_activated": funnel_conv.get("pro_to_activated"),
            "pricing_health": pricing_health,
        },
        "notes": notes,
        "fetched_at": combined.get("fetched_at") or pypi_multi.get("fetched_at"),
    }


def adoption_slack_lines(*, days: int = 30) -> list[str]:
    data = adoption_summary(days=days)
    p = data["pypi"]
    f = data["funnel"]
    c = data["comparison"]
    w = data["window_days"]

    lines = [f"*Adopción ({w}d)* · PyPI vs embudo P3", ""]

    if p.get("ok"):
        lines.append(
            f"• PyPI: *{_fmt(p.get('downloads_last_30d'))}* dl 30d · "
            f"*{_fmt(p.get('downloads_last_7d'))}* 7d · total *{_fmt(p.get('total_downloads'))}*"
        )
    else:
        lines.append("• PyPI: _sin datos Pepy_")

    lines.append(
        f"• Embudo: install *{f['install']}* → register *{f['register']}* → "
        f"search *{f['first_search']}* → starter *{f['starter_subscribe']}*"
    )
    lines.append(
        f"• Conv: register/PyPI30d *{_pct(c.get('register_per_pypi_30d'))}* · "
        f"register/install *{_pct(c.get('register_per_install'))}* · "
        f"search/register *{_pct(c.get('search_per_register'))}*"
    )

    for note in data.get("notes") or []:
        lines.append(f"_{note}_")

    lines.append("")
    return lines
