"""Dashboard HTML renderer with semantic moat section."""

from __future__ import annotations

import html

from dashboard_renderer import render_dashboard_html as _render_core


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _render_semantic_moat(view: dict) -> str:
    block = (view.get("blocks") or {}).get("semantic_moat") or {}
    if not block:
        return ""

    cards_html = "".join(
        f"""<div class="portada-card semantic-card">
  <div class="card-label">{_esc(c.get('label', ''))}</div>
  <div class="card-value">{_esc(c.get('headline', ''))}</div>
  <div class="card-sub">{_esc(c.get('subline', ''))}</div>
</div>"""
        for c in block.get("cards") or []
    )

    pct = float(block.get("progress_pct") or 0)
    bar_w = max(0, min(100, pct))

    return f"""
<section class="semantic-layer">
  <div class="section">[ {_esc(block.get('title', 'SEMANTIC MOAT').upper())} ]</div>
  <p class="section-intro">{_esc(block.get('intro', ''))}</p>
  <div class="semantic-progress">
    <div class="semantic-progress-bar" style="width:{bar_w:.1f}%"></div>
    <span class="semantic-progress-label">{pct:.1f}% snapshots linked</span>
  </div>
  <div class="portada-cards semantic-cards">{cards_html}</div>
  <p class="layer-note">API: GET /index/stats · POST /index/resolve · MCP index_resolve</p>
</section>
"""


SEMANTIC_CSS = """
.semantic-layer{margin:20px 0;padding:12px;border:1px solid #1a3a2a;border-radius:4px;background:#0d120f}
.semantic-cards .semantic-card{border-color:#1f4a35}
.semantic-progress{position:relative;height:18px;background:#111;border:1px solid #222;margin:10px 0 14px;border-radius:2px;overflow:hidden}
.semantic-progress-bar{height:100%;background:linear-gradient(90deg,#1a5c40,#3cffd0);transition:width .3s}
.semantic-progress-label{position:absolute;right:8px;top:2px;font-size:10px;color:#8aa}
"""


def render_dashboard_html(data: dict) -> str:
    """Core dashboard HTML plus semantic moat section."""
    view = data.get("dashboard_view") or {}
    html_out = _render_core(data)
    semantic = _render_semantic_moat(view)
    if not semantic:
        return html_out

    if "</style>" in html_out:
        html_out = html_out.replace("</style>", f"{SEMANTIC_CSS}</style>", 1)

    marker = '<div class="section">[ POR QUÉ'
    if marker in html_out:
        return html_out.replace(marker, semantic + marker, 1)
    if "</main>" in html_out:
        return html_out.replace("</main>", semantic + "</main>", 1)
    return html_out + semantic