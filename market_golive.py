"""Go-live dashboard — 3 north-star KPIs + founder alerts."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from market_adoption import adoption_summary
from market_funnel import activation_summary, funnel_summary

# Internal targets (founder ops)
SEARCH_PER_REGISTER_TARGET = 0.40
COVERAGE_7D_TARGET = 80.0
WEEKLY_ACTIVATED_GOAL = 1

# Spike D+7 (2026-06-10 → 2026-06-17) + pricing decision gates
SPIKE_PYPI_7D_TARGET = 2_000
SPIKE_PRO_ACTIVATED_TARGET = 2
SEARCH_TO_PRO_TARGET = 0.03
PRO_TO_ACTIVATED_TARGET = 0.50
PRICING_HEALTH_OK = 0.02
PRICING_HEALTH_TRIAL = 0.005
TTFV_MEDIAN_MINUTES_TARGET = 30.0
TTC_MEDIAN_HOURS_TARGET = 24.0
PRICING_MIN_REGISTER_SAMPLE = 20
WEBHOOK_ACTIVATION_SHARE_TARGET = 0.80


def _pct(rate: float | None) -> str:
    if rate is None:
        return "—"
    return f"{rate * 100:.1f}%"


def _kpi_status(*, ok: bool, warn: bool = False) -> str:
    if ok:
        return "ok"
    if warn:
        return "warn"
    return "critical"


def _alert(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(num / den, 4)


def _pricing_recommendation(
    *,
    register: int,
    first_search: int,
    pro_req: int,
    activated: int,
    search_to_pro: float | None,
    pro_to_activated: float | None,
    pricing_health: float | None,
) -> dict[str, Any]:
    """Post-spike pricing decision from funnel shape (see ops/go_live_check.py --spike)."""
    if register < PRICING_MIN_REGISTER_SAMPLE:
        return {
            "action": "wait",
            "label": "Muestra insuficiente",
            "detail": f"register {register} < {PRICING_MIN_REGISTER_SAMPLE} — no cambiar pricing aún.",
        }

    search_per_register = _rate(first_search, register)
    if search_per_register is not None and search_per_register < 0.25:
        return {
            "action": "fix_onboarding",
            "label": "Arreglar onboarding",
            "detail": "search/register <25% — adopción rota; no tocar precio.",
        }

    if pro_to_activated is not None and pro_req >= 2 and pro_to_activated < PRO_TO_ACTIVATED_TARGET:
        return {
            "action": "fix_checkout",
            "label": "Arreglar checkout",
            "detail": (
                f"activated/request_pro {_pct(pro_to_activated)} "
                f"(meta ≥{_pct(PRO_TO_ACTIVATED_TARGET)}) — ops PayPal, no bajar precio."
            ),
        }

    if pricing_health is not None and pricing_health >= PRICING_HEALTH_OK:
        return {
            "action": "keep_39",
            "label": "Mantener Pro $39",
            "detail": (
                f"pricing_health {_pct(pricing_health)} ≥ {_pct(PRICING_HEALTH_OK)}."
            ),
        }

    if (
        search_to_pro is not None
        and PRICING_HEALTH_TRIAL <= (pricing_health or 0) < PRICING_HEALTH_OK
    ) or (
        search_to_pro is not None
        and SEARCH_TO_PRO_TARGET * 0.33 <= search_to_pro < SEARCH_TO_PRO_TARGET
    ):
        return {
            "action": "trial_pro",
            "label": "Trial Pro 14d",
            "detail": (
                f"intent {_pct(search_to_pro)} o health {_pct(pricing_health)} — "
                "probar trial antes que Starter $19."
            ),
        }

    if (
        search_per_register is not None
        and search_per_register >= SEARCH_PER_REGISTER_TARGET
        and (search_to_pro or 0) < SEARCH_TO_PRO_TARGET
        and (pricing_health or 0) < PRICING_HEALTH_TRIAL
    ):
        return {
            "action": "evaluate_starter",
            "label": "Evaluar Starter $19 (CLI only)",
            "detail": (
                "search alto, intent bajo — Starter oculto post D+14, no en landing."
            ),
        }

    return {
        "action": "keep_39",
        "label": "Mantener Pro $39",
        "detail": "Sin señal clara para cambio — seguir midiendo.",
    }


def _load_dashboard_data(dashboard_data: dict[str, Any] | None) -> dict[str, Any]:
    if dashboard_data is not None:
        return dashboard_data
    try:
        # Prefer the public accessor (added for cleaner cross-module use by
        # founder go-live alerts and to avoid importing private symbols).
        from routers.dashboard import get_cached_dashboard_data

        return get_cached_dashboard_data()
    except (ImportError, AttributeError):
        # Fallback for older deployments or during refactors
        try:
            from routers.dashboard import _cached_dashboard_data

            return _cached_dashboard_data()
        except Exception:
            return {}
    except Exception:
        # Never let dashboard data loading take down the go-live view
        return {}


def go_live_summary(*, days: int = 30, dashboard_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge adoption funnel + data moat into 3 KPIs and actionable alerts."""
    days = max(1, min(days, 90))
    adoption = adoption_summary(days=days)
    funnel = funnel_summary(days=days)
    activation_paths = activation_summary(days=days)
    dash = _load_dashboard_data(dashboard_data)

    f = adoption["funnel"]
    c = adoption["comparison"]
    register = int(f.get("register", 0) or 0)
    first_search = int(f.get("first_search", 0) or 0)
    starter = int(f.get("starter_subscribe", 0) or 0)
    pro_req = int(f.get("request_pro", 0) or 0)
    activated = int(f.get("activated", 0) or 0)

    search_per_register = c.get("search_per_register")
    ttfv = funnel.get("ttfv_median_minutes")
    ttc = funnel.get("ttc_median_hours")
    search_to_pro = funnel.get("conversion", {}).get("search_to_pro")
    pro_to_activated = funnel.get("conversion", {}).get("pro_to_activated")
    pricing_health = _rate(activated, first_search)
    intent_rate = search_to_pro
    pypi_7d = adoption["pypi"].get("downloads_last_7d")
    pricing_decision = _pricing_recommendation(
        register=register,
        first_search=first_search,
        pro_req=pro_req,
        activated=activated,
        search_to_pro=search_to_pro,
        pro_to_activated=pro_to_activated,
        pricing_health=pricing_health,
    )

    activation_ok = (
        register == 0
        or (
            search_per_register is not None
            and search_per_register >= SEARCH_PER_REGISTER_TARGET
        )
    )
    activation_warn = (
        register > 0
        and search_per_register is not None
        and 0.25 <= search_per_register < SEARCH_PER_REGISTER_TARGET
    )

    kpis = dash.get("kpis", {})
    moat = dash.get("moat_summary", {})
    collector = dash.get("collector", {})
    store_health = dash.get("store_health", [])

    coverage_7d = float(kpis.get("coverage_7d_pct") or moat.get("coverage_7d_pct") or 0)
    collector_status = str(collector.get("status") or "unknown")
    collector_stale = bool(moat.get("collector_stale"))
    critical_stores = [
        h for h in store_health if float(h.get("success_pct", 0) or 0) < 30
    ]

    revenue_has_signal = starter > 0 or pro_req > 0 or activated > 0
    revenue_ok = activated > 0 or (starter + pro_req) > 0
    revenue_warn = not revenue_has_signal and register >= 3

    moat_ok = (
        coverage_7d >= COVERAGE_7D_TARGET
        and not critical_stores
        and collector_status not in ("stale", "failed", "unknown")
        and not collector_stale
    )
    moat_warn = (
        not moat_ok
        and coverage_7d >= 60
        and len(critical_stores) == 0
        and not collector_stale
    )

    alerts: list[dict[str, str]] = []

    if register > 0 and first_search == 0:
        alerts.append(
            _alert(
                "critical",
                "activation_no_search",
                f"Hay {register} registros sin first_search en {days}d — revisar onboarding P5.",
            )
        )
    elif search_per_register is not None and search_per_register < SEARCH_PER_REGISTER_TARGET:
        sev = "critical" if search_per_register < 0.25 else "warn"
        alerts.append(
            _alert(
                sev,
                "activation_low",
                f"Activación baja: search/register {_pct(search_per_register)} "
                f"(meta ≥{_pct(SEARCH_PER_REGISTER_TARGET)}).",
            )
        )

    if ttfv is not None and ttfv > 60:
        alerts.append(
            _alert(
                "warn",
                "ttfv_slow",
                f"TTFV mediana {ttfv:.0f} min (>60 min) — usuarios tardan en primera búsqueda.",
            )
        )
    elif ttfv is not None and ttfv > TTFV_MEDIAN_MINUTES_TARGET:
        alerts.append(
            _alert(
                "info",
                "ttfv_above_target",
                f"TTFV mediana {ttfv:.0f} min (meta <{TTFV_MEDIAN_MINUTES_TARGET:.0f} min).",
            )
        )

    if ttc is not None and ttc > TTC_MEDIAN_HOURS_TARGET:
        alerts.append(
            _alert(
                "warn",
                "ttc_slow",
                f"TTC mediana {ttc:.0f} h (>{TTC_MEDIAN_HOURS_TARGET:.0f} h) — checkout o activación lenta.",
            )
        )

    if isinstance(pypi_7d, int) and days <= 7 and pypi_7d < SPIKE_PYPI_7D_TARGET:
        alerts.append(
            _alert(
                "warn",
                "spike_pypi_gate",
                f"PyPI 7d {pypi_7d:,} < meta spike {SPIKE_PYPI_7D_TARGET:,}.",
            )
        )

    if days <= 7 and activated < SPIKE_PRO_ACTIVATED_TARGET and register >= 10:
        alerts.append(
            _alert(
                "warn",
                "spike_pro_gate",
                f"Pro activados {activated} < meta spike {SPIKE_PRO_ACTIVATED_TARGET} "
                f"(ventana {days}d).",
            )
        )

    if (
        first_search >= 5
        and search_to_pro is not None
        and search_to_pro < SEARCH_TO_PRO_TARGET
        and register >= PRICING_MIN_REGISTER_SAMPLE
    ):
        alerts.append(
            _alert(
                "info",
                "pricing_intent_low",
                f"Intent Pro (search→request_pro) {_pct(search_to_pro)} "
                f"(meta ≥{_pct(SEARCH_TO_PRO_TARGET)}).",
            )
        )

    if pro_req >= 2 and pro_to_activated is not None and pro_to_activated < PRO_TO_ACTIVATED_TARGET:
        alerts.append(
            _alert(
                "warn",
                "pricing_activation_leak",
                f"Fuga activación: activated/request_pro {_pct(pro_to_activated)} "
                f"(meta ≥{_pct(PRO_TO_ACTIVATED_TARGET)}).",
            )
        )

    act_req = activation_paths.get("subscription_requests", {})
    act_ev = activation_paths.get("activated_events", {})
    pending_manual = int(act_req.get("pending_manual", 0) or 0)
    pending_auto = int(act_req.get("pending_auto", 0) or 0)
    webhook_share = activation_paths.get("webhook_share")

    if pending_manual > 0:
        alerts.append(
            _alert(
                "warn",
                "activation_manual_queue",
                f"{pending_manual} Pro request(s) en cola manual (hosted button) — "
                f"migrar a suscripción webhook.",
            )
        )

    if act_ev.get("total", 0) >= 2 and webhook_share is not None:
        if webhook_share < WEBHOOK_ACTIVATION_SHARE_TARGET:
            alerts.append(
                _alert(
                    "warn",
                    "activation_webhook_low",
                    f"Webhook share {_pct(webhook_share)} "
                    f"(meta ≥{_pct(WEBHOOK_ACTIVATION_SHARE_TARGET)}) — "
                    f"manual={act_ev.get('manual', 0)}.",
                )
            )

    if pending_auto > 0 and activated == 0 and pro_req > 0:
        alerts.append(
            _alert(
                "info",
                "activation_pending_webhook",
                f"{pending_auto} suscripción(es) Pro pendientes de confirmar en PayPal.",
            )
        )

    if register >= 5 and not revenue_has_signal:
        alerts.append(
            _alert(
                "warn",
                "revenue_zero",
                f"{register} registros en {days}d sin starter/request_pro/activated — revisar P6 billing.",
            )
        )
    if activated == 0 and (starter > 0 or pro_req > 0):
        alerts.append(
            _alert(
                "info",
                "revenue_pending_activation",
                f"Hay {starter} starter + {pro_req} pro request sin activated — seguimiento manual.",
            )
        )

    if coverage_7d < COVERAGE_7D_TARGET:
        sev = "critical" if coverage_7d < 60 else "warn"
        alerts.append(
            _alert(
                sev,
                "moat_coverage_low",
                f"Cobertura 7d {coverage_7d:.1f}% (meta ≥{COVERAGE_7D_TARGET:.0f}%).",
            )
        )

    if critical_stores:
        names = ", ".join(h.get("store", "?") for h in critical_stores[:5])
        alerts.append(
            _alert(
                "critical",
                "moat_stores_dead",
                f"{len(critical_stores)} tienda(s) críticas (<30% éxito): {names}.",
            )
        )

    if collector_stale or collector_status in ("stale", "failed"):
        alerts.append(
            _alert(
                "critical",
                "collector_stale",
                f"Collector {collector_status} — moat envejeciendo; trigger o revisar daemon.",
            )
        )
    elif collector_status == "unknown":
        alerts.append(
            _alert(
                "warn",
                "collector_unknown",
                "Estado del collector desconocido — verificar /health/collector.",
            )
        )

    if not adoption["pypi"].get("ok"):
        alerts.append(
            _alert(
                "info",
                "pypi_unavailable",
                adoption["pypi"].get("message") or "PyPI (Pepy) sin datos — revisar PEPY_API_KEY.",
            )
        )

    severity_order = {"critical": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 9))

    alert_count = {
        "critical": sum(1 for a in alerts if a["severity"] == "critical"),
        "warn": sum(1 for a in alerts if a["severity"] == "warn"),
        "info": sum(1 for a in alerts if a["severity"] == "info"),
    }

    if alert_count["critical"] > 0:
        overall = "critical"
    elif alert_count["warn"] > 0:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "targets": {
            "search_per_register_min": SEARCH_PER_REGISTER_TARGET,
            "coverage_7d_min": COVERAGE_7D_TARGET,
            "weekly_activated_goal": WEEKLY_ACTIVATED_GOAL,
            "spike_pypi_7d": SPIKE_PYPI_7D_TARGET,
            "spike_pro_activated": SPIKE_PRO_ACTIVATED_TARGET,
            "search_to_pro_min": SEARCH_TO_PRO_TARGET,
            "pro_to_activated_min": PRO_TO_ACTIVATED_TARGET,
            "pricing_health_ok": PRICING_HEALTH_OK,
            "pricing_health_trial": PRICING_HEALTH_TRIAL,
            "ttfv_median_minutes_max": TTFV_MEDIAN_MINUTES_TARGET,
            "ttc_median_hours_max": TTC_MEDIAN_HOURS_TARGET,
            "webhook_activation_share_min": WEBHOOK_ACTIVATION_SHARE_TARGET,
        },
        "kpis": {
            "activation": {
                "title": "Activación",
                "register": register,
                "first_search": first_search,
                "search_per_register": search_per_register,
                "search_per_register_target": SEARCH_PER_REGISTER_TARGET,
                "ttfv_median_minutes": ttfv,
                "status": _kpi_status(ok=activation_ok, warn=activation_warn),
            },
            "revenue": {
                "title": "Revenue",
                "starter_subscribe": starter,
                "request_pro": pro_req,
                "activated": activated,
                "weekly_paid_goal": WEEKLY_ACTIVATED_GOAL,
                "register_to_paid": round((starter + pro_req + activated) / register, 4)
                if register > 0
                else None,
                "status": _kpi_status(ok=revenue_ok, warn=revenue_warn),
            },
            "pro_activation": {
                "title": "Activación Pro",
                "pending_auto": pending_auto,
                "pending_manual": pending_manual,
                "requests_activated": act_req.get("activated", 0),
                "webhook_activated": act_ev.get("webhook", 0),
                "manual_activated": act_ev.get("manual", 0),
                "webhook_share": webhook_share,
                "webhook_share_target": WEBHOOK_ACTIVATION_SHARE_TARGET,
                "unified_webhook": activation_paths.get("unified_webhook"),
                "status": _kpi_status(
                    ok=bool(activation_paths.get("unified_webhook")),
                    warn=pending_manual > 0 or (
                        webhook_share is not None
                        and webhook_share < WEBHOOK_ACTIVATION_SHARE_TARGET
                        and act_ev.get("total", 0) >= 1
                    ),
                ),
            },
            "pricing": {
                "title": "Pricing / spike",
                "pypi_downloads_7d": pypi_7d,
                "spike_pypi_7d_target": SPIKE_PYPI_7D_TARGET,
                "search_to_pro": search_to_pro,
                "search_to_pro_target": SEARCH_TO_PRO_TARGET,
                "pro_to_activated": pro_to_activated,
                "pro_to_activated_target": PRO_TO_ACTIVATED_TARGET,
                "pricing_health": pricing_health,
                "pricing_health_ok": PRICING_HEALTH_OK,
                "intent_rate": intent_rate,
                "ttc_median_hours": ttc,
                "decision": pricing_decision,
                "status": _kpi_status(
                    ok=activated >= SPIKE_PRO_ACTIVATED_TARGET
                    or (pricing_health or 0) >= PRICING_HEALTH_OK,
                    warn=pro_req > 0 and activated == 0,
                ),
            },
            "data_moat": {
                "title": "Data moat",
                "coverage_7d_pct": coverage_7d,
                "coverage_target": COVERAGE_7D_TARGET,
                "collector_status": collector_status,
                "collector_stale": collector_stale,
                "critical_stores": [
                    {"store": h.get("store"), "success_pct": h.get("success_pct")}
                    for h in critical_stores[:10]
                ],
                "critical_store_count": len(critical_stores),
                "snapshots_24h": kpis.get("snapshots_24h"),
                "status": _kpi_status(ok=moat_ok, warn=moat_warn),
            },
        },
        "adoption": {
            "pypi_downloads_30d": adoption["pypi"].get("downloads_last_30d"),
            "pypi_downloads_7d": pypi_7d,
            "install": f.get("install"),
            "notes": adoption.get("notes", []),
        },
        "activation_detail": activation_paths,
        "alerts": alerts,
        "alert_count": alert_count,
    }


