"""Agentic Commerce Pulse — weekly BBVA-style research report from moat data."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from market_brief import KPI_LABELS, build_brief_summary


def iso_week(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    cal = now.isocalendar()
    return f"{cal.year}-W{cal.week:02d}"


def _extract_inflation(brief: dict) -> float | None:
    shelf = brief.get("shelf") or {}
    for key in ("retail_price_velocity_7d_pct", "shelf_price_avg_delta_pct", "shelf_inflation_avg_pct"):
        val = shelf.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    for ind in brief.get("analytics", {}).get("indicators", []):
        if ind.get("key") in ("shelf_inflation_avg_pct", "retail_price_velocity_7d_pct"):
            val = ind.get("value")
            if val is not None:
                return float(val)
    return None


def _extract_anomaly_from_core_subcats(sub_items: list[dict]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_abs = 0.0
    for item in sub_items:
        key = str(item.get("key") or "")
        if "momentum" not in key and "price" not in key:
            continue
        val = item.get("value")
        if val is None:
            continue
        try:
            delta = float(val)
        except (TypeError, ValueError):
            continue
        if abs(delta) <= best_abs:
            continue
        best_abs = abs(delta)
        name = key.replace("subcat_", "").replace("_price_momentum", "").replace("_", " ")
        best = {"subcategory": name, "delta_pct": round(delta, 1)}
    return best


def adapt_brief_for_summary(brief: dict) -> dict:
    """Normalize core or router brief payloads for build_brief_summary."""
    adapted = dict(brief)
    shelf = dict(brief.get("shelf") or {})
    infl = _extract_inflation(brief)
    if infl is not None and "shelf_inflation_avg_pct" not in shelf:
        analytics = dict(adapted.get("analytics") or {})
        indicators = list(analytics.get("indicators") or [])
        if not any(i.get("key") == "shelf_inflation_avg_pct" for i in indicators):
            indicators.append({"key": "shelf_inflation_avg_pct", "value": infl})
        analytics["indicators"] = indicators
        adapted["analytics"] = analytics

    sub_block = brief.get("subcategories") or {}
    sub_items = sub_block.get("subcategories") or []
    if sub_items and not (isinstance(sub_items[0], dict) and sub_items[0].get("signals")):
        anomaly = _extract_anomaly_from_core_subcats(sub_items)
        if anomaly:
            adapted.setdefault("subcategories", {})["subcategories"] = [
                {
                    "subcategory": anomaly["subcategory"],
                    "signals": {"subcat_price_momentum": {"value": anomaly["delta_pct"]}},
                }
            ]
    return adapted


def _moat_kpis(dashboard: dict | None) -> dict[str, Any]:
    if not dashboard:
        return {}
    kpis = dashboard.get("kpis") or {}
    return {
        "total_indexed": kpis.get("total_indexed"),
        "snapshots_24h": kpis.get("snapshots_24h"),
        "stores_indexed": kpis.get("stores_indexed"),
        "stores_fresh_24h": kpis.get("stores_fresh_24h"),
        "coverage_7d_pct": kpis.get("coverage_7d_pct"),
    }


def _is_publishable(brief: dict, dashboard: dict | None) -> bool:
    cov = (brief.get("confidence") or {}).get("coverage") or {}
    if cov.get("publishable") is False:
        return False
    if cov.get("publishable") is True:
        return True
    moat = _moat_kpis(dashboard)
    coverage = moat.get("coverage_7d_pct")
    if coverage is not None:
        return float(coverage) >= 60.0
    return True


def build_executive_highlights(
    summary: dict[str, Any],
    *,
    brief: dict,
    moat: dict[str, Any],
    lang: str = "es",
) -> list[str]:
    """BBVA-style causal bullets — rule-based narrative from verified signals."""
    es = lang != "en"
    highlights: list[str] = []
    cc = summary.get("country", "PE")
    days = summary.get("days", 7)
    infl = summary.get("inflation_pct")

    if infl is not None:
        if infl > 3:
            highlights.append(
                (
                    f"La presión al alza en góndola se intensificó en {cc}: señal de inflación retail "
                    f"{infl:+.1f}% en {days}d (collector CLI Market, no IPC oficial)."
                )
                if es
                else (
                    f"Shelf inflation pressure intensified in {cc}: retail price signal "
                    f"{infl:+.1f}% over {days}d (CLI Market collector — not official CPI)."
                )
            )
        elif infl < -1:
            highlights.append(
                (
                    f"Precios de referencia cedieron {infl:.1f}% en {cc} durante los últimos {days} días."
                )
                if es
                else f"Reference prices eased {infl:.1f}% in {cc} over the last {days} days."
            )
        else:
            highlights.append(
                (
                    f"Inflación retail estable en {cc} ({infl:+.1f}% en {days}d) según precios de góndola online."
                )
                if es
                else f"Retail inflation stable in {cc} ({infl:+.1f}% over {days}d) from online shelf prices."
            )

    pvi = summary.get("pvi")
    if pvi is not None and float(pvi) >= 15:
        highlights.append(
            (
                f"Dispersión de precios elevada (PVI {float(pvi):.0f}%): comparar retailers "
                "antes de comprar o abastecer."
            )
            if es
            else (
                f"Elevated price dispersion (PVI {float(pvi):.0f}%): cross-retailer comparison "
                "matters more this week."
            )
        )

    anomaly = summary.get("largest_anomaly")
    if anomaly:
        name = anomaly.get("subcategory", "—")
        delta = anomaly.get("delta_pct", 0)
        highlights.append(
            (
                f"Mayor anomalía detectada: {name} {delta:+.1f}% vs mediana del mercado monitoreado."
            )
            if es
            else f"Largest anomaly: {name} {delta:+.1f}% vs monitored market median."
        )

    bai = summary.get("bai")
    if bai is not None:
        bai_f = float(bai)
        if bai_f > 105 or (0 < bai_f <= 1 and bai_f > 0.5):
            highlights.append(
                (
                    "Estrés de canasta básica elevado (BAI): presión en staples de consumo masivo."
                )
                if es
                else "Elevated basic basket stress (BAI): pressure on mass-consumption staples."
            )

    pdi = summary.get("pdi")
    if pdi is not None and float(pdi) > 1.0:
        highlights.append(
            (
                "Intensidad promocional alta (PDI): validar descuentos reales vs precio de lista inflado."
            )
            if es
            else "High promo intensity (PDI): verify real discounts vs inflated list prices."
        )

    indexed = moat.get("total_indexed")
    fresh = moat.get("snapshots_24h")
    if indexed and fresh:
        highlights.append(
            (
                f"Data moat activo: {int(indexed):,} precios indexados · {int(fresh):,} refresh 24h."
            )
            if es
            else f"Data moat active: {int(indexed):,} indexed prices · {int(fresh):,} refreshed 24h."
        )

    aff = brief.get("affordability") or {}
    if aff.get("headline_es") and es:
        highlights.append(str(aff["headline_es"]))
    elif aff.get("band"):
        highlights.append(
            f"Affordability band: {aff.get('band')} (CLI Market affordability score)."
        )

    return highlights[:6]


def build_narrative_paragraphs(highlights: list[str], *, lang: str = "es") -> list[str]:
    """Turn highlights into short causal paragraphs for the report body."""
    if not highlights:
        msg = (
            "Señales insuficientes para narrativa causal esta semana — ejecutá "
            "`market indicators --refresh` y reintentá."
        )
        if lang == "en":
            msg = (
                "Insufficient signals for causal narrative this week — run "
                "`market indicators --refresh` and retry."
            )
        return [msg]

    es = lang != "en"
    lead = (
        "Este pulso resume qué cambió en el comercio online monitoreado y por qué importa "
        "para compras, procurement y agentes IA."
    )
    if not es:
        lead = (
            "This pulse summarizes what changed in monitored online commerce and why it "
            "matters for shopping, procurement, and AI agents."
        )
    return [lead, *highlights[:4]]


def build_commerce_pulse(
    brief: dict,
    *,
    dashboard: dict | None = None,
    country: str = "PE",
    days: int = 7,
    lang: str = "es",
) -> dict[str, Any]:
    """Assemble Agentic Commerce Pulse payload (JSON + markdown)."""
    now = datetime.now(timezone.utc)
    adapted = adapt_brief_for_summary(brief)
    summary = build_brief_summary(adapted)
    summary["country"] = (country or summary.get("country") or "PE").upper()
    summary["days"] = days
    moat = _moat_kpis(dashboard)
    highlights = build_executive_highlights(summary, brief=brief, moat=moat, lang=lang)
    narrative = build_narrative_paragraphs(highlights, lang=lang)
    publishable = _is_publishable(brief, dashboard)

    pulse = {
        "report": "agentic_commerce_pulse",
        "title": f"Agentic Commerce Pulse — {summary['country']}",
        "country": summary["country"],
        "week": iso_week(now),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": days,
        "lang": lang,
        "publishable": publishable,
        "headline": brief.get("headline") or summary.get("headline"),
        "executive_highlights": highlights,
        "narrative": narrative,
        "executive": summary,
        "kpis": {
            "pvi": summary.get("pvi"),
            "bai": summary.get("bai"),
            "pdi": summary.get("pdi"),
            "rcs": summary.get("rcs"),
            "inflation_pct": summary.get("inflation_pct"),
        },
        "moat": moat,
        "confidence": brief.get("confidence") or {},
        "disclaimer": brief.get("disclaimer")
        or (
            "Señales de precios de góndola online — no equivalentes a IPC oficial. "
            "Metodología CLI Market collector."
        ),
        "brief": brief,
    }
    pulse["markdown"] = render_pulse_markdown(pulse)
    return pulse


def render_pulse_markdown(pulse: dict[str, Any]) -> str:
    """BBVA-inspired markdown report for publishing."""
    cc = pulse.get("country", "PE")
    week = pulse.get("week", iso_week())
    title = pulse.get("title", f"Agentic Commerce Pulse — {cc}")
    generated = (pulse.get("generated_at") or "")[:10]
    publishable = "true" if pulse.get("publishable") else "false"
    exec_lines = "\n".join(f"- {h}" for h in pulse.get("executive_highlights") or [])
    narrative = "\n\n".join(pulse.get("narrative") or [])
    kpis = pulse.get("kpis") or {}
    moat = pulse.get("moat") or {}
    summary = pulse.get("executive") or {}

    kpi_rows = []
    for key, label in (
        ("inflation_pct", "Retail inflation (7d)"),
        ("pvi", f"{KPI_LABELS['pvi'][0]} · dispersion"),
        ("bai", f"{KPI_LABELS['bai'][0]} · basket stress"),
        ("pdi", f"{KPI_LABELS['pdi'][0]} · promos"),
        ("rcs", f"{KPI_LABELS['rcs'][0]} · fairness"),
    ):
        val = kpis.get(key)
        if val is None:
            disp = "—"
        elif key == "inflation_pct":
            disp = f"{float(val):+.1f}%"
        elif key in ("pvi", "rcs"):
            disp = f"{float(val):.1f}"
        else:
            disp = str(val)
        kpi_rows.append(f"| {label} | {disp} |")

    moat_rows = []
    for label, key in (
        ("Prices indexed", "total_indexed"),
        ("Refresh 24h", "snapshots_24h"),
        ("Stores indexed", "stores_indexed"),
        ("Coverage 7d", "coverage_7d_pct"),
    ):
        val = moat.get(key)
        if val is None:
            continue
        if key == "coverage_7d_pct":
            moat_rows.append(f"| {label} | {float(val):.1f}% |")
        else:
            moat_rows.append(f"| {label} | {int(val):,} |")

    anomaly = summary.get("largest_anomaly")
    anomaly_line = ""
    if anomaly:
        anomaly_line = (
            f"\n**Largest anomaly:** {anomaly.get('subcategory')} "
            f"{anomaly.get('delta_pct'):+.1f}% vs median\n"
        )

    return f"""---
