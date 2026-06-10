#!/usr/bin/env python3
"""Command & Control — panel diario founder para CLI Market.

Publica en Slack #command-control-cli-market:
  - Checklist de comandos founder (producto + GTM)
  - KPIs actuales + tendencia visual (sparklines 7–14 días)
  - Estado PAM, go-live, index, collector

Usage:
  python ops/command_control_daily.py              # imprime + guarda snapshot
  python ops/command_control_daily.py --slack      # postea al canal C&C
  python ops/command_control_daily.py --dry-run    # no guarda ni postea
  python ops/command_control_daily.py --remote     # KPIs desde producción

Schedule (primary: GitHub Actions command-control-morning.yml at 12:30 UTC):
  curl -X POST "$MARKET_API_URL/admin/cron/command-control" -H "Authorization: Bearer $MARKET_API_TOKEN"

Env:
  SLACK_BOT_TOKEN
  SLACK_CHANNEL_COMMAND_CONTROL   — ID del canal (o auto-resolve por nombre)
  MARKET_API_TOKEN                — dashboard/index remoto
  DASHBOARD_DATA_URL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_env import load_repo_env  # noqa: E402

load_repo_env()


def _default_metrics_dir() -> Path:
    override = os.getenv("COMMAND_CONTROL_METRICS_DIR", "").strip()
    if override:
        return Path(override)
    data_root = os.getenv("MARKET_DATA_DIR", "").strip()
    if data_root:
        return Path(data_root) / "metrics" / "command-control"
    return Path(__file__).resolve().parent / "metrics" / "command-control"


METRICS_DIR = _default_metrics_dir()
HISTORY_FILE = METRICS_DIR / "history.jsonl"
CHANNEL_NAME = "command-control-cli-market"

DASHBOARD_URL = os.getenv(
    "DASHBOARD_DATA_URL",
    "https://cli-market-production.up.railway.app/dashboard/data",
)
API_BASE = os.getenv(
    "MARKET_API_URL",
    DASHBOARD_URL.rsplit("/dashboard/data", 1)[0]
    or "https://cli-market-production.up.railway.app",
)
REPORT_DIR = Path(__file__).resolve().parent / "reports"

FOUNDER_COMMANDS: list[dict[str, Any]] = [
    {
        "block": "🌅 Mañana — producto",
        "items": [
            ("1", "python ops/command_control_daily.py --slack", "Este panel (KPIs + checklist)"),
            ("2", "python ops/daily_briefing.py", "Bitácora producto + contenido → Slack"),
            ("3", "python ops/go_live_check.py --remote", "3 north-star KPIs + alerts"),
            ("3b", "python ops/adoption_index.py --github", "Adoption Index V1 (score + snapshot)"),
            ("4", "python ops/production_acceptance.py --phase user --tier 2", "PAM tier 2 (smoke usuario)"),
        ],
    },
    {
        "block": "📊 Data moat",
        "items": [
            ("5", "python collect_prices.py --report", "Collector KPIs (crudo)"),
            ("5b", "python ops/collector_daily_report.py", "Collector daily sweep + health (auto)"),
            ("5c", "python ops/collector_daily_report.py --slack", "Post collector health a bitácora / C&C"),
            ("5d", "python ops/observatory_daily.py", "Observatory snapshot (MAA + retención MCP)"),
            ("6", "curl -H 'Authorization: Bearer $MARKET_API_TOKEN' $API/index/stats", "Golden Records + linkage %"),
            ("7", "python ops/go_live_check.py --remote --json", "JSON para scripts / alertas"),
        ],
    },
    {
        "block": "📣 GTM (cli-market-content)",
        "items": [
            ("8", "cd ../cli-market-content && make today", "Qué publicar hoy (10 canales)"),
            ("9", "make gate && make gate-remote", "Data-gate local + API live"),
            ("10", "make content && make brief", "Copy + briefing LinkedIn"),
            ("11", "make publish day=N", "Marcar día publicado"),
        ],
    },
    {
        "block": "🔁 Semanal / spike",
        "items": [
            ("12", "python ops/production_acceptance.py --tier 2", "PAM completo tier 2"),
            ("13", "python ops/payments_e2e.py", "Rails de pago (PayPal/MP/Yape/Plin)"),
            ("14", "python ops/go_live_check.py --spike --remote", "Post-spike pricing decision"),
            ("15", "cd ../cli-market-content && make week2", "Monetización semana 2"),
        ],
    },
    {
        "block": "🚀 Post-deploy",
        "items": [
            ("16", ".\\ops\\deploy_landing.ps1", "Landing Cloudflare (si UI)"),
            ("17", "Ver ops/PYPI_RELEASE.md", "Release CLI PyPI"),
            ("18", "railway up", "API Railway tras cambios backend"),
        ],
    },
]

BOOKMARKS = [
    ("Landing", "https://cli-market.dev"),
    ("Stats (público)", "https://cli-market.dev/stats"),
    ("Dashboard", f"{API_BASE}/dashboard"),
    ("Observatory API", f"{API_BASE}/analytics/observatory"),
    ("Adoption Index", f"{API_BASE}/analytics/adoption-index"),
    ("Health", f"{API_BASE}/health"),
    ("Health DB", f"{API_BASE}/health/db"),
    ("PyPI", "https://pypi.org/project/cli-market-world/"),
    ("GitHub", "https://github.com/Treevu-ai/cli-market-world"),
]

# Meta internas (alineadas con market_golive.py)
COVERAGE_7D_TARGET = 80.0
LINKAGE_TARGET = 85.0
STORE_SUCCESS_WARN = 70.0


def _sparkline(values: list[float], *, width: int = 10) -> str:
    """Unicode mini-chart (oldest → newest, left to right)."""
    if not values:
        return "—"
    subset = values[-width:]
    lo, hi = min(subset), max(subset)
    if hi == lo:
        return "▄" * len(subset)
    blocks = "▁▂▃▄▅▆▇█"
    out: list[str] = []
    for v in subset:
        idx = int((v - lo) / (hi - lo) * (len(blocks) - 1))
        out.append(blocks[idx])
    return "".join(out)


def _status_emoji(level: str) -> str:
    return {"ok": "✅", "warn": "🟡", "critical": "🔴", "info": "ℹ️"}.get(level, "•")


def _metric_level(
    value: float,
    *,
    target: float | None = None,
    warn_below: float | None = None,
    higher_is_better: bool = True,
) -> str:
    if target is not None:
        if higher_is_better:
            if value >= target:
                return "ok"
            if warn_below is not None and value >= warn_below:
                return "warn"
            return "critical"
        if value <= target:
            return "ok"
        return "warn"
    return "ok"


def _delta_str(current: float, previous: float | None, *, pct: bool = False) -> str:
    if previous is None:
        return ""
    diff = current - previous
    if pct:
        return f" ({diff:+.1f}pp)"
    if abs(diff) < 1:
        return f" ({diff:+.0f})"
    return f" ({diff:+,.0f})"


def _load_history() -> list[dict[str, Any]]:
    if not HISTORY_FILE.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _save_snapshot(snapshot: dict[str, Any]) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.open("a", encoding="utf-8").write(
        json.dumps(snapshot, ensure_ascii=False) + "\n"
    )


def _latest_pam() -> dict[str, Any] | None:
    reports = sorted(REPORT_DIR.glob("pam-*.json"), reverse=True)
    for path in reports[:5]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "summary" in data or "results" in data:
                return {"file": path.name, **data}
        except Exception:
            continue
    return None


def _pam_summary(pam: dict[str, Any] | None) -> dict[str, Any]:
    if not pam:
        return {"status": "unknown", "pass": 0, "fail": 0, "skip": 0}
    summary = pam.get("summary") or {}
    passed = summary.get("PASS", summary.get("pass", summary.get("passed", 0)))
    failed = summary.get("FAIL", summary.get("fail", summary.get("failed", 0)))
    skipped = summary.get("SKIP", summary.get("skip", summary.get("skipped", 0)))
    status = "ok" if int(failed or 0) == 0 else "fail"
    return {
        "status": status,
        "pass": int(passed or 0),
        "fail": int(failed or 0),
        "skip": int(skipped or 0),
        "file": pam.get("file", ""),
    }


def _fetch_dashboard_remote() -> dict[str, Any] | None:
    token = os.getenv("MARKET_API_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(DASHBOARD_URL, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) and "error" not in data else None
    except Exception:
        return None


def _normalize_adoption_index_payload(data: dict[str, Any]) -> dict[str, Any]:
    signals = data.get("signals") or {}
    pypi = signals.get("pypi") or {}
    funnel = signals.get("funnel") or {}
    breakdown = data.get("breakdown") or {}
    score = data.get("score")
    return {
        "ok": score is not None,
        "score": float(score or 0),
        "grade": data.get("grade") or "?",
        "source": data.get("source", "unknown"),
        "downloads_30d": pypi.get("downloads_30d"),
        "downloads_7d": pypi.get("downloads_7d"),
        "downloads_30d_raw": pypi.get("downloads_30d_raw"),
        "downloads_7d_raw": pypi.get("downloads_7d_raw"),
        "downloads_30d_no_ci": pypi.get("downloads_30d_no_ci"),
        "downloads_7d_no_ci": pypi.get("downloads_7d_no_ci"),
        "ci_share_pct_30d": pypi.get("ci_share_pct_30d"),
        "pypi_windows_source": pypi.get("windows_source"),
        "growth_pct": pypi.get("growth_pct_7d_vs_baseline"),
        "first_search": funnel.get("first_search"),
        "register": funnel.get("register"),
        "request_pro": funnel.get("request_pro"),
        "activated": funnel.get("activated"),
        "real_usage_score": (breakdown.get("real_usage") or {}).get("score"),
        "downloads_score": (breakdown.get("downloads") or {}).get("score"),
        "computed_at": data.get("computed_at"),
    }


def _fetch_adoption_index(*, remote: bool) -> dict[str, Any]:
    if remote:
        try:
            r = httpx.get(f"{API_BASE}/analytics/adoption-index", timeout=25)
            if r.status_code == 200 and isinstance(r.json(), dict):
                return _normalize_adoption_index_payload(r.json())
        except Exception:
            pass
    try:
        from market_adoption_index import compute_adoption_index, latest_snapshot, score_grade

        snap = latest_snapshot()
        if snap and snap.get("score") is not None:
            payload = {
                "score": snap["score"],
                "grade": score_grade(float(snap["score"])),
                "breakdown": snap.get("breakdown"),
                "signals": snap.get("signals"),
                "computed_at": snap.get("created_at"),
                "source": "snapshot",
            }
        else:
            live = compute_adoption_index(days=30, include_github=False)
            payload = {
                "score": live["score"],
                "grade": live["grade"],
                "breakdown": live["breakdown"],
                "signals": live["signals"],
                "computed_at": live["computed_at"],
                "source": "live",
            }
        return _normalize_adoption_index_payload(payload)
    except Exception:
        return {"ok": False}


def _fetch_index_stats_remote() -> dict[str, Any] | None:
    token = os.getenv("MARKET_API_TOKEN", "")
    if not token:
        return None
    try:
        r = httpx.get(
            f"{API_BASE}/index/stats",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _fetch_index_stats_local() -> dict[str, Any] | None:
    try:
        from index_gate import index_stats

        return index_stats()
    except Exception:
        return None


def _fetch_observatory(*, remote: bool) -> dict[str, Any]:
    """MAA + MCP telemetry aggregates (30d window; DAA = 1d MAA)."""
    if remote:
        try:
            r30 = httpx.get(f"{API_BASE}/analytics/observatory?days=30", timeout=25)
            if r30.status_code == 200 and isinstance(r30.json(), dict):
                data = r30.json()
                try:
                    r1 = httpx.get(f"{API_BASE}/analytics/observatory?days=1", timeout=15)
                    if r1.status_code == 200:
                        data["daa"] = int(r1.json().get("maa") or 0)
                except Exception:
                    data["daa"] = None
                return data
        except Exception:
            pass
    try:
        from market_observatory import observatory_summary

        data = observatory_summary(days=30)
        data["daa"] = int(observatory_summary(days=1).get("maa") or 0)
        return data
    except Exception:
        return {"telemetry_enabled": False}


def _collect_metrics(*, remote: bool) -> dict[str, Any]:
    today = date.today().isoformat()
    dash = _fetch_dashboard_remote() if remote else None
    if dash is None and not remote:
        try:
            from routers.dashboard import dashboard_data

            dash = dashboard_data()
        except Exception:
            dash = {}

    kpis = (dash or {}).get("kpis", {})
    coll = (dash or {}).get("collector", {})
    moat = (dash or {}).get("moat_summary", {})

    index = (
        _fetch_index_stats_remote()
        if remote
        else _fetch_index_stats_local() or _fetch_index_stats_remote()
    )

    golive: dict[str, Any] = {}
    try:
        from market_golive import go_live_summary

        golive = go_live_summary(days=30, dashboard_data=dash)
    except Exception:
        golive = {}

    pam = _pam_summary(_latest_pam())
    act = golive.get("kpis", {}).get("activation", {})
    rev = golive.get("kpis", {}).get("revenue", {})

    return {
        "date": today,
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "remote" if remote else "local",
        "moat": {
            "total_indexed": int(kpis.get("total_indexed", 0) or 0),
            "snapshots_24h": int(kpis.get("snapshots_24h", 0) or 0),
            "stores_indexed": int(kpis.get("stores_indexed", 0) or 0),
            "coverage_7d_pct": float(kpis.get("coverage_7d_pct", 0) or 0),
            "store_success_pct": float(kpis.get("store_success_pct", 0) or 0),
            "healthy_stores": int(kpis.get("healthy_stores", 0) or 0),
            "total_stores": int(kpis.get("total_stores", 0) or 0),
            "collector_status": coll.get("status", "?"),
            "collector_stale": bool(moat.get("collector_stale", False)),
        },
        "index": {
            "registry_size": int((index or {}).get("registry_size", 0) or 0),
            "linkage_pct": float((index or {}).get("linkage_pct", 0) or 0),
            "snapshots_linked": int((index or {}).get("snapshots_linked", 0) or 0),
        },
        "golive": {
            "overall": golive.get("overall_status", "unknown"),
            "moat_status": golive.get("kpis", {}).get("data_moat", {}).get("status", "unknown"),
            "register_30d": int(act.get("register", 0) or 0),
            "first_search_30d": int(act.get("first_search", 0) or 0),
            "search_per_register": act.get("search_per_register"),
            "request_pro_30d": int(rev.get("request_pro", 0) or 0),
            "activated_30d": int(rev.get("activated", 0) or 0),
            "alerts": [
                a for a in golive.get("alerts", [])
                if a.get("severity") in ("critical", "warn")
            ][:5],
        },
        "pam": pam,
        "adoption_index": _fetch_adoption_index(remote=remote),
        "observatory": _fetch_observatory(remote=remote),
    }


def _pct(rate: float | None) -> str:
    if rate is None:
        return "—"
    return f"{rate * 100:.1f}%"


def _adoption_clause(gl: dict[str, Any], adoption_index: dict[str, Any] | None = None) -> str:
    """Frase de adopción que no suene rota con funnel en cero."""
    ai = adoption_index or {}
    index_bit = ""
    if ai.get("ok"):
        index_bit = f" Adoption Index *{ai['score']:.0f}/100* ({ai['grade']})."
    reg = int(gl.get("register_30d", 0) or 0)
    activated = int(gl.get("activated_30d", 0) or 0)
    if reg > 0:
        return (
            f"Adopción (30 d): {reg} registros, búsqueda/registro {_pct(gl.get('search_per_register'))}, "
            f"{activated} cuentas Pro activadas.{index_bit}"
        )
    if activated > 0:
        pro_note = (
            "1 cuenta Pro activada manualmente."
            if activated == 1
            else f"{activated} cuentas Pro activadas manualmente."
        )
        return (
            f"Adopción (30 d): funnel en soft-launch (0 registros públicos); {pro_note}{index_bit}"
        )
    if index_bit:
        return f"Adopción (30 d): etapa pre-tracción — foco en moat y GTM.{index_bit}"
    return "Adopción (30 d): etapa pre-tracción — foco en moat y GTM."


def _executive_story(metrics: dict[str, Any]) -> str:
    """2–3 frases que un founder puede leer en voz alta."""
    m = metrics["moat"]
    ix = metrics["index"]
    gl = metrics["golive"]
    pam = metrics["pam"]

    fresh = "fresco" if not m["collector_stale"] else "atrasado (stale)"
    linkage_clause = (
        f"El {ix['linkage_pct']:.0f}% de esos precios ya tienen identidad de producto "
        f"única (Golden Record prod_*)."
        if ix["linkage_pct"] >= 50
        else "Falta vincular más snapshots al índice semántico (linkage bajo)."
    )

    caveats: list[str] = []
    if m["store_success_pct"] < STORE_SUCCESS_WARN:
        caveats.append(
            f"⚠️ solo {m['healthy_stores']} de {m['total_stores']} retailers respondieron bien "
            f"({m['store_success_pct']:.0f}%) — conviene explicarlo como deuda de conectores, "
            "no como caída del moat"
        )
    if pam["skip"] > 0 and pam["fail"] == 0:
        caveats.append(f"ℹ️ PAM con {pam['skip']} SKIP (checks sin credenciales o entorno)")

    quality = (
        f"PAM: {pam['pass']} pruebas OK, {pam['fail']} fallos."
        if pam["fail"] == 0
        else f"⚠️ PAM con {pam['fail']} fallo(s) — revisar antes de demos."
    )

    caveat_txt = (" " + " · ".join(caveats)) if caveats else ""
    return (
        f"Hoy operamos *{m['total_indexed']:,} precios* de *{m['stores_indexed']} retailers*; "
        f"el collector está *{fresh}* y actualizó *{m['snapshots_24h']:,}* precios en 24 h. "
        f"{linkage_clause} {_adoption_clause(gl, metrics.get('adoption_index'))} {quality}{caveat_txt}"
    )


def _story_layers(metrics: dict[str, Any]) -> list[str]:
    """Arco Recolectar → Normalizar → Entregar (el 'por qué' del stack)."""
    m, ix, pam = metrics["moat"], metrics["index"], metrics["pam"]
    return [
        "*🧱 La historia en 3 capas*",
        "_Usa esto cuando alguien pregunte '¿qué hacen?' — no sueltes números sueltos._",
        "",
        "1️⃣ *Recolectamos* — APIs VTEX, no scraping. "
        f"{m['total_indexed']:,} precios · {m['stores_indexed']} cadenas · actualización cada ~4 h.",
        "2️⃣ *Normalizamos* — Golden Record = «ISBN del súper». "
        f"{ix['registry_size']:,} productos únicos · {ix['linkage_pct']:.0f}% del moat ya etiquetado.",
        "3️⃣ *Entregamos* — API + CLI + MCP para agentes. "
        f"PAM: {pam['pass']} OK, {pam['fail']} fallos — el producto responde en producción.",
        "",
    ]


def _audience_scripts(metrics: dict[str, Any]) -> list[str]:
    """Una línea por audiencia — copiar según con quién hablas."""
    m, ix, gl = metrics["moat"], metrics["index"], metrics["golive"]
    store_note = ""
    if m["store_success_pct"] < STORE_SUCCESS_WARN:
        store_note = (
            f" (hoy {m['healthy_stores']} de {m['total_stores']} cadenas con fetch exitoso; "
            "ampliamos catálogo y aún no todos los conectores están al día)"
        )
    return [
        "*🎤 Según con quién hables*",
        "",
        f"• *Inversor:* «Construimos un moat de precios de retail en LATAM: "
        f"{m['total_indexed']:,} observaciones, {ix['linkage_pct']:.0f}% con identidad semántica. "
        "Es barrera de datos más comparación entre cadenas que un LLM no puede inventar.»",
        f"• *Retailer VTEX:* «Vemos su catálogo junto al de {m['stores_indexed']} cadenas "
        f"con el mismo producto normalizado: Price Pulse y benchmarking sin integración adicional"
        f"{store_note}.»",
        f"• *Dev / agente:* «Un endpoint: búsqueda, comparación y checkout. "
        f"{ix['registry_size']:,} Golden Records, linkage {ix['linkage_pct']:.0f}%, "
        f"{metrics['pam']['pass']} checks PAM en verde en producción.»",
        f"• *Tú mismo (1 línea):* {_adoption_clause(gl, metrics.get('adoption_index'))}",
        "",
    ]


def _metric_cards(metrics: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Cada métrica: valor, meta, qué es, cómo explicarla."""
    m = metrics["moat"]
    ix = metrics["index"]
    pam = metrics["pam"]
    prev = history[-1] if history else {}
    pm, pi = prev.get("moat", {}), prev.get("index", {})

    cards: list[dict[str, str]] = [
        {
            "key": "total_indexed",
            "title": "Precios indexados (moat)",
            "value": f"{m['total_indexed']:,}",
            "level": "ok",
            "meta": "Crece con cada ciclo del collector",
            "meaning": "Total de snapshots de precio en PostgreSQL — el inventario histórico del data moat.",
            "why": "Sin volumen histórico no hay tendencia ni inflación observada — es el activo que vendemos.",
            "say": (
                f"«Indexamos {m['total_indexed']:,} precios reales de góndola; "
                "no es scraping, son APIs de retailers.»"
            ),
            "delta": _delta_str(m["total_indexed"], pm.get("total_indexed")),
        },
        {
            "key": "snapshots_24h",
            "title": "Refresh 24h",
            "value": f"{m['snapshots_24h']:,}",
            "level": "critical" if m["collector_stale"] else "ok",
            "meta": "Debe moverse cada ~4h si el collector corre",
            "meaning": "Cuántos precios se actualizaron en el último día — proxy de frescura operativa.",
            "why": "Un moat viejo es un catálogo muerto; esto prueba que el pipeline corre.",
            "say": (
                f"«En las últimas 24h actualizamos {m['snapshots_24h']:,} precios; "
                f"el ciclo objetivo es cada 4 horas.»"
            ),
            "delta": _delta_str(m["snapshots_24h"], pm.get("snapshots_24h")),
        },
        {
            "key": "coverage_7d",
            "title": "Coverage 7 días",
            "value": f"{m['coverage_7d_pct']:.1f}%",
            "level": _metric_level(
                m["coverage_7d_pct"],
                target=COVERAGE_7D_TARGET,
                warn_below=60,
            ),
            "meta": f"Meta ≥ {COVERAGE_7D_TARGET:.0f}%",
            "meaning": "Porcentaje de tiendas con al menos un snapshot en los últimos 7 días.",
            "why": "Distingue 'tenemos muchos precios' de 'cubrimos todas las cadenas activas'.",
            "say": (
                f"«{m['coverage_7d_pct']:.0f}% de nuestras tiendas tuvieron dato fresco esta semana; "
                "mide si el moat cubre el catálogo o solo un subconjunto.»"
            ),
            "delta": _delta_str(m["coverage_7d_pct"], pm.get("coverage_7d_pct"), pct=True),
        },
        {
            "key": "store_success",
            "title": "Tiendas sanas",
            "value": f"{m['healthy_stores']}/{m['total_stores']} ({m['store_success_pct']:.0f}%)",
            "level": _metric_level(
                m["store_success_pct"],
                target=STORE_SUCCESS_WARN,
                warn_below=45,
            ),
            "meta": "Éxito de fetch por retailer",
            "meaning": "Cuántos retailers respondieron bien en el último barrido (sin timeout ni error API).",
            "why": "Bajo % ≠ moat roto: muchas cadenas están en onboarding; alto % = confianza para demos.",
            "say": (
                f"«{m['healthy_stores']} de {m['total_stores']} cadenas respondieron bien — "
                "si baja, hay conectores rotos o retailers caídos.»"
            ),
            "delta": "",
        },
        {
            "key": "registry",
            "title": "Golden Records",
            "value": f"{ix['registry_size']:,}",
            "level": "ok" if ix["registry_size"] > 0 else "warn",
            "meta": "prod_* en cli-market-index",
            "meaning": "Productos canónicos únicos (marca + nombre + medida normalizada) — el grafo semántico.",
            "why": "Es el «ISBN del súper»: sin esto, cada retailer habla un idioma distinto.",
            "say": (
                f"«Tenemos {ix['registry_size']:,} productos únicos normalizados; "
                "dos SKUs distintos del mismo aceite 900ml colapsan al mismo prod_*.»"
            ),
            "delta": _delta_str(ix["registry_size"], pi.get("registry_size")),
        },
        {
            "key": "linkage",
            "title": "Linkage %",
            "value": f"{ix['linkage_pct']:.1f}%",
            "level": _metric_level(
                ix["linkage_pct"],
                target=LINKAGE_TARGET,
                warn_below=70,
            ),
            "meta": f"Meta ≥ {LINKAGE_TARGET:.0f}%",
            "meaning": "Qué porcentaje del moat ya está etiquetado con un Golden Record (canonical_product_id).",
            "why": "Linkage alto = comparar precios entre cadenas sin fuzzy match en cada query.",
            "say": (
                f"«El {ix['linkage_pct']:.1f}% del inventario ya está vinculado a identidad de producto — "
                "sin esto, comparar entre cadenas es fuzzy match cada vez.»"
            ),
            "delta": _delta_str(ix["linkage_pct"], pi.get("linkage_pct"), pct=True),
        },
        {
            "key": "pam",
            "title": "PAM (smoke prod)",
            "value": f"{pam['pass']} PASS · {pam['fail']} FAIL · {pam['skip']} SKIP",
            "level": "critical" if pam["fail"] else ("warn" if pam["skip"] else "ok"),
            "meta": "Tier 2 = flujos usuario críticos",
            "meaning": "Production Acceptance Matrix — batería automática contra API, pagos, index, search.",
            "why": "Traduce «los números se ven bien» en «un agente puede buscar y comprar sin sorpresas».",
            "say": (
                f"«Corrimos {pam['pass']} checks en producción; "
                + (
                    "ningún fallo en flujos críticos.»"
                    if pam["fail"] == 0
                    else (
                        f"{pam['fail']} fallo significa algo roto para clientes o agentes.»"
                        if pam["fail"] == 1
                        else f"{pam['fail']} fallos significan algo roto para clientes o agentes.»"
                    )
                )
            ),
            "delta": "",
        },
    ]
    return cards