def go_live_spike_markdown(*, days: int = 7, dashboard_data: dict[str, Any] | None = None) -> str:
    """Spike D+7 table for price-pulse / founder debrief."""
    data = go_live_summary(days=days, dashboard_data=dashboard_data)
    act = data["kpis"]["activation"]
    rev = data["kpis"]["revenue"]
    pr = data["kpis"]["pricing"]
    pac = data["kpis"]["pro_activation"]
    dec = pr["decision"]
    pypi_7d = pr.get("pypi_downloads_7d")

    def _cell(val: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, float) and val < 1:
            return _pct(val)
        return str(val)

    lines = [
        "## Spike D+7 — embudo y pricing",
        "",
        f"_Ventana **{days}d** · generado {data['generated_at'][:19]} UTC_",
        "",
        "| Métrica | Valor actual | Meta | Estado |",
        "|---------|--------------|------|--------|",
        f"| PyPI 7d rolling | {_cell(pypi_7d)} | ≥{SPIKE_PYPI_7D_TARGET:,} | "
        f"{'✅' if isinstance(pypi_7d, int) and pypi_7d >= SPIKE_PYPI_7D_TARGET else '⏳'} |",
        f"| Registers (únicos) | {act['register']} | — | |",
        f"| first_search (únicos) | {act['first_search']} | — | |",
        f"| search/register | {_cell(act['search_per_register'])} | ≥{_pct(SEARCH_PER_REGISTER_TARGET)} | "
        f"{'✅' if (act['search_per_register'] or 0) >= SEARCH_PER_REGISTER_TARGET else '⏳'} |",
        f"| request_pro (únicos) | {rev['request_pro']} | — | |",
        f"| search→pro (intent) | {_cell(pr['search_to_pro'])} | ≥{_pct(SEARCH_TO_PRO_TARGET)} | "
        f"{'✅' if (pr['search_to_pro'] or 0) >= SEARCH_TO_PRO_TARGET else '⏳'} |",
        f"| activated (únicos) | {rev['activated']} | ≥{SPIKE_PRO_ACTIVATED_TARGET} | "
        f"{'✅' if rev['activated'] >= SPIKE_PRO_ACTIVATED_TARGET else '⏳'} |",
        f"| pro→activated | {_cell(pr['pro_to_activated'])} | ≥{_pct(PRO_TO_ACTIVATED_TARGET)} | "
        f"{'✅' if (pr['pro_to_activated'] or 0) >= PRO_TO_ACTIVATED_TARGET else '⏳'} |",
        f"| **pricing_health** (act/search) | {_cell(pr['pricing_health'])} | ≥{_pct(PRICING_HEALTH_OK)} | "
        f"{'✅' if (pr['pricing_health'] or 0) >= PRICING_HEALTH_OK else '⏳'} |",
        f"| TTFV mediana (min) | {act['ttfv_median_minutes'] or '—'} | <{TTFV_MEDIAN_MINUTES_TARGET:.0f} | |",
        f"| TTC mediana (h) | {pr['ttc_median_hours'] or '—'} | <{TTC_MEDIAN_HOURS_TARGET:.0f} | |",
        f"| Pro pending webhook | {pac['pending_auto']} | 0 cola | "
        f"{'✅' if pac['pending_auto'] == 0 else '⏳'} |",
        f"| Pro pending manual | {pac['pending_manual']} | 0 | "
        f"{'✅' if pac['pending_manual'] == 0 else '⚠️'} |",
        f"| activated vía webhook | {pac['webhook_activated']} | — | |",
        f"| webhook share | {_cell(pac['webhook_share'])} | ≥{_pct(WEBHOOK_ACTIVATION_SHARE_TARGET)} | "
        f"{'✅' if (pac['webhook_share'] or 0) >= WEBHOOK_ACTIVATION_SHARE_TARGET or pac['webhook_activated'] == 0 else '⏳'} |",
        f"| **unified webhook** | {'sí' if pac['unified_webhook'] else 'no'} | sí | "
        f"{'✅' if pac['unified_webhook'] else '⚠️'} |",
        "",
        f"**Decisión pricing:** **{dec['label']}** (`{dec['action']}`) — {dec['detail']}",
        "",
        "```bash",
        f"python ops/go_live_check.py --spike --days {days}",
        "```",
        "",
    ]
    return "\n".join(lines)


