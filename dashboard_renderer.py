"""Server-rendered dashboard HTML from dashboard_view + metric_glossary only."""

from __future__ import annotations

import html


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _render_global_bar(view: dict, glossary: dict) -> str:
    bar = (view.get("blocks") or {}).get("global_bar") or {}
    collector = _esc(bar.get("collector_label", "unknown"))
    fresh = bar.get("fresh_24h_pct")
    fresh_label = f"FRESH {fresh:.1f}% &lt;24h" if fresh is not None else "FRESH —"
    utc = _esc(bar.get("utc_time", "—"))
    return f"""
<header class="global-bar" id="global-bar">
  <span class="bar-item">COLLECTOR <b class="state-{bar.get('collector_status', 'unknown')}">{collector}</b></span>
  <span class="bar-sep">|</span>
  <span class="bar-item">{fresh_label}</span>
   <span class="bar-sep">|</span>
   <span class="bar-item">UTC {utc}</span>
   <span class="bar-sep">|</span>
   <button type="button" class="bar-refresh-btn" id="refresh-trigger" title="Disparar un ciclo del collector ahora">&#x1F504; Refresh</button>
   <span class="bar-sep">|</span>
   <button type="button" class="bar-glossary-btn" id="glossary-toggle" aria-expanded="false">[?] glosario</button>
</header>
<details class="glossary-panel" id="glossary-panel">
  <summary class="glossary-summary">Glosario de métricas</summary>
  {_render_glossary(glossary)}
</details>
"""


def _render_glossary(glossary: dict) -> str:
    legend = glossary.get("status_legend") or {}
    legend_items = "".join(
        f"<li><code>{_esc(k)}</code> — {_esc(v)}</li>" for k, v in legend.items()
    )
    sections = glossary.get("sections") or {}
    section_blocks = []
    for sid, sec in sections.items():
        if not isinstance(sec, dict):
            continue
        title = _esc(sec.get("title") or sid)
        summary = _esc(sec.get("summary") or "")
        metrics = sec.get("metrics") if isinstance(sec.get("metrics"), dict) else {}
        metric_rows = "".join(
            f"<tr><td>{_esc(m.get('label', key))}</td><td class='metric-desc'>{_esc(m.get('description', ''))}</td></tr>"
            for key, m in metrics.items()
            if isinstance(m, dict)
        )
        table = (
            f"<table><tr><th>Métrica</th><th>Significado</th></tr>{metric_rows}</table>"
            if metric_rows
            else ""
        )
        section_blocks.append(
            f"<div class='glossary-section'><h3>{title}</h3>"
            f"<p class='metric-desc'>{summary}</p>{table}</div>"
        )
    return f"""
<ul class="glossary">{legend_items}</ul>
{"".join(section_blocks[:8])}
"""


def _render_portada(view: dict) -> str:
    portada = (view.get("blocks") or {}).get("portada") or {}
    cards = portada.get("cards") or []
    cards_html = "".join(
        f"""<div class="portada-card">
  <div class="card-label">{_esc(c.get('label', ''))}</div>
  <div class="card-value">{_esc(c.get('headline', ''))}</div>
  <div class="card-sub">{_esc(c.get('subline', ''))}</div>
</div>"""
        for c in cards
    )
    acceso = portada.get("acceso") or []
    acceso_html = ""
    if acceso:
        acceso_rows = "".join(
            f"<tr><td><code>{_esc(a.get('cmd', ''))}</code></td><td class='metric-desc'>{_esc(a.get('desc', ''))}</td></tr>"
            for a in acceso
        )
        acceso_html = f"""
  <div class="acceso-block">
    <div class="section">[ ACCESO ]</div>
    <p class="section-intro">Endpoints existentes — copiar y ejecutar contra la misma base que alimenta este panel.</p>
    <table><tr><th>Comando</th><th>Qué devuelve</th></tr>{acceso_rows}</table>
  </div>"""
    return f"""
<section class="portada">
  <div class="portada-cards">{cards_html}</div>{acceso_html}
</section>
"""