def _explain_section(cards: list[dict[str, str]], *, brief: bool = True) -> list[str]:
    """Glosario: en brief solo expande métricas 🟡/🔴; el resto va en tabla compacta."""
    lines = [
        "*📖 Glosario rápido*",
        "_Semáforo = salud · «Por qué importa» = argumento · «Cómo decirlo» = frase lista para una call._",
        "",
    ]

    if brief:
        lines.append("*Resumen*")
        for c in cards:
            lines.append(
                f"{_status_emoji(c['level'])} *{c['title']}:* {c['value']}{c['delta']}"
            )
        lines.append("")

        attention = [c for c in cards if c["level"] in ("warn", "critical")]
        if attention:
            lines.append("*Detalle (solo lo que conviene explicar hoy)*")
            for c in attention:
                lines.extend(_card_detail_lines(c))
        else:
            lines.append("_Todo verde — si preguntan, usa las frases de «Según con quién hables» arriba._")
            lines.append("")
        return lines

    lines.append("*Todas las métricas*")
    for c in cards:
        lines.extend(_card_detail_lines(c))
    return lines


def _card_detail_lines(c: dict[str, str]) -> list[str]:
    out = [
        f"{_status_emoji(c['level'])} *{c['title']}:* *{c['value']}*{c['delta']}",
        f"    _Qué es:_ {c['meaning']}",
        f"    _Por qué importa:_ {c.get('why', '—')}",
        f"    _Meta:_ {c['meta']}",
        f"    _Cómo decirlo:_ {c['say']}",
        "",
    ]
    return out