title: {title}
country: {cc}
week: {week}
generated_at: {pulse.get("generated_at")}
publishable: {publishable}
report: agentic_commerce_pulse
---

# CLI Market — Agentic Commerce Pulse
## LatAm Retail Outlook · {cc} · {week}

> {pulse.get("headline", "")}

### Executive Highlights

{exec_lines or "- (no highlights — refresh indicators)"}
{anomaly_line}
### This Week in Commerce

| KPI | Value |
|-----|-------|
{chr(10).join(kpi_rows)}

### Data Moat Snapshot

| Metric | Value |
|--------|-------|
{chr(10).join(moat_rows) or "| — | — |"}

### Market Narrative

{narrative}

### Methodology

{pulse.get("disclaimer", "")}

---
*Generated {generated} · CLI Market Intelligence · `market brief -c {cc}` · `GET /v1/intel/pulse`*
"""


def maybe_enhance_narrative_with_llm(pulse: dict[str, Any]) -> dict[str, Any]:
    """Optional LLM polish when OPENAI_API_KEY is set and MARKET_PULSE_LLM=1."""
    if os.getenv("MARKET_PULSE_LLM", "").strip() not in ("1", "true", "yes"):
        return pulse
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return pulse
    try:
        import httpx

        prompt = (
            "Rewrite these commerce intelligence bullets as 2 short BBVA-style causal paragraphs. "
            "Keep all numbers exact. Spanish if lang=es.\n\n"
            f"lang={pulse.get('lang', 'es')}\n"
            f"highlights={pulse.get('executive_highlights')}"
        )
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.getenv("MARKET_PULSE_LLM_MODEL", "gpt-4o-mini"),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        if text:
            pulse["narrative"] = [text]
            pulse["llm_enhanced"] = True
            pulse["markdown"] = render_pulse_markdown(pulse)
    except Exception:
        pass
    return pulse


def build_price_pulse_markdown(dashboard: dict, meta: dict | None = None) -> str:
    """Backward-compatible entry for ops/sync_linkedin_metrics (replaces monday.py)."""
    del meta  # unused — kept for call-site compatibility
    pulse = generate_commerce_pulse(country="PE", days=7, lang="es", dashboard=dashboard, llm=True)
    return pulse["markdown"]


def generate_commerce_pulse(
    *,
    country: str = "PE",
    days: int = 7,
    lang: str = "es",
    dashboard: dict | None = None,
    llm: bool = False,
) -> dict[str, Any]:
    """Build pulse from live DB + optional dashboard KPIs."""
    from market_core import get_db
    from market_indicators import build_intel_brief

    cc = (country or "PE").strip().upper()[:2]
    db = get_db()
    brief = build_intel_brief(db, country=cc, days=max(1, days))
    db.close()
    pulse = build_commerce_pulse(brief, dashboard=dashboard, country=cc, days=days, lang=lang)
    if llm:
        pulse = maybe_enhance_narrative_with_llm(pulse)
    return pulse