def _render_quality_funnel(view: dict) -> str:
    funnel = (view.get("blocks") or {}).get("quality_funnel") or {}
    if not funnel:
        return ""
    captured = funnel.get("captured", 0)
    flagged = funnel.get("flagged", 0)
    clean = funnel.get("clean", 0)
    citable = funnel.get("citable", 0)
    filters = ", ".join(funnel.get("filters") or [])
    flagged_rows = ""
    for item in funnel.get("flagged_samples") or []:
        flagged_rows += (
            f"<tr class='dirty-row'><td>{_esc(item.get('name', '')[:40])}</td>"
            f"<td>{_esc(item.get('store_name', ''))}</td>"
            f"<td>{_esc(item.get('reason', ''))}</td></tr>"
        )
    flagged_table = (
        f"""<details class="anomalies-panel dirty-section">
  <summary>Anomalías flagged ({flagged:,}) — transparencia</summary>
  <table><tr><th>Producto</th><th>Tienda</th><th>Motivo</th></tr>{flagged_rows or '<tr><td colspan=3>sin muestras</td></tr>'}</table>
</details>"""
        if flagged_rows
        else ""
    )
    health = funnel.get("scraping_health") or {}
    health_rows = "".join(
        f"<tr><td>{_esc(r.get('store', ''))}</td>"
        f"<td class='state-{_esc(r.get('state', 'unknown'))}'>{_esc(r.get('state', ''))}</td>"
        f"<td>{r.get('success_pct', 0):.0f}%</td>"
        f"<td>{r.get('consecutive_failures', 0)}</td>"
        f"<td>{r.get('coverage_7d_pct', 0):.1f}%</td></tr>"
        for r in health.get("stores") or []
    )
    return f"""
<section class="quality-layer">
  <div class="section">[ CALIDAD Y CONFIANZA ]</div>
  <p class="section-intro">Embudo de filtrado estricto — volumen bruto vs dato publicable.</p>
  <div class="funnel">
    <div class="funnel-step"><span class="funnel-n">{captured:,}</span> capturados</div>
    <div class="funnel-arrow">→</div>
    <div class="funnel-step funnel-flagged"><span class="funnel-n">{flagged:,}</span> flagged</div>
    <div class="funnel-arrow">→</div>
    <div class="funnel-step funnel-clean"><span class="funnel-n">{clean:,}</span> clean</div>
    <div class="funnel-arrow">→</div>
    <div class="funnel-step funnel-citable"><span class="funnel-n">{citable:,}</span> citable</div>
  </div>
  <p class="layer-note">Filtros: {filters}</p>
  <p class="layer-note">Nombres de producto con gramaje no parseable: {funnel.get('non_normalizable_names', 0):,}</p>
  <p class="layer-note">Confianza por snapshot: {" · ".join(f"{k}: {v:,}" for k, v in sorted((funnel.get('confidence_dist') or {}).items())) or "sin datos"}</p>
  <div class="section" style="margin-top:12px">[ SALUD SCRAPING ]</div>
  <p class="layer-note">{_esc(health.get('summary', ''))}</p>
  <table><tr><th>Tienda</th><th>Estado</th><th>Éxito</th><th>Fallos seguidos</th><th>Cobertura 7d</th></tr>{health_rows or '<tr><td colspan=5>sin datos</td></tr>'}</table>
  {flagged_table}
</section>
"""