def _fmt_int(n: int | float | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}"


def _pypi_ci_suffix(ai: dict[str, Any]) -> str:
    """Annotate PyPI line with raw totals and CI share when Pepy Pro data is present."""
    raw = ai.get("downloads_30d_raw")
    ci = ai.get("ci_share_pct_30d")
    if raw is None and ci is None:
        return ""
    parts: list[str] = []
    if raw is not None:
        parts.append(f"raw {_fmt_int(raw)}")
    if isinstance(ci, (int, float)):
        parts.append(f"CI {ci:.0f}%")
    src = ai.get("pypi_windows_source")
    if src == "pro_no_ci":
        parts.append("no-CI")
    return f" ({' · '.join(parts)})" if parts else ""


def _observatory_section(
    metrics: dict[str, Any],
    history: list[dict[str, Any]],
) -> list[str]:
    obs = metrics.get("observatory") or {}
    if not obs.get("telemetry_enabled"):
        return [
            "*🔭 Observatory (MAA)*",
            "",
            "_Telemetría MCP desactivada o sin datos — `OBSERVATORY_TELEMETRY=1` en Railway._",
            "",
        ]

    def series() -> list[float]:
        out: list[float] = []
        for row in history:
            node = row.get("observatory") or {}
            try:
                out.append(float(node.get("maa", 0)))
            except (TypeError, ValueError):
                pass
        return out

    prev = history[-1].get("observatory", {}) if history else {}
    prev_maa = prev.get("maa")
    maa = int(obs.get("maa") or 0)
    daa = obs.get("daa")
    daa_txt = f"*{_fmt_int(daa)}*" if daa is not None else "—"
    sr = obs.get("success_rate")
    sr_txt = f"{float(sr) * 100:.1f}%" if isinstance(sr, (int, float)) else "—"
    ret7 = (obs.get("mcp_retention_7d") or {}).get("retention_rate")
    ret_txt = f"{float(ret7) * 100:.1f}%" if isinstance(ret7, (int, float)) else "—"
    calls = int(obs.get("calls_success") or 0)

    return [
        "*🔭 Observatory (MAA)*",
        "",
        f"• MAA 30d: `{_sparkline(series())}` *{maa:,}*"
        f"{_delta_str(maa, prev_maa)} · DAA *{daa_txt}*",
        f"• Consultas exitosas 30d: *{calls:,}* · success rate *{sr_txt}* · retención 7d *{ret_txt}*",
        f"• Top tool: *{(obs.get('top_tools') or [{}])[0].get('name', '—')}* · "
        f"países activos *{int(obs.get('countries_active') or 0)}*",
        "",
    ]


