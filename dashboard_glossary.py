"""Plain-language metric glossary for the Data Moat dashboard.

Written for readers with no finance or economics background.
"""

from __future__ import annotations

MetricHelp = dict[str, str]  # label, description, [example]

SECTION = dict[str, str | dict[str, MetricHelp]]


def _m(label: str, description: str, example: str = "") -> MetricHelp:
    out: MetricHelp = {"label": label, "description": description}
    if example:
        out["example"] = example
    return out


# ── Per-metric keys used in moat_guide.layers[].metrics ───────────────────────

LAYER_METRICS: dict[str, dict[str, MetricHelp]] = {
    "inventory": {
        "total_indexed": _m(
            "Precios guardados",
            "Cuántas veces registramos un precio de un producto en una tienda. "
            "Es como contar entradas en un cuaderno: un mismo producto en 3 tiendas suma 3.",
            "12.000 = doce mil registros de precio, no necesariamente 12.000 productos distintos.",
        ),
        "unique_products": _m(
            "Productos distintos",
            "Combinaciones únicas de producto + tienda. Un arroz en Wong y el mismo arroz en Metro cuentan como 2.",
        ),
        "stores_indexed": _m(
            "Tiendas con datos",
            "Cuántas tiendas distintas ya tienen al menos un precio guardado en el sistema.",
        ),
    },
    "freshness": {
        "snapshots_24h": _m(
            "Actualizaciones últimas 24 h",
            "Precios que se volvieron a consultar hoy o ayer. Si es 0, los datos existen pero pueden estar desactualizados.",
        ),
        "stores_fresh_24h": _m(
            "Tiendas actualizadas hoy",
            "Tiendas de las que obtuvimos al menos un precio nuevo en las últimas 24 horas.",
        ),
        "fresh_24h_pct": _m(
            "% de tiendas frescas",
            "Porcentaje del catálogo activo que recibió datos nuevos en 24 h. "
            "100 % = todas las tiendas se revisaron recientemente.",
        ),
        "moat_age_hours": _m(
            "Horas desde el último dato",
            "Cuánto tiempo pasó desde que guardamos el precio más reciente de cualquier tienda. "
            "Menos horas = más confianza para decidir hoy.",
        ),
        "last_collected_at": _m(
            "Última captura",
            "Fecha y hora (UTC) del precio más nuevo que tenemos.",
        ),
        "status": _m(
            "Estado de frescura",
            "fresh = datos recientes · aging = llevan horas sin refresh · stale = más de 24 h sin datos nuevos.",
        ),
    },
    "coverage": {
        "stores_active_catalog": _m(
            "Tiendas en catálogo",
            "Tiendas que el sistema intenta monitorear de forma regular (lista objetivo).",
        ),
        "coverage_7d_pct": _m(
            "Cobertura 7 días",
            "Qué porcentaje de esas tiendas tuvo al menos un precio nuevo en la última semana. "
            "No mide calidad del precio, solo si la tienda respondió.",
        ),
        "marketing_gate_pct": _m(
            "Meta de cobertura",
            "Umbral interno (80 %) para considerar el catálogo listo para comunicación pública.",
        ),
        "marketing_gate_pass": _m(
            "¿Cumple la meta?",
            "Sí = al menos 80 % de tiendas activas con datos en 7 días.",
        ),
        "stale_stores_sample": _m(
            "Tiendas sin datos recientes",
            "Ejemplos de tiendas que no reportaron precios en la última semana.",
        ),
    },
    "ops": {
        "collector_status": _m(
            "Estado del recolector",
            "Programa automático que visita las tiendas y guarda precios. "
            "ok = última corrida guardó precios · empty = corrió pero 0 precios · "
            "stale/dead = hace mucho o el moat superó 8h/24h.",
        ),
        "collector_age_hours": _m(
            "Horas desde la última ejecución",
            "Cuánto hace que corrió el recolector, aunque no haya traído precios nuevos.",
        ),
        "last_prices_collected": _m(
            "Precios en la última corrida",
            "Cuántos precios guardó la última ejecución del recolector.",
        ),
        "last_stores_succeeded": _m(
            "Tiendas exitosas",
            "Tiendas de las que la última corrida obtuvo al menos un precio.",
        ),
    },
}