def _render_hero(view: dict) -> str:
    hero = (view.get("blocks") or {}).get("hero") or {}
    sticky = hero.get("sticky", True)
    sticky_cls = " hero-sticky" if sticky else ""
    badges = "".join(
        f"""<div class="badge" title="{_esc(b.get('tooltip', ''))}">
<span class="badge-main">{_esc(b.get('label', ''))}</span>
<span class="badge-sub">{_esc(b.get('sublabel', ''))}</span>
</div>"""
        for b in hero.get("trust_badges") or []
    )
    status = hero.get("system_status") or {}
    status_label = _esc(status.get("label", ""))
    status_state = _esc(status.get("state", "unknown"))
    return f"""
<div class="hero-panel{sticky_cls}">
<h1 class="hero-title">{_esc(hero.get('title', 'CLI Market'))}</h1>
<p class="hero-sub">{_esc(hero.get('subtitle', ''))}</p>
<div class="badges">{badges}</div>
<p class="system-status state-{status_state}">estado: {status_label}</p>
</div>
"""


def _render_moat(view: dict) -> str:
    moat = (view.get("blocks") or {}).get("moat_narrative") or {}
    pillars = "".join(
        f"""<div class="pillar">
<b>{_esc(p.get('title', ''))}</b><br>
<span class="pillar-head">{_esc(p.get('headline', ''))}</span>
<p class="metric-desc">{_esc(p.get('microcopy', ''))}</p>
</div>"""
        for p in moat.get("pillars") or []
    )
    growth = moat.get("growth_chart") or {}
    growth_html = ""
    if growth.get("state") == "live":
        growth_html = (
            f"""<p class="metric-desc">{_esc(growth.get('label', ''))}</p>"""
            f"""<p class="layer-note">"""
            f"""Snapshots acumulados: {growth.get('total_snapshots', 0):,} · """
            f"""Promedio diario (7d): {growth.get('avg_daily_7d', 0):,.0f} · """
            f"""Días con datos: {growth.get('days_tracked', 0)} · """
            f"""Collector cada {growth.get('collector_interval_h', 8)}h"""
            f"""</p>"""
        )
    else:
        growth_html = f"""<p class="metric-desc">{_esc(growth.get('label', ''))}</p>"""
    return f"""
<div class="section">[ {_esc(moat.get('title', '').upper())} ]</div>
<p class="section-intro">{_esc(moat.get('intro', ''))}</p>
<div class="pillars">{pillars}</div>
{growth_html}
"""


def _render_canasta(view: dict) -> str:
    canasta = (view.get("blocks") or {}).get("canasta") or {}
    rows = ""
    for s in canasta.get("stores") or []:
        warn = f"<br><span class='warn'>{_esc(s['warning'])}</span>" if s.get("warning") else ""
        total_cell = _esc(s.get("total_label", "—")) if s.get("comparable") else "—"
        rows += (
            f"<tr><td>{_esc(s.get('store_name', ''))}</td>"
            f"<td>{_esc(s.get('completeness_label', ''))}{warn}</td>"
            f"<td class='num'>{total_cell}</td></tr>"
        )
    cheap_rows = "".join(
        f"<tr><td>{_esc(i.get('copy', ''))}</td><td class='metric-desc'>{_esc(i.get('microcopy', ''))}</td></tr>"
        for i in (canasta.get("cheapest_by_line") or {}).get("items") or []
    )
    return f"""
<div class="section clean-section">[ {_esc(canasta.get('title', '').upper())} ]</div>
<p class="section-intro">{_esc(canasta.get('subtitle', ''))}</p>
<p class="layer-note">{_esc(canasta.get('quality_note', ''))}</p>
<table><tr><th>Tienda</th><th>Completitud</th><th>Total canasta</th></tr>{rows or '<tr><td colspan=3>sin datos</td></tr>'}</table>
<div class="section" style="margin-top:12px">[ {_esc((canasta.get('cheapest_by_line') or {}).get('title', '').upper())} ]</div>
<table><tr><th>Comparación</th><th>Nota</th></tr>{cheap_rows or '<tr><td colspan=2>sin datos</td></tr>'}</table>
"""