def _adoption_index_section(
    metrics: dict[str, Any],
    history: list[dict[str, Any]],
) -> list[str]:
    ai = metrics.get("adoption_index") or {}
    if not ai.get("ok"):
        return [
            "*📈 Adoption Index V1*",
            "",
            "_Sin snapshot — `python ops/adoption_index.py` o cron `adoption-index-nightly`._",
            "",
        ]

    def series() -> list[float]:
        out: list[float] = []
        for row in history:
            node = row.get("adoption_index") or {}
            try:
                out.append(float(node.get("score", 0)))
            except (TypeError, ValueError):
                pass
        return out

    prev = history[-1].get("adoption_index", {}) if history else {}
    prev_score = prev.get("score")
    growth = ai.get("growth_pct")
    growth_txt = f"{growth:+.1f}%" if isinstance(growth, (int, float)) else "—"

    return [
        "*📈 Adoption Index V1*",
        "",
        f"• Score: `{_sparkline(series())}` *{ai['score']:.1f}/100* · grade *{ai['grade']}*"
        f"{_delta_str(ai['score'], prev_score)} · fuente `{ai.get('source', '?')}`",
        f"• PyPI 30d: *{_fmt_int(ai.get('downloads_30d'))}*"
        f"{_pypi_ci_suffix(ai)} · 7d: *{_fmt_int(ai.get('downloads_7d'))}*"
        f" · growth 7d: *{growth_txt}*",
        f"• Embudo 30d: register *{_fmt_int(ai.get('register'))}* → first_search *"
        f"{_fmt_int(ai.get('first_search'))}* · Pro *{_fmt_int(ai.get('request_pro'))}*"
        f" → activated *{_fmt_int(ai.get('activated'))}*",
        f"• Sub-scores: downloads *{ai.get('downloads_score', '—')}* · "
        f"real usage *{ai.get('real_usage_score', '—')}*",
        "",
    ]


