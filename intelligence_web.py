"""Public HTML views for Intelligence Terminal — homepage embed + landing."""

from __future__ import annotations

import html
from typing import Any


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def pulse_view_model(pulse: dict[str, Any]) -> dict[str, Any]:
    """Slim payload for homepage widgets (no brief blob)."""
    executive = pulse.get("executive") or {}
    kpis = pulse.get("kpis") or {}
    moat = pulse.get("moat") or {}
    return {
        "country": pulse.get("country"),
        "week": pulse.get("week"),
        "headline": pulse.get("headline"),
        "title": pulse.get("title"),
        "generated_at": pulse.get("generated_at"),
        "publishable": pulse.get("publishable"),
        "executive_highlights": pulse.get("executive_highlights") or [],
        "kpis": kpis,
        "moat": {
            "total_indexed": moat.get("total_indexed"),
            "snapshots_24h": moat.get("snapshots_24h"),
            "coverage_7d_pct": moat.get("coverage_7d_pct"),
        },
        "largest_anomaly": executive.get("largest_anomaly"),
    }


def render_commerce_pulse_page(
    pulse: dict[str, Any],
    *,
    embed: bool = False,
    api_base: str = "https://cli-market-api.fly.dev",
) -> str:
    """Dark-terminal HTML for /intelligence and /embed/commerce-pulse."""
    vm = pulse_view_model(pulse)
    cc = _esc(vm.get("country") or "PE")
    week = _esc(vm.get("week") or "")
    headline = _esc(vm.get("headline") or "")
    highlights = vm.get("executive_highlights") or []
    kpis = vm.get("kpis") or {}
    moat = vm.get("moat") or {}
    anomaly = vm.get("largest_anomaly") or {}

    hl_html = "".join(f"<li>{_esc(h)}</li>" for h in highlights[:6]) or "<li class='dim'>Sin highlights — refresh en curso.</li>"

    def _kpi(label: str, val: object, fmt: str = "") -> str:
        if val is None:
            disp = "—"
        elif fmt == "pct_signed" and isinstance(val, (int, float)):
            disp = f"{float(val):+.1f}%"
        elif fmt == "pct":
            disp = f"{float(val):.1f}%"
        elif fmt == "int" and isinstance(val, (int, float)):
            disp = f"{int(val):,}"
        else:
            disp = _esc(val)
        return f"<div class='kpi'><span class='kpi-label'>{label}</span><span class='kpi-value'>{disp}</span></div>"

    kpi_grid = "\n".join(
        [
            _kpi("Inflación retail (7d)", kpis.get("inflation_pct"), "pct_signed"),
            _kpi("PVI · dispersión", kpis.get("pvi"), "pct"),
            _kpi("BAI · canasta", kpis.get("bai")),
            _kpi("PDI · promos", kpis.get("pdi")),
            _kpi("RCS · fairness", kpis.get("rcs")),
        ]
    )

    moat_line = ""
    if moat.get("total_indexed"):
        moat_line = (
            f"<p class='moat'>"
            f"{int(moat.get('total_indexed') or 0):,} precios indexados · "
            f"{int(moat.get('snapshots_24h') or 0):,} refresh 24h · "
            f"cobertura 7d {float(moat.get('coverage_7d_pct') or 0):.1f}%"
            f"</p>"
        )

    anomaly_html = ""
    if anomaly.get("subcategory"):
        anomaly_html = (
            f"<p class='anomaly'>Mayor anomalía: "
            f"<strong>{_esc(anomaly.get('subcategory'))}</strong> "
            f"{float(anomaly.get('delta_pct', 0)):+.1f}% vs mediana</p>"
        )

    countries = ["PE", "MX", "CL", "CO", "AR", "BR"]
    nav = "".join(
        f"<a href='?country={c}' class='cc{' active' if c == vm.get('country') else ''}'>{c}</a>"
        for c in countries
    )

    embed_class = " embed" if embed else ""
    footer = "" if embed else f"""
<footer>
  <a href="{_esc(api_base)}/docs">API</a> ·
  <a href="https://cli-market.dev">cli-market.dev</a> ·
  <code>market brief -c {cc}</code> ·
  <code>market pulse -c {cc}</code>
</footer>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CLI Market — This Week in LatAm Commerce · {cc}</title>
  <style>
    :root {{ --bg:#0a0c0f; --panel:#111418; --border:#1e2a22; --accent:#00ff88; --text:#e8e8e8; --dim:#7a8a80; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:ui-monospace,Menlo,Consolas,monospace; background:var(--bg); color:var(--text); }}
    body{embed_class} {{ padding:12px; }}
    .wrap {{ max-width:920px; margin:0 auto; padding:24px 20px 32px; }}
    h1 {{ font-size:1.1rem; font-weight:600; color:var(--accent); margin:0 0 4px; letter-spacing:.04em; }}
    .sub {{ color:var(--dim); font-size:.85rem; margin-bottom:16px; }}
    .headline {{ font-size:1rem; margin:0 0 18px; line-height:1.5; }}
    .nav {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:20px; }}
    .nav a {{ color:var(--dim); text-decoration:none; border:1px solid var(--border); padding:4px 10px; border-radius:4px; font-size:.75rem; }}
    .nav a.active, .nav a:hover {{ color:var(--accent); border-color:var(--accent); }}
    section {{ background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:16px 18px; margin-bottom:16px; }}
    section h2 {{ font-size:.75rem; text-transform:uppercase; letter-spacing:.12em; color:var(--accent); margin:0 0 12px; }}
    ul {{ margin:0; padding-left:18px; line-height:1.55; font-size:.88rem; }}
    li.dim {{ color:var(--dim); }}
    .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }}
    .kpi {{ border:1px solid var(--border); border-radius:6px; padding:10px; }}
    .kpi-label {{ display:block; font-size:.65rem; color:var(--dim); text-transform:uppercase; letter-spacing:.06em; }}
    .kpi-value {{ display:block; font-size:1.05rem; margin-top:4px; color:var(--text); }}
    .moat, .anomaly {{ font-size:.8rem; color:var(--dim); margin:12px 0 0; }}
    .anomaly strong {{ color:var(--text); }}
    footer {{ margin-top:24px; font-size:.75rem; color:var(--dim); }}
    footer a {{ color:var(--accent); }}
    code {{ background:#0d120f; padding:2px 6px; border-radius:3px; }}
  </style>
</head>
<body class="{embed_class.strip()}">
  <div class="wrap">
    <h1>THIS WEEK IN LATAM COMMERCE</h1>
    <p class="sub">Agentic Commerce Pulse · {cc} · {week}</p>
    <nav class="nav" aria-label="Country">{nav}</nav>
    <p class="headline">{headline}</p>
    {anomaly_html}
    <section>
      <h2>Executive Highlights</h2>
      <ul>{hl_html}</ul>
    </section>
    <section>
      <h2>KPIs</h2>
      <div class="kpis">{kpi_grid}</div>
      {moat_line}
    </section>
    {footer}
  </div>
</body>
</html>"""


def embed_snippet_for_homepage(api_base: str = "https://cli-market-api.fly.dev") -> str:
    """Copy-paste snippet for cli-market.dev homepage maintainers."""
    return f"""<!-- CLI Market — This Week in LatAm Commerce embed -->
<section id="cli-market-intelligence" style="margin:48px auto;max-width:960px;padding:0 16px">
  <h2 style="font-family:monospace;color:#00ff88">This Week in LatAm Commerce</h2>
  <iframe
    src="{api_base}/embed/commerce-pulse?country=PE"
    title="CLI Market Agentic Commerce Pulse"
    style="width:100%;min-height:520px;border:1px solid #1e2a22;border-radius:12px;background:#0a0c0f"
    loading="lazy"
  ></iframe>
  <p style="font-size:12px;color:#666;margin-top:8px">
    Live data · <a href="{api_base}/intelligence">full report</a> ·
    <code>market pulse -c PE</code>
  </p>
</section>"""