def _render_spreads(view: dict) -> str:
    spreads = (view.get("blocks") or {}).get("price_spreads") or {}
    rows = "".join(
        f"<tr><td>{_esc(i.get('copy', ''))}</td><td>{_esc(i.get('stores_label', ''))}</td></tr>"
        for i in spreads.get("items") or []
    )
    body = (
        "<p class='metric-desc'>Aún no hay diferencias verificadas que superen el umbral mínimo.</p>"
        if not rows
        else f"<table><tr><th>Diferencia</th><th>Cobertura</th></tr>{rows}</table>"
    )
    return f"""
<div class="section clean-section">[ {_esc(spreads.get('title', '').upper())} ]</div>
<p class="section-intro">{_esc(spreads.get('subtitle', ''))}</p>
<p class="layer-note">{_esc(spreads.get('quality_note', ''))}</p>
{body}
"""


def _render_inflation(view: dict) -> str:
    inflation = (view.get("blocks") or {}).get("inflation") or {}
    if inflation.get("measuring"):
        body = f"<div class='help-box'>{_esc(inflation.get('measuring_copy', ''))}</div>"
    else:
        infl_rows = "".join(
            f"<tr><td>{_esc(r.get('copy', ''))}</td></tr>" for r in inflation.get("rows") or []
        )
        body = f"<table>{infl_rows}</table>" if infl_rows else "<p class='metric-desc'>Sin variación esta semana.</p>"
    return f"""
<div class="section">[ {_esc(inflation.get('title', '').upper())} ]</div>
<p class="section-intro">{_esc(inflation.get('term_tooltip', ''))}</p>
{body}
"""


def _render_enrichment(view: dict) -> str:
    enrichment = (view.get("blocks") or {}).get("enrichment") or {}
    items = enrichment.get("items") or []
    if not items:
        return ""

    # Cross-table: indicator → country → value
    indicators: dict[str, dict] = {}
    countries_seen: set[str] = set()
    for item in items:
        key = item.get("key", "")
        cc = item.get("country") or "Global"
        countries_seen.add(cc)
        if key not in indicators:
            indicators[key] = {
                "name": item.get("name", key),
                "source": item.get("source", ""),
                "countries": {},
            }
        indicators[key]["countries"][cc] = item.get("value_label", "—")

    country_order = sorted(countries_seen - {"Global"})
    if "Global" in countries_seen:
        country_order.insert(0, "Global")

    # Header
    header = "<tr><th>Indicador</th><th>Fuente</th>"
    for cc in country_order:
        header += f"<th class='num'>{_esc(cc)}</th>"
    header += "</tr>"

    # Rows
    rows = ""
    for _key, info in sorted(indicators.items()):
        rows += f"<tr><td>{_esc(info['name'])}</td><td class='metric-desc'>{_esc(info['source'])}</td>"
        for cc in country_order:
            val = info["countries"].get(cc, "—")
            rows += f"<td class='num'>{_esc(val)}</td>"
        rows += "</tr>"

    count = len(indicators)
    return f"""
<section class="exploration-layer">
  <div class="section clean-section">[ {_esc(enrichment.get('title', 'INDICADORES DE MERCADO').upper())} ]</div>
  <p class="section-intro">{_esc(enrichment.get('subtitle', ''))}</p>
  <div style="overflow-x:auto"><table>{header}{rows}</table></div>
  <p class="layer-note">{count} indicadores · {len(country_order)} países · {_esc(enrichment.get('docs', ''))}</p>
</section>
"""