def _scoreboard(metrics: dict[str, Any]) -> list[str]:
    """Tabla de un vistazo — números sin narrativa (ya cubierta arriba)."""
    m, ix, gl, pam, ai = (
        metrics["moat"],
        metrics["index"],
        metrics["golive"],
        metrics["pam"],
        metrics.get("adoption_index") or {},
    )
    coll = "OK" if not m["collector_stale"] else "STALE"
    adoption_line = (
        f"Adoption Index {ai['score']:.0f}/100 ({ai['grade']})"
        if ai.get("ok")
        else "Adoption Index —"
    )
    obs = metrics.get("observatory") or {}
    maa_line = (
        f"MAA {int(obs.get('maa') or 0):,}"
        if obs.get("telemetry_enabled")
        else "MAA —"
    )
    return [
        "*📊 Scoreboard*",
        "",
        f"Moat {m['total_indexed']:,} · 24h {m['snapshots_24h']:,} · "
        f"coverage {m['coverage_7d_pct']:.0f}% · collector {coll}",
        f"Index {ix['registry_size']:,} GR · linkage {ix['linkage_pct']:.1f}%",
        f"PAM {pam['pass']}/{pam['fail']}/{pam['skip']} (pass/fail/skip) · "
        f"go-live {gl.get('overall', '?')}",
        adoption_line,
        maa_line,
        "",
    ]


