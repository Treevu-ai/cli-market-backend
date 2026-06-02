"""Render-ready dashboard content blocks (Spanish product copy + data bindings).

Maps raw /dashboard/data fields to the six-block content spec. Consumers (HTML,
React, agents) should prefer `dashboard_view` over re-deriving copy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from data_v1_service import intelligence_acceso_examples

SPEC_VERSION = "1.2"
CANASTA_TOTAL_ITEMS = 10
CANASTA_PARTIAL_THRESHOLD = 6  # 60% — below this, totals are not comparable
COLLECTOR_INTERVAL_HOURS = 8

TOOLTIPS: dict[str, str] = {
    "freshness": "Qué porcentaje de precios se actualizó en las últimas 24 horas.",
    "coverage": "Qué parte del catálogo activo tiene un precio reciente.",
    "spread": "Cuántas veces más caro es el mismo producto entre la tienda más cara y la más barata.",
    "snapshot": "Una foto del precio en un momento dado.",
}


def _fmt_int(n: int | float | None) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", ".")


def _fmt_age_es(hours: float | None) -> str:
    if hours is None:
        return "sin datos recientes"
    if hours < 1:
        mins = max(1, int(hours * 60))
        return f"hace {mins} {'minuto' if mins == 1 else 'minutos'}"
    if hours < 48:
        h = int(round(hours))
        return f"hace {h} {'hora' if h == 1 else 'horas'}"
    days = int(round(hours / 24))
    return f"hace {days} {'día' if days == 1 else 'días'}"


def _global_system_status(
    *,
    fresh_24h_pct: float,
    moat_age_h: float | None,
    collector_status: str,
    snapshots_24h: int,
    total_indexed: int,
    freshness_label: str,
) -> dict:
    if moat_age_h is None or total_indexed == 0:
        return {"state": "unknown", "label": "partial — sin datos recientes"}
    if moat_age_h >= 24 or (snapshots_24h == 0 and total_indexed > 0):
        return {"state": "stale", "label": "stale — datos envejeciendo"}
    if (
        fresh_24h_pct >= 80
        and moat_age_h < 8
        and collector_status == "ok"
        and freshness_label == "fresh"
    ):
        return {"state": "ok", "label": "ok — sistema al día"}
    return {"state": "partial", "label": "partial — actualización incompleta"}


def _inflation_measuring(inflation: list[dict], top_risers: list, top_fallers: list) -> bool:
    if not inflation:
        return True
    if all(float(r.get("avg_before") or 0) == 0 for r in inflation):
        return True
    if not top_risers and not top_fallers:
        return True
    return False


def _seed_display_name(seed: str, sample_name: str) -> str:
    if sample_name:
        return sample_name[:50].strip()
    return seed.replace("_", " ").capitalize()


def build_dashboard_view_model(data: dict) -> dict:
    """Build six content blocks + tooltips from a /dashboard/data payload."""
    kpis = data.get("kpis") or {}
    collector = data.get("collector") or {}
    generated_at = data.get("generated_at") or datetime.now(timezone.utc).isoformat()

    total_indexed = int(kpis.get("total_indexed") or 0)
    stores_indexed = int(kpis.get("stores_indexed") or 0)
    fresh_24h_pct = float(kpis.get("fresh_24h_pct") or 0)
    moat_age_h = kpis.get("moat_age_hours")
    snapshots_24h = int(kpis.get("snapshots_24h") or 0)
    collector_status = str(collector.get("status") or "unknown")

    freshness_layer = next(
        (l for l in (data.get("moat_guide") or {}).get("layers", []) if l.get("id") == "freshness"),
        {},
    )
    freshness_label = str((freshness_layer.get("metrics") or {}).get("status") or "unknown")

    countries = data.get("by_country") or []
    country_count = len(countries)

    system_status = _global_system_status(
        fresh_24h_pct=fresh_24h_pct,
        moat_age_h=moat_age_h,
        collector_status=collector_status,
        snapshots_24h=snapshots_24h,
        total_indexed=total_indexed,
        freshness_label=freshness_label,
    )

    try:
        gen_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        gen_dt = datetime.now(timezone.utc)

    quality = data.get("quality_funnel") or {}
    q_clean = int(quality.get("clean") or 0)
    q_flagged = int(quality.get("flagged") or 0)
    q_citable = int(quality.get("citable") or 0)

    # ── Global bar (Capa 0) ──────────────────────────────────────────────────
    block_global_bar = {
        "id": "global_bar",
        "collector_status": collector_status,
        "collector_label": collector_status,
        "fresh_24h_pct": fresh_24h_pct,
        "utc_time": gen_dt.strftime("%H:%M"),
        "source": "collector.status + kpis.fresh_24h_pct + generated_at",
    }

    # ── Portada (Capa 1) ─────────────────────────────────────────────────────
    block_portada = {
        "id": "portada",
        "cards": [
            {
                "label": "INVENTORY",
                "headline": (
                    f"{_fmt_int(total_indexed)} precios · "
                    f"{stores_indexed} tiendas · {country_count} países"
                ),
                "subline": "kpis.total_indexed, stores_indexed, by_country",
                "source": "kpis + by_country",
            },
            {
                "label": "FRESHNESS",
                "headline": (
                    f"{fresh_24h_pct:.1f}% <24h · último snapshot {_fmt_age_es(moat_age_h)}"
                ),
                "subline": "kpis.fresh_24h_pct, moat_age_hours",
                "source": "kpis",
            },
            {
                "label": "QUALITY",
                "headline": f"{_fmt_int(q_clean)} clean / {_fmt_int(q_flagged)} flagged / {q_citable} citable",
                "subline": "quality_funnel",
                "source": "quality_funnel",
            },
        ],
        # Public dashboard omits curl/endpoints — see docs/ops/dashboard-api-access.md
        "acceso": intelligence_acceso_examples(),
    }

    # ── Block 1 — Hero ───────────────────────────────────────────────────────
    block_hero = {
        "id": "hero",
        "state": system_status["state"],
        "title": "CLI Market · Precios reales, verificados todos los días",
        "subtitle": (
            f"Seguimos los precios de {_fmt_int(total_indexed)} productos en "
            f"{stores_indexed} tiendas de {country_count} países. "
            f"Último precio capturado: {_fmt_age_es(moat_age_h)}."
        ),
        "trust_badges": [
            {
                "label": f"{int(round(fresh_24h_pct))}% de los precios son de hoy",
                "sublabel": "Frescura 24h",
                "tooltip": TOOLTIPS["freshness"],
                "value": fresh_24h_pct,
                "source": "kpis.fresh_24h_pct",
            },
            {
                "label": f"El dato más reciente {_fmt_age_es(moat_age_h)}",
                "sublabel": "Antigüedad del dato",
                "tooltip": "Tiempo desde el último precio guardado en cualquier tienda.",
                "value": moat_age_h,
                "source": "kpis.moat_age_hours",
            },
            {
                "label": system_status["label"],
                "sublabel": "Estado global",
                "tooltip": "Resumen de frescura del inventario y del collector.",
                "value": system_status["state"],
                "source": "derived: fresh_24h_pct + moat_age_hours + collector.status",
            },
        ],
        "system_status": system_status,
        "sticky": True,
    }

    # ── Block 2 — Moat narrative ─────────────────────────────────────────────
    block_moat = {
        "id": "moat_narrative",
        "state": "ok",
        "title": "Por qué estos datos son difíciles de copiar",
        "intro": (
            "Cada día sumamos miles de precios verificados de tiendas reales. "
            "Ese inventario no se improvisa: se construye día a día y cubre tiendas de varios países a la vez. "
            "Mientras más tiempo pasa, más profundo es el foso que nos separa de cualquier competidor que empiece desde cero."
        ),
        "pillars": [
            {
                "title": "Volumen acumulado",
                "headline": f"{_fmt_int(total_indexed)} precios ya guardados",
                "microcopy": "Disponibles aunque el sistema esté en pausa.",
                "source": "kpis.total_indexed",
            },
            {
                "title": "Cobertura amplia",
                "headline": f"{stores_indexed} tiendas en {country_count} países",
                "microcopy": "Comparación entre cadenas, no una sola tienda.",
                "source": "kpis.stores_indexed + len(by_country)",
            },
            {
                "title": "Frescura diaria",
                "headline": f"{int(round(fresh_24h_pct))}% renovado en 24h",
                "microcopy": "Precios consultados de nuevo en el último día.",
                "source": "kpis.fresh_24h_pct",
            },
        ],
        "growth_chart": {
            "state": "live" if data.get("inventory_daily") else "empty",
            "label": "El foso se hace más profundo cada día",
            "total_snapshots": int(data.get("total_snapshots_all") or total_indexed),
            "moat_start": data.get("moat_start"),
            "avg_daily_7d": float(data.get("avg_daily_snapshots_7d") or 0),
            "collector_interval_h": int(data.get("collector", {}).get("interval_hours") or 8),
            "days_tracked": len(data.get("inventory_daily") or []),
            "source": "inventory_daily[] (daily snapshot counts)",
        },
    }

    # ── Block 3 — Canasta ────────────────────────────────────────────────────
    canasta_rows: list[dict] = []
    for row in data.get("canasta_basica") or []:
        items = int(row.get("items") or 0)
        completeness_pct = items * 10
        partial = items < CANASTA_PARTIAL_THRESHOLD
        canasta_rows.append({
            "store_name": row.get("store_name"),
            "currency": row.get("currency"),
            "total": row.get("total"),
            "total_label": f"{row.get('currency', '')} {float(row.get('total') or 0):,.2f}",
            "items_found": items,
            "items_total": CANASTA_TOTAL_ITEMS,
            "completeness_label": f"{items} de {CANASTA_TOTAL_ITEMS} productos",
            "completeness_pct": completeness_pct,
            "comparable": not partial,
            "warning": "Comparación parcial — faltan productos" if partial else None,
            "source": "canasta_basica",
        })

    canasta_rows.sort(
        key=lambda r: (-r["items_found"], r["total"] if r["comparable"] else float("inf")),
    )

    cheapest_rows = []
    for row in data.get("cheapest_by_line") or []:
        line_name = row.get("line_name") or "?"
        store_name = row.get("store_name") or "?"
        cheapest_rows.append({
            "line": line_name,
            "store": store_name,
            "copy": f"{line_name} → más barato en {store_name}",
            "microcopy": "Promedio más bajo de la categoría.",
            "avg_price": row.get("avg_price"),
            "currency": row.get("currency"),
            "source": "cheapest_by_line",
        })

    block_canasta = {
        "id": "canasta",
        "state": "ok" if canasta_rows else "empty",
        "title": "La canasta básica, tienda por tienda",
        "subtitle": "Cuánto costaría una lista de 10 productos esenciales en cada tienda.",
        "quality_note": (
            "Solo comparamos totales entre tiendas con una completitud similar. "
            "Una canasta de 3 de 10 no es comparable con una de 8 de 10."
        ),
        "stores": canasta_rows,
        "cheapest_by_line": {
            "title": "Dónde conviene comprar cada cosa",
            "items": cheapest_rows,
        },
        "sort_rule": "completeness desc, then total asc (if completeness >= 60%)",
    }

    # ── Block 4 — Verified spreads (marketing only) ──────────────────────────
    spread_items = []
    for row in data.get("marketing_spreads") or []:
        ratio = float(row.get("spread_ratio") or 0)
        name = _seed_display_name(str(row.get("seed") or ""), str(row.get("sample_name") or ""))
        stores_n = int(row.get("stores") or 0)
        spread_items.append({
            "name": name,
            "spread_ratio": ratio,
            "spread_label": f"{ratio:.1f} veces",
            "copy": f"{name} cuesta hasta {ratio:.1f} veces más en una tienda que en otra",
            "stores_compared": stores_n,
            "stores_label": f"comparado en {stores_n} tiendas",
            "currency": row.get("currency"),
            "sample_name": row.get("sample_name"),
            "source": "marketing_spreads",
        })

    block_spreads = {
        "id": "price_spreads",
        "state": "ok" if spread_items else "empty",
        "title": "El mismo producto, precios muy distintos",
        "subtitle": "Diferencias verificadas entre tiendas para el mismo tipo de producto.",
        "quality_note": (
            "Excluimos automáticamente precios sospechosos para evitar comparaciones falsas."
        ),
        "items": spread_items,
        "data_rule": "marketing_spreads ONLY (not dispersion)",
        "threshold_note": (
            f"Umbral canasta ≥{data.get('analytics_meta', {}).get('marketing_canasta_min_spread', 2.5)}x · "
            "farmacia ≥10x · pack 1kg/1L · misma unidad · 2+ tiendas"
        ),
    }

    # ── Block 5 — Inflation ──────────────────────────────────────────────────
    inflation = data.get("inflation") or []
    measuring = _inflation_measuring(inflation, data.get("top_risers") or [], data.get("top_fallers") or [])
    eta = gen_dt + timedelta(hours=COLLECTOR_INTERVAL_HOURS * 2)

    inflation_rows = []
    if not measuring:
        for row in inflation:
            delta = float(row.get("delta_pct") or 0)
            inflation_rows.append({
                "line": row.get("line"),
                "currency": row.get("currency"),
                "delta_pct": delta,
                "copy": f"{row.get('line')} ({row.get('currency')}): {'+' if delta > 0 else ''}{delta:.1f}% esta semana",
                "direction": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
                "source": "inflation",
            })

    block_inflation = {
        "id": "inflation",
        "state": "measuring" if measuring else "ok",
        "title": "Cómo se mueven los precios",
        "term_tooltip": "Comparamos el precio promedio de esta semana contra el de la semana pasada.",
        "measuring": measuring,
        "measuring_copy": (
            "Midiendo. Necesitamos una segunda captura para comparar. "
            f"Primera lectura de inflación disponible aproximadamente el {eta.strftime('%Y-%m-%d')}."
        ),
        "rows": inflation_rows,
        "movers": {
            "risers": data.get("top_risers") or [],
            "fallers": data.get("top_fallers") or [],
        },
    }

    # ── Block 5b — All indicators (internal + external + enrichment) ──────────
    indicators_meta = data.get("indicators") or {}
    # Merge latest (global recent) + enrichment (per-country) — dedup by key+country
    latest_raw = indicators_meta.get("latest") or []
    enrichment_only = indicators_meta.get("enrichment") or []
    seen_pairs: set[tuple[str, str]] = set()
    enrichment_raw: list[dict] = []
    for item in latest_raw + enrichment_only:
        pair = (str(item.get("key") or ""), str(item.get("country") or ""))
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            enrichment_raw.append(item)
    tier2_keys = {
        "imf_inflation_yoy", "eurostat_food_hicp_yoy", "eurostat_headline_hicp_yoy",
        "bcb_food_inflation_mom", "bcb_headline_inflation_mom", "macro_unemployment_rate",
        "imf_wb_cpi_gap", "imf_gdp_growth_yoy", "imf_epi_inflation_yoy", "wb_gdp_growth_yoy",
    }
    enrichment_items = []
    for row in enrichment_raw:
        key = str(row.get("key") or "")
        val = row.get("value")
        unit = row.get("unit") or ""
        name = row.get("name") or key.replace("_", " ")
        cc = row.get("country") or "—"
        tier = "tier2" if key in tier2_keys else "tier1"
        if val is None:
            val_label = "—"
        else:
            try:
                fval = float(val)
                if unit in ("pct", "%"):
                    val_label = f"{fval:+.2f}%"
                else:
                    val_label = f"{fval:.2f}{(' ' + unit) if unit else ''}"
            except (TypeError, ValueError):
                val_label = str(val)
        enrichment_items.append({
            "key": key,
            "name": name,
            "country": cc,
            "value_label": val_label,
            "source": row.get("source") or "",
            "tier": tier,
            "copy": f"{name} ({cc}): {val_label}",
            "recorded_at": row.get("recorded_at"),
        })

    block_enrichment = {
        "id": "enrichment",
        "state": "ok" if enrichment_items else "empty",
        "title": "Indicadores de mercado (34 definidos · 8 países · 7 APIs públicas)",
        "subtitle": (
            "Indicadores derivados de fuentes abiertas — Open Food Facts, Wikimedia, "
            "Open-Meteo, World Bank, IMF, Eurostat y BCB — combinados con el moat de precios."
        ),
        "indicator_count": len(enrichment_items) or None,
        "docs": indicators_meta.get("docs") or "docs/DATA-MOAT-INDICATORS.md",
        "endpoints": [
            "GET /v1/intel/indicators",
            "GET /v1/intel/enrichment",
            "GET /v1/intel/enrichment/subcategories",
            "POST /v1/intel/enrichment/refresh",
        ],
        "items": enrichment_items,
    }

    # ── Block 6 — Ops (collapsed) ────────────────────────────────────────────
    quality_alerts = [
        {
            "name": d.get("name"),
            "store_name": d.get("store_name"),
            "discount_pct": d.get("discount_pct"),
            "confidence": d.get("confidence", "suspect"),
            "copy": f"{(d.get('name') or '')[:40]} ({d.get('store_name')}) — posible error de scraping",
            "source": "suspect_discounts",
        }
        for d in (data.get("suspect_discounts") or [])
    ]

    matrix = data.get("line_country_matrix") or []
    gaps = []
    if matrix:
        lines = list(dict.fromkeys(r["line"] for r in matrix))
        countries_m = sorted(set(r["country"] for r in matrix))
        lookup = {f"{r['line']}|{r['country']}": r["stores"] for r in matrix}
        for ln in lines:
            for c in countries_m:
                if lookup.get(f"{ln}|{c}", 0) == 0:
                    gaps.append(f"{ln}×{c}")

    store_health_items = data.get("store_health") or []
    dead_stores = [h for h in store_health_items if float(h.get("success_pct") or 0) < 30]
    ok_stores = [h for h in store_health_items if float(h.get("success_pct") or 0) >= 80]
    scraping_summary = (
        f"{len(dead_stores)} DEAD · {len(ok_stores)}/{len(store_health_items)} OK"
        if store_health_items
        else "sin datos de salud"
    )

    flagged_samples = []
    for d in (data.get("suspect_discounts") or [])[:8]:
        flagged_samples.append({
            "name": d.get("name"),
            "store_name": d.get("store_name"),
            "reason": f"discount>={int(d.get('discount_pct') or 90)}%",
        })
    for o in (data.get("outliers") or [])[:5]:
        group_n = o.get("group_n", "?")
        band = o.get("band", "?")
        flagged_samples.append({
            "name": o.get("name"),
            "store_name": o.get("store_name"),
            "reason": f"median_outlier_{band}x (n={group_n}, {o.get('deviation', '?')})",
        })

    block_quality_funnel = {
        "id": "quality_funnel",
        "captured": int(quality.get("captured") or total_indexed),
        "flagged": q_flagged,
        "clean": q_clean,
        "citable": q_citable,
        "non_normalizable_names": int(quality.get("non_normalizable_names") or 0),
        "confidence_dist": quality.get("confidence_dist") or {},
        "filters": quality.get("filters") or [],
        "flagged_samples": flagged_samples,
        "scraping_health": {
            "summary": scraping_summary,
            "stores": [
                {
                    "store": h.get("store"),
                    "success_pct": float(h.get("success_pct") or 0),
                    "consecutive_failures": int(h.get("consecutive_failures") or 0),
                    "state": (
                        "dead" if float(h.get("success_pct") or 0) < 30
                        else ("ok" if float(h.get("success_pct") or 0) >= 80 else "partial")
                    ),
                    "coverage_7d_pct": float(h.get("coverage_7d_pct") or 0),
                }
                for h in store_health_items[:15]
            ],
        },
        "source": "quality_funnel + store_health + suspect_discounts + outliers",
    }

    dispersion = data.get("dispersion") or []
    crit_dispersion = [d for d in dispersion if d.get("status") == "crit"][:12]

    block_exploration = {
        "id": "exploration",
        "clean_default": True,
        "by_line_currency": [
            {
                "line_name": r.get("line_name") or r.get("line"),
                "currency": r.get("currency"),
                "count": int(r.get("count") or 0),
                "p25": float(r.get("p25") or 0),
                "p50": float(r.get("p50") or 0),
                "p75": float(r.get("p75") or 0),
                "min_price": float(r.get("min_price") or 0),
                "max_price": float(r.get("max_price") or 0),
                "normalizable_pct": float(r.get("normalizable_pct") or 0),
                "normalized_unit": r.get("normalized_unit"),
            }
            for r in data.get("by_line_currency") or []
        ],
        "dispersion_sample": crit_dispersion,
        "source": "by_line_currency + dispersion (crit, collapsed when clean toggle ON)",
    }

    block_ops = {
        "id": "ops",
        "state": "ok",
        "collapsed_default": True,
        "title": "Salud del sistema",
        "subtitle": "Información técnica para el equipo de datos.",
        "sections": [
            {
                "id": "store_health",
                "title": "Estado de tiendas",
                "source": "store_health",
                "items": data.get("store_health") or [],
            },
            {
                "id": "collector",
                "title": "Fiabilidad del collector",
                "source": "collector + collector_history",
                "collector": collector,
                "history": data.get("collector_history") or [],
            },
            {
                "id": "freshness",
                "title": "Frescura por tienda",
                "source": "freshness",
                "items": data.get("freshness") or [],
            },
            {
                "id": "quality_alerts",
                "title": "Alertas de calidad",
                "intro": "Precios detectados como probables errores y excluidos de las comparaciones.",
                "source": "suspect_discounts (discount_pct >= 90)",
                "items": quality_alerts,
            },
            {
                "id": "outliers",
                "title": "Valores atípicos",
                "source": "outliers",
                "items": data.get("outliers") or [],
            },
            {
                "id": "coverage_gaps",
                "title": "Brechas de cobertura",
                "intro": "Combinaciones país × categoría que aún no cubrimos.",
                "source": "line_country_matrix",
                "gaps": gaps[:20],
            },
        ],
        "raw_analytics": {
            "dispersion": data.get("dispersion") or [],
            "canasta_spreads": data.get("canasta_spreads") or [],
            "analytics_meta": data.get("analytics_meta") or {},
        },
    }

    gen_display = gen_dt.strftime("%Y-%m-%d a las %H:%M UTC")
    footer_stamp = (
        f"Actualizado el {gen_display} · Frescura {int(round(fresh_24h_pct))}%"
    )

    return {
        "spec_version": SPEC_VERSION,
        "locale": "es",
        "generated_at": generated_at,
        "footer_stamp": footer_stamp,
        "tooltips": TOOLTIPS,
        "blocks": {
            "global_bar": block_global_bar,
            "portada": block_portada,
            "quality_funnel": block_quality_funnel,
            "hero": block_hero,
            "moat_narrative": block_moat,
            "canasta": block_canasta,
            "price_spreads": block_spreads,
            "inflation": block_inflation,
            "enrichment": block_enrichment,
            "exploration": block_exploration,
            "ops": block_ops,
        },
        "reading_order": [
            "global_bar",
            "portada",
            "quality_funnel",
            "hero",
            "price_spreads",
            "canasta",
            "inflation",
            "enrichment",
            "exploration",
            "moat_narrative",
            "ops",
        ],
    }