SECTIONS: dict[str, SECTION] = {
    "header": {
        "title": "Resumen rápido",
        "summary": (
            "Números clave arriba de todo: cuántos precios tenemos, cuántos se renovaron hoy "
            "y cuántas tiendas participan. Piensa en ello como el estado de salud del inventario."
        ),
        "metrics": {
            "total_indexed": LAYER_METRICS["inventory"]["total_indexed"],
            "snapshots_24h": LAYER_METRICS["freshness"]["snapshots_24h"],
            "stores_indexed": LAYER_METRICS["inventory"]["stores_indexed"],
            "coverage_bar": _m(
                "Barra de cobertura",
                "Visual simple: cuántas tiendas del catálogo ya tienen precios guardados.",
            ),
        },
    },
    "mental_model": {
        "title": "¿Qué es el Data Moat?",
        "summary": (
            "Un «moat» (foso) es nuestra ventaja: precios reales de muchas tiendas en un solo lugar. "
            "No es una tienda online; es el archivo verificable que compara quién cobra más o menos."
        ),
    },
    "store_health": {
        "title": "Estado de tiendas",
        "summary": (
            "¿Cada supermercado o farmacia responde cuando intentamos leer sus precios? "
            "OK = funciona bien · WARN = falla a veces · DEAD = no obtuvimos datos recientes "
            "(puede ser un cambio en su web o un bloqueo temporal, no necesariamente que cerró)."
        ),
        "metrics": {
            "success_pct": _m(
                "Tasa de éxito",
                "Porcentaje de intentos recientes en los que la tienda devolvió precios válidos.",
            ),
            "consecutive_failures": _m(
                "Fallos seguidos",
                "Cuántas veces seguidas no pudimos leer esa tienda. Muchos fallos = revisar el conector.",
            ),
        },
    },
    "by_line": {
        "title": "Precios por categoría (línea)",
        "summary": (
            "Agrupa productos por tipo de negocio: supermercados, farmacias, electro, etc. "
            "Muestra cuántos precios hay y el precio típico (promedio), el más bajo y el más alto "
            "dentro de esa categoría — sin mezclar monedas distintas en un solo número."
        ),
        "metrics": {
            "count": _m("Cantidad de precios", "Registros en esa categoría."),
            "avg_price": _m("Precio promedio", "Suma de precios dividida entre cuántos hay. No es «el precio justo», solo el centro del rango."),
            "min_price": _m("Precio mínimo", "El más barato registrado en esa categoría."),
            "max_price": _m("Precio máximo", "El más caro registrado en esa categoría."),
        },
    },
    "cheapest_by_line": {
        "title": "Tienda más barata por categoría",
        "summary": (
            "Para cada tipo de negocio, qué tienda tiene el precio promedio más bajo en nuestros datos. "
            "Útil para ver quién suele ser más económico, no para un producto específico."
        ),
    },
    "top_discounts": {
        "title": "Mayores descuentos",
        "summary": (
            "Productos donde el precio publicado es mucho menor que un precio de lista anterior. "
            "Puede ser una oferta real o un error al leer la página."
        ),
        "metrics": {
            "discount_pct": _m(
                "% de descuento",
                "Cuánto bajó el precio respecto al precio tachado o anterior. 50 % = mitad de precio.",
            ),
        },
    },
    "outliers": {
        "title": "Precios fuera de lo normal",
        "summary": (
            "Productos cuyo precio es más del triple del promedio de productos parecidos. "
            "Suele indicar error de scraping, unidad distinta (ej. pack x12) o un producto premium mezclado con uno básico."
        ),
    },
    "inflation": {
        "title": "Cambio de precios en 7 días",
        "summary": (
            "Compara el precio promedio de hoy vs hace una semana en la misma categoría y moneda. "
            "No es la inflación oficial del gobierno: es «¿subieron o bajaron los precios que vemos en tiendas?»"
        ),
        "metrics": {
            "avg_now": _m("Promedio hoy", "Precio medio actual en esa categoría."),
            "avg_before": _m("Promedio hace 7 días", "Precio medio de la semana pasada."),
            "delta_pct": _m(
                "Variación %",
                "Cuánto cambió en porcentaje. +5 % = en promedio 5 % más caro que hace 7 días.",
            ),
        },
    },
    "dispersion": {
        "title": "Diferencia de precios entre tiendas",
        "summary": (
            "Para productos parecidos (misma subcategoría, misma moneda), ¿cuánto varía el precio? "
            "Ejemplo: varios tipos de arroz en distintos supermercados. "
            "Cuando es posible, comparamos precio por kilo o litro para empaques equivalentes."
        ),
        "metrics": {
            "subcategory": _m("Subcategoría", "Tipo de producto dentro de la línea (arroz, leche, jabón…)."),
            "price_basis": _m(
                "Cómo se comparó",
                "per_kg / per_L = precio por kilo o litro · nominal = precio de etiqueta tal cual.",
            ),
            "spread_ratio": _m(
                "Brecha de precio",
                "Qué tan lejos está el precio más caro del más barato, relativo al promedio. "
                "2x = la diferencia equivale al doble del precio medio aproximado.",
            ),
            "status": _m(
                "Nivel de alerta",
                "ok = diferencia normal · warn = >2x · crit = >10x (revisar si no es error de datos).",
            ),
        },
    },
    "marketing_spreads": {
        "title": "Diferencias listas para comunicar",
        "summary": (
            "Subconjunto verificado de la dispersión: mismo producto de canasta básica (empaque 1 kg o 1 L) "
            "o medicamento similar en farmacia, con al menos 2 tiendas y reglas estrictas. "
            "Sirve para citar en LinkedIn u otros materiales sin exagerar."
        ),
        "metrics": {
            "spread_ratio": _m(
                "Brecha verificada",
                "Diferencia de precio entre la tienda más barata y la más cara, con reglas de comparación uniformes.",
            ),
            "stores": _m("Tiendas comparadas", "Cuántas tiendas distintas entraron en la comparación."),
            "marketing_threshold": _m(
                "Umbral mínimo",
                "Solo aparecen aquí las brechas que superan este múltiplo (canasta: 2.5x, farmacia: 10x).",
            ),
        },
    },
    "canasta_basica": {
        "title": "Canasta básica",
        "summary": (
            "Lista fija de 10 productos de consumo diario (arroz, leche, aceite, etc.). "
            "Mostramos cuántos de esos 10 encontramos en cada tienda y cuánto costaría sumarlos. "
            "No es la canasta oficial del INDEC/INEI; es nuestra lista comparable entre retailers."
        ),
        "metrics": {
            "items": _m("Productos encontrados", "De 10 posibles, cuántos tiene precio en esa tienda hoy."),
            "total": _m(
                "Total estimado",
                "Suma de los precios encontrados. Si faltan productos, el total es parcial y puede subestimar el costo real.",
            ),
        },
    },
    "canasta_spreads": {
        "title": "Brechas en canasta básica",
        "summary": (
            "Para cada ítem de la canasta (arroz, aceite…), compara el mejor precio de cada tienda. "
            "Solo empaques estándar 1 kg / 1 L para que la comparación sea justa."
        ),
    },
    "price_movers": {
        "title": "Productos que subieron o bajaron",
        "summary": (
            "Compara el precio de hoy vs la captura anterior del mismo producto. "
            "Requiere al menos dos lecturas en días distintos; si no hay historial, aparece vacío."
        ),
        "metrics": {
            "delta_pct": _m(
                "Cambio %",
                "Cuánto varió ese producto entre la última y la penúltima lectura.",
            ),
        },
    },
    "freshness_table": {
        "title": "Última actualización por tienda",
        "summary": "Fecha y hora del precio más reciente que tenemos de cada tienda.",
    },
    "line_country_matrix": {
        "title": "Tiendas por país y categoría",
        "summary": (
            "Cuántas tiendas monitoreamos en cada combinación de país y tipo de negocio. "
            "Un punto (·) o cero = aún no hay cobertura ahí."
        ),
    },
    "analytics_meta": {
        "title": "Metadatos analíticos",
        "summary": "Parámetros técnicos de cómo se calcularon las métricas de arriba.",
        "metrics": {
            "dispersion_crit_count": _m(
                "Alertas críticas (dispersión)",
                "Cuántos grupos de productos tienen brecha >10x entre tiendas.",
            ),
            "marketing_crit_count": _m(
                "Listos para copy",
                "Cuántas brechas pasaron el filtro estricto para uso en comunicación pública.",
            ),
            "marketing_canasta_min_spread": _m(
                "Umbral canasta",
                "Brecha mínima (2.5x) para que un ítem de canasta aparezca en «listos para copy».",
            ),
        },
    },
}