def _priority_actions(metrics: dict[str, Any]) -> list[str]:
    """1–4 acciones concretas según estado (no checklist genérico)."""
    m, pam, gl = metrics["moat"], metrics["pam"], metrics["golive"]
    actions: list[tuple[int, str]] = []

    if pam["fail"] > 0:
        actions.append((1, f"🔴 PAM con {pam['fail']} FAIL → `python ops/production_acceptance.py --tier 2` y revisar ops/reports/"))
    if m["collector_stale"]:
        actions.append((1, "🔴 Collector stale → revisar servicio collector en Railway + `python collect_prices.py --status`"))
    if m["coverage_7d_pct"] < COVERAGE_7D_TARGET:
        actions.append((2, f"🟡 Coverage {m['coverage_7d_pct']:.0f}% < {COVERAGE_7D_TARGET:.0f}% → `make gate-remote` y tiendas con <30% éxito"))
    if metrics["index"]["linkage_pct"] < LINKAGE_TARGET:
        actions.append((3, f"🟡 Linkage {metrics['index']['linkage_pct']:.1f}% → `POST /index/backfill` o esperar ciclo index post-collector"))
    for a in gl.get("alerts", [])[:2]:
        sev = a.get("severity", "warn")
        icon = "🔴" if sev == "critical" else "🟡"
        actions.append((2 if sev == "critical" else 4, f"{icon} {a.get('message', '')}"))

    if not actions:
        actions.append((5, "✅ Sin acciones urgentes — ejecutar checklist matutino (briefing + go-live + content `make today`)"))

    actions.sort(key=lambda x: x[0])
    lines = ["*🎯 Prioridad hoy*", ""]
    for i, (_, msg) in enumerate(actions[:4], 1):
        lines.append(f"{i}. {msg}")
    lines.append("")
    return lines