def _render_exploration(view: dict) -> str:
    expl = (view.get("blocks") or {}).get("exploration") or {}
    if not expl:
        return ""
    by_line_rows = "".join(
        f"<tr><td>{_esc(r.get('line_name', ''))}</td>"
        f"<td class='num'>{r.get('count', 0):,}</td>"
        f"<td class='num'>{_esc(r.get('currency', ''))} {(r.get('p25') or 0):.2f}</td>"
        f"<td class='num'>{(r.get('p50') or 0):.2f}</td>"
        f"<td class='num'>{(r.get('p75') or 0):.2f}</td>"
        f"<td class='num'>{_esc(r.get('normalized_unit') or '—')}</td>"
        f"<td class='num'>{r.get('normalizable_pct', 0):.0f}%</td></tr>"
        for r in expl.get("by_line_currency") or []
    )
    dispersion_rows = "".join(
        f"<tr class='dirty-row'><td>{_esc(r.get('line', ''))}</td>"
        f"<td>{_esc(r.get('subcategory') or '—')}</td>"
        f"<td>{_esc(r.get('currency', ''))}</td>"
        f"<td class='num'>{(r.get('spread_ratio') or 0):.1f}x</td></tr>"
        for r in expl.get("dispersion_sample") or []
    )
    disp_block = f"""
<details class="dirty-section dirty-collapsed">
  <summary>Brechas sospechosas (dispersion crit) — colapsado</summary>
  <table><tr><th>Categoría</th><th>Subcat</th><th>Moneda</th><th>Brecha</th></tr>{dispersion_rows}</table>
</details>"""
    return f"""
<section class="exploration-layer">
  <div class="filter-bar">
    <label class="toggle-clean"><input type="checkbox" id="clean-toggle" checked> solo dato limpio</label>
  </div>
  <div class="section clean-section">[ PRECIOS POR CATEGORÍA ]</div>
  <table><tr><th>Categoría</th><th>Precios</th><th>P25</th><th>Mediana</th><th>P75</th><th>Unidad</th><th>Normalizable</th></tr>{by_line_rows or '<tr><td colspan=7>sin datos</td></tr>'}</table>
  {disp_block if dispersion_rows else ''}
</section>
"""