def go_live_markdown(*, days: int = 30, dashboard_data: dict[str, Any] | None = None) -> str:
    data = go_live_summary(days=days, dashboard_data=dashboard_data)
    act = data["kpis"]["activation"]
    rev = data["kpis"]["revenue"]
    moat = data["kpis"]["data_moat"]
    pr = data["kpis"]["pricing"]
    pac = data["kpis"]["pro_activation"]
    w = data["window_days"]

    lines = [
        f"# Go-live · {data['overall_status'].upper()}",
        "",
        f"_Ventana {w}d · generado {data['generated_at'][:19]} UTC_",
        "",
        "## KPI 1 · Activación",
        "",
        f"- Register: **{act['register']}** · First search: **{act['first_search']}**",
        f"- Search/register: **{_pct(act['search_per_register'])}** "
        f"(meta ≥{_pct(act['search_per_register_target'])})",
        f"- TTFV mediana: **{act['ttfv_median_minutes'] or '—'}** min",
        f"- Estado: **{act['status']}**",
        "",
        "## KPI 2 · Revenue",
        "",
        f"- request_pro: **{rev['request_pro']}** · Activated: **{rev['activated']}**",
        f"- Meta semanal paid: **{rev['weekly_paid_goal']}** · "
        f"Meta spike Pro: **{SPIKE_PRO_ACTIVATED_TARGET}**",
        f"- Estado: **{rev['status']}**",
        "",
        "## KPI 3 · Activación Pro (webhook)",
        "",
        f"- Pending webhook: **{pac['pending_auto']}** · Pending manual: **{pac['pending_manual']}**",
        f"- Activated webhook: **{pac['webhook_activated']}** · manual: **{pac['manual_activated']}**",
        f"- Webhook share: **{_pct(pac['webhook_share'])}** (meta ≥{_pct(WEBHOOK_ACTIVATION_SHARE_TARGET)})",
        f"- Unified: **{'sí' if pac['unified_webhook'] else 'no'}** · Estado: **{pac['status']}**",
        "",
        "## KPI 4 · Pricing / spike",
        "",
        f"- PyPI 7d: **{pr.get('pypi_downloads_7d') or '—'}** (meta ≥{SPIKE_PYPI_7D_TARGET:,})",
        f"- search→pro: **{_pct(pr['search_to_pro'])}** (meta ≥{_pct(SEARCH_TO_PRO_TARGET)})",
        f"- pro→activated: **{_pct(pr['pro_to_activated'])}** (meta ≥{_pct(PRO_TO_ACTIVATED_TARGET)})",
        f"- pricing_health: **{_pct(pr['pricing_health'])}** (meta ≥{_pct(PRICING_HEALTH_OK)})",
        f"- TTC mediana: **{pr['ttc_median_hours'] or '—'}** h",
        f"- Decisión: **{pr['decision']['label']}** — {pr['decision']['detail']}",
        f"- Estado: **{pr['status']}**",
        "",
        "## KPI 5 · Data moat",
        "",
        f"- Coverage 7d: **{moat['coverage_7d_pct']:.1f}%** (meta ≥{moat['coverage_target']:.0f}%)",
        f"- Collector: **{moat['collector_status']}** · stale: **{moat['collector_stale']}**",
        f"- Tiendas críticas: **{moat['critical_store_count']}**",
        f"- Estado: **{moat['status']}**",
        "",
    ]

    if data["alerts"]:
        lines += ["## Alertas", ""]
        for a in data["alerts"]:
            icon = {"critical": "🔴", "warn": "🟡", "info": "ℹ️"}.get(a["severity"], "•")
            lines.append(f"- {icon} [{a['severity']}] {a['message']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def go_live_slack_lines(*, days: int = 30, dashboard_data: dict[str, Any] | None = None) -> list[str]:
    data = go_live_summary(days=days, dashboard_data=dashboard_data)
    act = data["kpis"]["activation"]
    rev = data["kpis"]["revenue"]
    moat = data["kpis"]["data_moat"]
    w = data["window_days"]

    status_emoji = {"healthy": "✅", "degraded": "🟡", "critical": "🔴"}.get(
        data["overall_status"], "•"
    )

    pr = data["kpis"]["pricing"]
    pac = data["kpis"]["pro_activation"]
    lines = [
        f"{status_emoji} *Go-live* · {data['overall_status']} ({w}d)",
        "",
        f"• *Activación:* search/reg *{_pct(act['search_per_register'])}* · "
        f"TTFV *{act['ttfv_median_minutes'] or '—'}* min · {act['status']}",
        f"• *Revenue:* request_pro *{rev['request_pro']}* · activated *{rev['activated']}* · {rev['status']}",
        f"• *Pro webhook:* pending manual *{pac['pending_manual']}* · webhook share "
        f"*{_pct(pac['webhook_share'])}* · unified *{'sí' if pac['unified_webhook'] else 'no'}*",
        f"• *Pricing:* health *{_pct(pr['pricing_health'])}* · intent *{_pct(pr['search_to_pro'])}* · "
        f"{pr['decision']['label']}",
        f"• *Moat:* coverage *{moat['coverage_7d_pct']:.1f}%* · collector *{moat['collector_status']}* · "
        f"críticas *{moat['critical_store_count']}* · {moat['status']}",
        "",
    ]

    critical_alerts = [a for a in data["alerts"] if a["severity"] == "critical"]
    warn_alerts = [a for a in data["alerts"] if a["severity"] == "warn"]
    for a in critical_alerts[:5]:
        lines.append(f"🔴 {a['message']}")
    for a in warn_alerts[:3]:
        lines.append(f"🟡 {a['message']}")
    if critical_alerts or warn_alerts:
        lines.append("")

    return lines


def render_go_live_html(*, days: int = 30, dashboard_data: dict[str, Any] | None = None) -> str:
    data = go_live_summary(days=days, dashboard_data=dashboard_data)
    act = data["kpis"]["activation"]
    rev = data["kpis"]["revenue"]
    pac = data["kpis"]["pro_activation"]
    pr = data["kpis"]["pricing"]
    moat = data["kpis"]["data_moat"]

    status_color = {
        "ok": "#22c55e",
        "warn": "#eab308",
        "critical": "#ef4444",
    }
    overall_bg = {"healthy": "#14532d", "degraded": "#713f12", "critical": "#7f1d1d"}.get(
        data["overall_status"], "#1e293b"
    )

    def _card(title: str, kpi: dict[str, Any]) -> str:
        color = status_color.get(kpi.get("status", ""), "#64748b")
        return f"""
        <div class="card">
          <div class="card-head">
            <h2>{title}</h2>
            <span class="badge" style="background:{color}">{kpi.get('status', '?')}</span>
          </div>
          <pre>{_kpi_body(title, kpi)}</pre>
        </div>"""

    def _kpi_body(title: str, kpi: dict[str, Any]) -> str:
        if title == "Activación":
            return (
                f"register: {kpi['register']}\n"
                f"first_search: {kpi['first_search']}\n"
                f"search/register: {_pct(kpi['search_per_register'])}\n"
                f"ttfv_median: {kpi['ttfv_median_minutes'] or '—'} min"
            )
        if title == "Revenue":
            return (
                f"request_pro: {kpi['request_pro']}\n"
                f"activated: {kpi['activated']}\n"
                f"weekly_goal: {kpi['weekly_paid_goal']}"
            )
        if title == "Activación Pro":
            return (
                f"pending_webhook: {kpi.get('pending_auto', 0)}\n"
                f"pending_manual: {kpi.get('pending_manual', 0)}\n"
                f"webhook_activated: {kpi.get('webhook_activated', 0)}\n"
                f"webhook_share: {_pct(kpi.get('webhook_share'))}\n"
                f"unified: {kpi.get('unified_webhook')}"
            )
        if title == "Pricing":
            dec = kpi.get("decision", {})
            return (
                f"pypi_7d: {kpi.get('pypi_downloads_7d') or '—'}\n"
                f"search_to_pro: {_pct(kpi.get('search_to_pro'))}\n"
                f"pricing_health: {_pct(kpi.get('pricing_health'))}\n"
                f"decision: {dec.get('label', '—')}"
            )
        stores = kpi.get("critical_stores", []) or []
        store_lines = "\n".join(
            f"  - {html.escape(str(s.get('store', '?')))}: {s.get('success_pct')}%"
            for s in stores[:5]
        ) or "  (none)"
        return (
            f"coverage_7d: {kpi['coverage_7d_pct']:.1f}%\n"
            f"collector: {kpi['collector_status']}\n"
            f"stale: {kpi['collector_stale']}\n"
            f"critical_stores ({kpi['critical_store_count']}):\n{store_lines}"
        )

    alert_rows = ""
    for a in data["alerts"]:
        sev = a["severity"]
        # Escape any dynamic content in alerts (defense in depth even though
        # sources are internal founder metrics).
        safe_msg = html.escape(a["message"])
        alert_rows += (
            f'<div class="alert {sev}"><strong>{sev}</strong> {safe_msg}</div>\n'
        )
    if not alert_rows:
        alert_rows = '<div class="alert ok">Sin alertas activas.</div>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>CLI Market · Go-live</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background:#0a0a0a; color:#e2e8f0; margin:0; padding:24px; }}
    h1 {{ margin:0 0 8px; font-size:1.4rem; }}
    .overall {{ display:inline-block; padding:6px 12px; border-radius:6px; background:{overall_bg}; margin-bottom:20px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px; margin-bottom:24px; }}
    .card {{ background:#111; border:1px solid #333; border-radius:8px; padding:16px; }}
    .card-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }}
    .card h2 {{ margin:0; font-size:1rem; color:#94a3b8; }}
    .badge {{ padding:4px 8px; border-radius:4px; font-size:0.75rem; color:#000; font-weight:700; text-transform:uppercase; }}
    pre {{ margin:0; white-space:pre-wrap; color:#cbd5e1; font-size:0.85rem; }}
    .alerts h2 {{ font-size:1rem; color:#94a3b8; }}
    .alert {{ padding:10px 12px; border-radius:6px; margin-bottom:8px; font-size:0.85rem; }}
    .alert.critical {{ background:#450a0a; border:1px solid #ef4444; }}
    .alert.warn {{ background:#422006; border:1px solid #eab308; }}
    .alert.info {{ background:#1e293b; border:1px solid #64748b; }}
    .alert.ok {{ background:#14532d; border:1px solid #22c55e; }}
    .meta {{ color:#64748b; font-size:0.8rem; margin-top:24px; }}
    a {{ color:#38bdf8; }}
  </style>
</head>
<body>
  <h1>CLI Market · Go-live dashboard</h1>
  <div class="overall">Estado: {data['overall_status']} · ventana {data['window_days']}d</div>
  <div class="grid">
    {_card("Activación", act)}
    {_card("Revenue", rev)}
    {_card("Activación Pro", pac)}
    {_card("Pricing", pr)}
    {_card("Data moat", moat)}
  </div>
  <div class="alerts">
    <h2>Alertas ({data['alert_count']['critical']} críticas · {data['alert_count']['warn']} warn)</h2>
    {alert_rows}
  </div>
  <p class="meta">
    JSON: <a href="/dashboard/go-live?days={data['window_days']}">/dashboard/go-live</a> ·
    Moat: <a href="/dashboard">/dashboard</a> ·
    Adopción: <a href="/analytics/adoption">/analytics/adoption</a>
  </p>
</body>
</html>"""