def _trend_reading(history: list[dict[str, Any]], current: dict[str, Any]) -> str:
    if len(history) < 2:
        return ""
    prev = history[-1]
    m0, m1 = prev.get("moat", {}), current["moat"]
    parts: list[str] = []
    di = m1["total_indexed"] - int(m0.get("total_indexed", 0) or 0)
    if di != 0:
        parts.append(f"moat {'creció' if di > 0 else 'bajó'} {di:+,}")
    ds = m1["snapshots_24h"] - int(m0.get("snapshots_24h", 0) or 0)
    if ds != 0:
        parts.append(f"refresh 24h {ds:+,}")
    ai0 = prev.get("adoption_index", {})
    ai1 = current.get("adoption_index") or {}
    if ai0.get("score") is not None and ai1.get("ok"):
        ds_score = float(ai1["score"]) - float(ai0.get("score", 0) or 0)
        if abs(ds_score) >= 0.5:
            parts.append(f"Adoption Index {ds_score:+.1f}pp")
    if not parts:
        return ""
    return "_Lectura tendencia vs ayer:_ " + " · ".join(parts) + "."


def _trend_section(history: list[dict[str, Any]], current: dict[str, Any]) -> list[str]:
    """Sparklines + lectura vs ayer."""
    if len(history) < 2:
        return [
            "_Tendencia:_ sin histórico aún; mañana verás sparklines (▁▂▃▄▅▆▇█ = sube o baja en el tiempo)._",
            "",
        ]

    def series(key_path: str) -> list[float]:
        out: list[float] = []
        for row in history:
            node: Any = row
            for part in key_path.split("."):
                node = (node or {}).get(part, {})
            try:
                out.append(float(node))
            except (TypeError, ValueError):
                pass
        return out

    prev = history[-1] if history else {}
    m = current["moat"]
    ix = current["index"]
    pm = prev.get("moat", {})
    pi = prev.get("index", {})

    reading = _trend_reading(history, current)
    lines = [
        "*📈 Tendencia* (▁→█ = más reciente a la derecha)",
        "",
        f"• Moat: `{_sparkline(series('moat.total_indexed'))}` *{m['total_indexed']:,}*"
        f"{_delta_str(m['total_indexed'], pm.get('total_indexed'))}",
        f"• Refresh 24h: `{_sparkline(series('moat.snapshots_24h'))}` *{m['snapshots_24h']:,}*"
        f"{_delta_str(m['snapshots_24h'], pm.get('snapshots_24h'))}",
        f"• Coverage 7d: `{_sparkline(series('moat.coverage_7d_pct'))}` *{m['coverage_7d_pct']:.1f}%*"
        f"{_delta_str(m['coverage_7d_pct'], pm.get('coverage_7d_pct'), pct=True)}",
        f"• Golden Records: `{_sparkline(series('index.registry_size'))}` *{ix['registry_size']:,}*"
        f"{_delta_str(ix['registry_size'], pi.get('registry_size'))}",
        f"• Linkage: `{_sparkline(series('index.linkage_pct'))}` *{ix['linkage_pct']:.1f}%*"
        f"{_delta_str(ix['linkage_pct'], pi.get('linkage_pct'), pct=True)}",
    ]
    ai = current.get("adoption_index") or {}
    if ai.get("ok"):
        pai = prev.get("adoption_index", {})
        lines.append(
            f"• Adoption Index: `{_sparkline(series('adoption_index.score'))}` "
            f"*{ai['score']:.1f}*"
            f"{_delta_str(ai['score'], pai.get('score'))}"
        )
    obs = current.get("observatory") or {}
    if obs.get("telemetry_enabled"):
        pobs = prev.get("observatory", {})
        lines.append(
            f"• MAA: `{_sparkline(series('observatory.maa'))}` "
            f"*{int(obs.get('maa') or 0):,}*"
            f"{_delta_str(int(obs.get('maa') or 0), pobs.get('maa'))}"
        )
    lines.append("")
    if reading:
        lines.append(reading)
        lines.append("")
    return lines