def _render_ops(view: dict) -> str:
    ops = (view.get("blocks") or {}).get("ops") or {}
    collapsed = ops.get("collapsed_default", True)
    open_attr = "" if collapsed else " open"
    sections_html = []
    for sec in ops.get("sections") or []:
        sid = sec.get("id", "")
        title = _esc(sec.get("title", sid))
        intro = f"<p class='metric-desc'>{_esc(sec.get('intro', ''))}</p>" if sec.get("intro") else ""
        if sid == "store_health":
            rows = "".join(
                f"<tr><td>{_esc(h.get('store', ''))}</td>"
                f"<td>{(h.get('success_pct') or 0):.0f}%</td>"
                f"<td>{h.get('consecutive_failures', 0)}</td></tr>"
                for h in sec.get("items") or []
            )
            sections_html.append(
                f"<h4>{title}</h4>{intro}"
                f"<table><tr><th>Tienda</th><th>Éxito</th><th>Fallos</th></tr>{rows}</table>"
            )
        elif sid == "collector":
            coll = sec.get("collector") or {}
            hist = sec.get("history") or []
            hist_rows = "".join(
                f"<tr><td>{_esc(h.get('started_at', ''))[:19]}</td>"
                f"<td>{h.get('stores_succeeded', 0)}/{h.get('stores_attempted', 0)}</td>"
                f"<td>{h.get('prices_collected', 0)}</td></tr>"
                for h in hist[:5]
            )
            last_prices = hist[0].get("prices_collected", 0) if hist else coll.get("prices_collected", 0)
            sections_html.append(
                f"<h4>{title}</h4>"
                f"<p class='metric-desc'>Estado: <code>{_esc(coll.get('status', ''))}</code> · "
                f"edad {coll.get('age_hours', '—')}h · última corrida {last_prices} precios</p>"
                f"<table><tr><th>Inicio</th><th>Tiendas</th><th>Precios</th></tr>{hist_rows}</table>"
            )
        elif sid == "freshness":
            rows = "".join(
                f"<tr><td>{_esc(f.get('store_name', ''))}</td><td>{_esc(str(f.get('last_seen', ''))[:19])}</td></tr>"
                for f in (sec.get("items") or [])[:15]
            )
            sections_html.append(
                f"<h4>{title}</h4><table><tr><th>Tienda</th><th>Último snapshot</th></tr>{rows}</table>"
            )
        elif sid in ("quality_alerts", "outliers"):
            if sid == "outliers":
                rows = "".join(
                    f"<tr class='dirty-row'><td>{_esc((item.get('name') or '')[:35])}</td>"
                    f"<td>{_esc(item.get('store_name', ''))}</td>"
                    f"<td>{_esc(item.get('currency', ''))} {item.get('trigger_price', item.get('price', ''))}</td>"
                    f"<td>n={item.get('group_n', '?')} · {item.get('band', '?')}x</td>"
                    f"<td>{_esc(str(item.get('lo', '')))}–{_esc(str(item.get('hi', '')))}</td>"
                    f"<td class='state-{_esc(item.get('store_health_state', 'unknown'))}'>{_esc(item.get('store_health_state', ''))}</td>"
                    f"<td>{_esc(str(item.get('queried_at', ''))[:16])}</td></tr>"
                    for item in sec.get("items") or []
                )
                sections_html.append(
                    f"<h4 class='dirty-section'>{title}</h4>{intro}"
                    f"<table><tr><th>Producto</th><th>Tienda</th><th>Precio</th><th>Grupo</th><th>Banda</th><th>Scraper</th><th>Captura</th></tr>"
                    f"{rows or '<tr><td colspan=7>sin datos</td></tr>'}</table>"
                )
            else:
                rows = "".join(
                    f"<tr class='dirty-row'><td>{_esc((item.get('name') or item.get('copy', ''))[:40])}</td>"
                    f"<td>{_esc(item.get('store_name', ''))}</td></tr>"
                    for item in sec.get("items") or []
                )
                sections_html.append(
                    f"<h4 class='dirty-section'>{title}</h4>{intro}"
                    f"<table><tr><th>Producto</th><th>Tienda</th></tr>{rows or '<tr><td colspan=2>sin datos</td></tr>'}</table>"
                )
        elif sid == "coverage_gaps":
            gaps = sec.get("gaps") or []
            sections_html.append(
                f"<h4>{title}</h4>{intro}<p class='metric-desc'>{_esc(', '.join(gaps[:15]))}</p>"
            )
    raw = ops.get("raw_analytics") or {}
    meta = raw.get("analytics_meta") or {}
    raw_note = (
        f"<p class='metric-desc'>Analítica cruda: {meta.get('dispersion_crit_count', 0)} grupos crit · "
        f"{meta.get('marketing_crit_count', 0)} listos para copy</p>"
    )
    return f"""
<details class="ops-panel"{open_attr}>
  <summary>[ {_esc(ops.get('title', 'OPS').upper())} ] — {_esc(ops.get('subtitle', ''))}</summary>
  {raw_note}
  {"".join(sections_html)}
</details>
"""