STATUS_LEGEND: dict[str, str] = {
    "ok": "Diferencia pequeña o normal entre tiendas.",
    "warn": "Diferencia notable (>2x). Vale la pena comparar antes de comprar.",
    "crit": "Diferencia muy grande (>10x). Revisar si no es error de datos o unidades distintas.",
    "fresh": "Datos de las últimas horas — alta confianza para decidir hoy.",
    "aging": "Datos de hace varias horas — usar con precaución.",
    "stale": "Sin datos nuevos en 24+ horas — inventario histórico.",
}


def build_metric_glossary() -> dict:
    """Full glossary payload for /dashboard/data."""
    return {
        "audience": "Lenguaje simple — no se requiere saber de finanzas ni economía.",
        "sections": SECTIONS,
        "layer_metrics": LAYER_METRICS,
        "status_legend": STATUS_LEGEND,
    }


def metric_label(section_id: str, key: str, fallback: str | None = None) -> str:
    sec = SECTIONS.get(section_id, {})
    metrics = sec.get("metrics") if isinstance(sec.get("metrics"), dict) else {}
    if isinstance(metrics, dict) and key in metrics:
        return metrics[key]["label"]
    for layer in LAYER_METRICS.values():
        if key in layer:
            return layer[key]["label"]
    return fallback or key.replace("_", " ").title()


def metric_description(section_id: str, key: str) -> str:
    sec = SECTIONS.get(section_id, {})
    metrics = sec.get("metrics") if isinstance(sec.get("metrics"), dict) else {}
    if isinstance(metrics, dict) and key in metrics:
        return metrics[key]["description"]
    for layer in LAYER_METRICS.values():
        if key in layer:
            return layer[key]["description"]
    return ""


def section_summary(section_id: str) -> str:
    sec = SECTIONS.get(section_id, {})
    return str(sec.get("summary", ""))


def section_title(section_id: str, fallback: str = "") -> str:
    sec = SECTIONS.get(section_id, {})
    return str(sec.get("title", fallback))