def _checklist_section() -> list[str]:
    lines = ["*✅ Checklist founder (completo)*", ""]
    for block in FOUNDER_COMMANDS:
        lines.append(f"*{block['block']}*")
        for num, cmd, why in block["items"]:
            lines.append(f"`{num}.` `{cmd}` — _{why}_")
        lines.append("")
    return lines


def build_message(*, remote: bool = False, brief: bool = True) -> str:
    history = _load_history()
    metrics = _collect_metrics(remote=remote)
    gl = metrics["golive"]
    cards = _metric_cards(metrics, history)

    status_emoji = {"healthy": "✅", "degraded": "🟡", "critical": "🔴"}.get(
        gl.get("overall"), "⚪"
    )

    lines = [
        f"🎛️ *Command & Control* · {metrics['date']}",
        "",
        "*📣 En una frase*",
        _executive_story(metrics),
        "",
        f"{status_emoji} *Go-live:* {gl.get('overall', '?')} · "
        f"moat {gl.get('moat_status', '?')} · "
        f"collector {'stale' if metrics['moat']['collector_stale'] else metrics['moat']['collector_status']}",
        "",
    ]
    lines.extend(_story_layers(metrics))
    lines.extend(_priority_actions(metrics))
    lines.extend(_audience_scripts(metrics))
    lines.extend(_scoreboard(metrics))
    lines.extend(_observatory_section(metrics, history))
    lines.extend(_adoption_index_section(metrics, history))
    lines.extend(_explain_section(cards, brief=brief))
    lines.extend(_trend_section(history, metrics))

    if brief:
        lines.append("_Checklist completo:_ `python ops/command_control_daily.py --slack --full`")
        lines.append("")
    else:
        lines.extend(_checklist_section())

    lines.append("*🔗 Bookmarks*")
    for label, url in BOOKMARKS:
        lines.append(f"• <{url}|{label}>")
    lines.append("")
    lines.append(
        "_Bitácora = narrativa diaria · Command & Control = métricas y cómo explicarlas._ "
        "`python ops/command_control_daily.py --slack --remote`"
    )
    return "\n".join(lines)


def publish_command_control(
    *,
    remote: bool = True,
    brief: bool = True,
    save: bool = True,
) -> dict[str, Any]:
    """Build panel, optionally persist metrics, post to #command-control-cli-market."""
    metrics = _collect_metrics(remote=remote)
    text = build_message(remote=remote, brief=brief)
    if save:
        _save_snapshot(metrics)
    from slack_notify import deliver_to_command_control

    deliver_to_command_control(text)
    return {
        "ok": True,
        "posted": True,
        "remote": remote,
        "brief": brief,
        "preview": text[:800],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="CLI Market Command & Control panel")
    parser.add_argument("--slack", action="store_true", help="Post to #command-control-cli-market")
    parser.add_argument("--dry-run", action="store_true", help="Print only; no save, no Slack")
    parser.add_argument("--remote", action="store_true", help="Fetch KPIs from production API")
    parser.add_argument("--json", action="store_true", help="JSON metrics snapshot")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Incluir checklist founder completo (default en Slack: solo narrativa + prioridades)",
    )
    args = parser.parse_args()

    metrics = _collect_metrics(remote=args.remote)
    brief = not args.full

    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print(build_message(remote=args.remote, brief=brief))

    if not args.dry_run:
        _save_snapshot(metrics)

    if args.slack and not args.dry_run:
        from slack_notify import deliver_to_command_control

        deliver_to_command_control(build_message(remote=args.remote, brief=brief))
        print("Slack → command-control-cli-market", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())