DASHBOARD_CSS = """
body{background:#0a0a0a;color:#b0b0b0;font:12px 'JetBrains Mono',monospace;padding:0;margin:0}
main{padding:12px 16px 24px;max-width:900px}
.global-bar{position:sticky;top:0;z-index:100;background:#0d0d0d;border-bottom:1px solid #222;padding:8px 16px;display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:11px}
.bar-sep{color:#333}
.bar-item{color:#888}
.bar-item b{color:#3cffd0;font-weight:600;text-transform:uppercase}
.bar-glossary-btn{background:none;border:1px solid #333;color:#666;padding:2px 8px;cursor:pointer;font:inherit}
.bar-glossary-btn:hover{color:#3cffd0;border-color:#3cffd0}
.bar-refresh-btn{background:none;border:1px solid #333;color:#ffbd2e;padding:2px 8px;cursor:pointer;font:inherit}
.bar-refresh-btn:hover{color:#3cffd0;border-color:#3cffd0}
.bar-refresh-btn:disabled{color:#444;border-color:#222;cursor:not-allowed}
.glossary-panel{margin:0;border-bottom:1px solid #1a1a1a;background:#0a0a0a;padding:0 16px 12px}
.glossary-panel[open]{padding-top:8px}
.glossary-summary{cursor:pointer;color:#666;font-size:10px;list-style:none}
.glossary-section h3{color:#ffbd2e;font-size:11px;margin:12px 0 4px}
h1{color:#3cffd0;font-size:14px;margin:0 0 4px}
.hero-panel{background:#111;border:1px solid #1a1a1a;border-radius:6px;padding:14px 16px;margin:16px 0;max-width:860px}
.hero-sticky{position:sticky;top:42px;z-index:50}
.hero-title{color:#3cffd0;font-size:16px;margin:0 0 8px;line-height:1.35}
.hero-sub{color:#888;font-size:12px;line-height:1.55;margin:0 0 12px;max-width:720px}
.badges{display:flex;flex-wrap:wrap;gap:10px}
.badge{background:#0a0a0a;border:1px solid #222;border-radius:4px;padding:8px 10px;min-width:140px}
.badge-main{display:block;color:#ccc;font-size:11px}
.badge-sub{display:block;color:#555;font-size:9px;margin-top:4px;text-transform:uppercase}
.system-status{font-size:10px;margin:10px 0 0;color:#666;text-transform:uppercase}
.portada-cards{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.portada-card{flex:1;min-width:200px;background:#111;border:1px solid #1a1a1a;border-radius:4px;padding:12px}
.card-label{color:#666;font-size:10px;text-transform:uppercase;letter-spacing:1px}
.card-value{color:#3cffd0;font-size:14px;margin:6px 0 4px;line-height:1.4}
.card-sub{color:#555;font-size:10px}
.acceso-block{margin:20px 0 24px}
.pillars{display:flex;flex-wrap:wrap;gap:12px;margin:0 0 16px;max-width:860px}
.pillar{flex:1;min-width:180px;background:#111;border:1px solid #1a1a1a;border-radius:4px;padding:10px 12px}
.pillar-head{color:#3cffd0;font-size:13px}
.funnel{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:12px 0;background:#111;border:1px solid #1a1a1a;border-radius:4px;padding:12px}
.funnel-step{text-align:center;min-width:90px}
.funnel-n{display:block;color:#3cffd0;font-size:16px;font-weight:600}
.funnel-arrow{color:#444}
.funnel-flagged .funnel-n{color:#ffbd2e}
.funnel-citable .funnel-n{color:#3cffd0}
.filter-bar{margin:16px 0 8px;padding:8px 12px;background:#111;border:1px solid #1a1a1a;border-radius:4px}
.toggle-clean{color:#888;font-size:11px;cursor:pointer}
.toggle-clean input{margin-right:6px}
.warn{color:#ffbd2e;font-size:10px}
table{border-collapse:collapse;width:100%;max-width:860px;margin-bottom:16px}
th{text-align:left;color:#444;font-size:10px;text-transform:uppercase;padding:4px 8px;border-bottom:1px solid #1a1a1a}
td{padding:3px 8px;border-bottom:1px solid #111;font-size:11px;vertical-align:top}
td.num{text-align:right}
.section{color:#3cffd0;font-size:11px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:2px}
.section-intro{color:#777;font-size:11px;line-height:1.55;margin:0 0 10px;max-width:720px}
.layer-note{color:#555;font-size:10px;margin:0 0 12px;font-style:italic;max-width:720px;line-height:1.45}
.metric-desc{color:#555;font-size:10px;line-height:1.4}
.help-box{background:#111;border:1px solid #1a1a1a;border-radius:4px;padding:10px 12px;margin:0 0 14px;max-width:720px;font-size:10px;color:#666;line-height:1.5}
.ops-panel{margin:24px 0;border:1px solid #1a1a1a;border-radius:4px;padding:8px 12px;background:#0d0d0d}
.ops-panel summary{cursor:pointer;color:#666;font-size:11px;text-transform:uppercase;letter-spacing:1px}
.ops-panel h4{color:#888;font-size:11px;margin:16px 0 6px}
.footer{color:#333;font-size:9px;margin-top:20px;border-top:1px solid #1a1a1a;padding-top:8px}
ul.glossary{color:#666;font-size:10px;line-height:1.55;margin:0 0 16px 18px;max-width:720px}
.state-ok,.state-ok b{color:#3cffd0}
.state-partial,.state-warn,.state-empty{color:#ffbd2e}
.state-stale,.state-dead,.state-crit{color:#ff4444}
.state-running{color:#888}
body.clean-mode .dirty-section{display:none!important}
body:not(.clean-mode) .dirty-collapsed{display:block}
"""


DASHBOARD_JS = """
(function(){
  var toggle = document.getElementById('clean-toggle');
  if (toggle) {
    function apply(){ document.body.classList.toggle('clean-mode', toggle.checked); }
    toggle.addEventListener('change', apply);
    apply();
  }
  var gBtn = document.getElementById('glossary-toggle');
  var gPanel = document.getElementById('glossary-panel');
  if (gBtn && gPanel) {
    gBtn.addEventListener('click', function(){
      gPanel.open = !gPanel.open;
      gBtn.setAttribute('aria-expanded', gPanel.open);
    });
  }
  var adminToken = new URLSearchParams(window.location.search).get('token');
  var rBtn = document.getElementById('refresh-trigger');
  if (rBtn) {
    if (!adminToken) {
      rBtn.style.display = 'none';
    } else {
      rBtn.title = 'Refresh autorizado — disparar ciclo del collector';
      rBtn.addEventListener('click', function(){
        var orig = rBtn.textContent;
        rBtn.disabled = true;
        rBtn.textContent = '\u23f3 Solicitando...';
        fetch('/dashboard/collector/trigger', {
          method: 'POST',
          headers: {'Authorization': 'Bearer ' + adminToken}
        })
          .then(function(r){
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
          })
          .then(function(data){
            if (data.triggered) {
              rBtn.textContent = '\u2713 Trigger enviado';
              setTimeout(function(){ rBtn.textContent = orig; rBtn.disabled = false; }, 4000);
            } else {
              rBtn.textContent = '\u23f3 Ya pendiente';
              setTimeout(function(){ rBtn.textContent = orig; rBtn.disabled = false; }, 3000);
            }
          })
          .catch(function(){
            rBtn.textContent = '\u2717 No autorizado';
            setTimeout(function(){ rBtn.textContent = orig; rBtn.disabled = false; }, 3000);
          });
      });
    }
  }
})();
"""


def render_dashboard_html(data: dict) -> str:
    """Render full dashboard page from /dashboard/data payload."""
    view = data.get("dashboard_view") or {}
    glossary = data.get("metric_glossary") or {}
    footer = _esc(view.get("footer_stamp") or f"Actualizado {data.get('generated_at', '')[:16]} UTC")

    body = (
        _render_global_bar(view, glossary)
        + _render_portada(view)
        + _render_quality_funnel(view)
        + _render_hero(view)
        + _render_spreads(view)
        + _render_canasta(view)
        + _render_inflation(view)
        + _render_enrichment(view)
        + _render_exploration(view)
        + _render_moat(view)
        + _render_ops(view)
        + f'<p class="footer">{footer} · spec {view.get("spec_version", "?")} · cli-market.dev · v2026-06-01-refresh</p>'
    )

    return f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CLI Market // Data Moat</title>
<style>{DASHBOARD_CSS}</style>
</head><body class="clean-mode">
<main>
{body}
</main>
<script>{DASHBOARD_JS}</script>
</body></html>"""
