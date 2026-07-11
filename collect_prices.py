#!/usr/bin/env python3
"""
collect_prices.py — VTEX price collector for CLI Market data moat.

PostgreSQL (DATABASE_URL) or SQLite fallback.
Fly.io-compatible: DATABASE_URL is auto-injected by `fly postgres attach`.

Usage:
    python collect_prices.py              # run once
    python collect_prices.py --daemon     # run every 4h
    python collect_prices.py --status     # collection stats
    python collect_prices.py --report     # latest prices per line
"""

import asyncio, json, os, time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import httpx

from market_core import (
    STORES, LINES, logger as log,
    product_from_json as _pfj, fetch_store as _fetch_store,
    ensure_db_initialized,
)
from market_db import get_db
from store_credentials import get_default_stores, resolve_store_config


def _store_line(store: str) -> str:
    try:
        return resolve_store_config(store).get("line", "")
    except KeyError:
        return STORES.get(store, {}).get("line", "")

logger = log.getChild("collector")

DATABASE_URL = os.getenv("DATABASE_URL", "")

PARALLEL = int(os.getenv("COLLECT_PARALLEL", "6"))
REQUEST_DELAY = float(os.getenv("COLLECT_DELAY", "0.75"))
QUERY_TIMEOUT = 15.0
DAEMON_INTERVAL = int(os.getenv("COLLECT_INTERVAL_HOURS", "4"))
MAX_QUERIES_PER_LINE = int(os.getenv("COLLECT_MAX_QUERIES_PER_LINE", "12"))
CORE_QUERIES_PER_LINE = int(os.getenv("COLLECT_CORE_QUERIES", "3"))
COLLECTOR_ADVISORY_LOCK = int(os.getenv("COLLECTOR_ADVISORY_LOCK", "84957231"))
# Circuit breaker: trip after CB_FAIL_THRESHOLD consecutive query failures;
# stay open for CB_COOLDOWN seconds (default 5 min — outlasts the current cycle).
# Set low so one broken store doesn't burn MAX_QUERIES * QUERY_TIMEOUT seconds.
CB_FAIL_THRESHOLD = int(os.getenv("CB_FAIL_THRESHOLD", "3"))
CB_COOLDOWN = int(os.getenv("CB_COOLDOWN", "300"))
# Skip stores that have failed consecutively for this many full collector cycles
# (read from store_health table at the start of each cycle).
CB_PERSIST_SKIP = int(os.getenv("CB_PERSIST_SKIP", "10"))
INDEX_COLLECT_ENABLED = os.getenv("INDEX_COLLECT_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)

LINE_MAX_PRICE = {
    "supermercados": 10_000,
    "farmacias": 5_000,
    "electro": 50_000,
    "moda": 5_000,
    "hogar": 20_000,
    "departamentales": 10_000,
    "automotriz": 50_000,
}

# Nominal caps differ by currency (ARS/CLP/COP use much larger face values).
CURRENCY_LINE_MAX = {
    ("ARS", "supermercados"): 2_000_000,
    ("ARS", "farmacias"): 500_000,
    ("ARS", "electro"): 3_000_000,
    ("ARS", "hogar"): 2_000_000,
    ("ARS", "moda"): 500_000,
    ("ARS", "departamentales"): 2_000_000,
    ("CLP", "supermercados"): 200_000,
    ("CLP", "farmacias"): 100_000,
    ("CLP", "electro"): 5_000_000,
    ("CLP", "hogar"): 3_000_000,
    ("CLP", "moda"): 200_000,
    ("COP", "supermercados"): 500_000,
    ("COP", "farmacias"): 200_000,
    ("COP", "electro"): 20_000_000,
    ("COP", "hogar"): 5_000_000,
    ("MXN", "supermercados"): 50_000,
    ("MXN", "farmacias"): 20_000,
    ("MXN", "electro"): 200_000,
    ("MXN", "hogar"): 100_000,
}

# Extra pause for stores that rate-limit under parallel load.
STORE_EXTRA_DELAY = {
    "globo_br": 2.0,
}
# Appliance-brand stores miss the global electro rotation (Motorola-first).
# Stable per-store queries keep them contributing every batch cycle.
STORE_QUERY_OVERRIDES: dict[str, list[tuple[str, str]]] = {
    "electrolux_ar": [
        ("lavarropas", "electro"), ("heladera", "electro"), ("microondas", "electro"),
        ("refrigerador", "electro"), ("horno", "electro"), ("aspiradora", "electro"),
        ("cocina", "electro"), ("lavadora", "electro"), ("secarropas", "electro"),
        ("freezer", "electro"), ("cafetera", "electro"), ("licuadora", "electro"),
    ],
    "whirlpool_ar": [
        ("lavarropas", "electro"), ("heladera", "electro"), ("microondas", "electro"),
        ("refrigerador", "electro"), ("horno", "electro"), ("aspiradora", "electro"),
        ("cocina", "electro"), ("lavadora", "electro"), ("secarropas", "electro"),
        ("freezer", "electro"), ("lavaplatos", "electro"), ("purificador", "electro"),
    ],
    "electrolux_cl": [
        ("microondas", "electro"), ("refrigerador", "electro"), ("lavadora", "electro"),
        ("aspiradora", "electro"), ("horno", "electro"), ("heladera", "electro"),
        ("freezer", "electro"), ("cocina", "electro"), ("lavarropas", "electro"),
        ("plancha", "electro"), ("tostadora", "electro"), ("batidora", "electro"),
    ],
    "whirlpool_fr": [
        ("lave-linge", "electro"), ("réfrigérateur", "electro"), ("four", "electro"),
        ("aspirateur", "electro"), ("micro-ondes", "electro"), ("lave vaisselle", "electro"),
        ("sèche-linge", "electro"), ("congélateur", "electro"), ("cafetière", "electro"),
        ("mixeur", "electro"), ("bouilloire", "electro"), ("cuisinière", "electro"),
    ],
    "oster_br": [
        ("liquidificador", "electro"), ("batedeira", "electro"), ("cafeteira", "electro"),
        ("torradeira", "electro"), ("aspirador", "electro"), ("geladeira", "electro"),
        ("microondas", "electro"), ("panela", "electro"), ("mixer", "electro"),
        ("sanduicheira", "electro"), ("chaleira", "electro"), ("fogão", "electro"),
    ],
    # PE hogar/departamentales/automotriz stores don't match the generic SEED_QUERIES
    # (supermercados-heavy). Fixed per-store queries keep them contributing every cycle.
    "promart": [
        ("taladro", "hogar"), ("pintura", "hogar"), ("manguera", "hogar"),
        ("cemento", "hogar"), ("foco", "hogar"), ("cerradura", "hogar"),
        ("llave", "hogar"), ("perno", "hogar"), ("cinta", "hogar"),
        ("brocha", "hogar"), ("escalera", "hogar"), ("tuberia", "hogar"),
    ],
    "sodimac_pe": [
        ("taladro", "hogar"), ("pintura", "hogar"), ("martillo", "hogar"),
        ("tornillo", "hogar"), ("cerradura", "hogar"), ("tubo", "hogar"),
        ("llave", "hogar"), ("cable", "hogar"), ("cinta", "hogar"),
        ("foco", "hogar"), ("brocha", "hogar"), ("malla", "hogar"),
    ],
    "ripley_pe": [
        ("televisor", "departamentales"), ("zapatillas", "departamentales"),
        ("perfume", "departamentales"), ("mochila", "departamentales"),
        ("polo", "departamentales"), ("audifono", "departamentales"),
        ("reloj", "departamentales"), ("cartera", "departamentales"),
        ("ropa", "departamentales"), ("jeans", "departamentales"),
        ("celular", "departamentales"), ("tablet", "departamentales"),
    ],
    "falabella_pe": [
        ("televisor", "departamentales"), ("laptop", "departamentales"),
        ("zapatillas", "departamentales"), ("polo", "departamentales"),
        ("perfume", "departamentales"), ("audifono", "departamentales"),
        ("reloj", "departamentales"), ("cartera", "departamentales"),
        ("ropa", "departamentales"), ("jeans", "departamentales"),
        ("celular", "departamentales"), ("tablet", "departamentales"),
    ],
    # xray_pe removed 2026-06-24: 64 consecutive DNS failures, unrecoverable
    "lasirena_es": [
        ("salmón", "supermercados"), ("merluza", "supermercados"), ("pollo", "supermercados"),
        ("congelados", "supermercados"), ("pescado", "supermercados"), ("gambas", "supermercados"),
        ("croquetas", "supermercados"), ("verduras", "supermercados"), ("pizza", "supermercados"),
        ("leche", "supermercados"), ("arroz", "supermercados"), ("aceite", "supermercados"),
    ],
}


def queries_for_store(store: str, global_queries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    overrides = STORE_QUERY_OVERRIDES.get(store)
    if overrides:
        if MAX_QUERIES_PER_LINE > 0:
            return overrides[:MAX_QUERIES_PER_LINE]
        return overrides
    return global_queries



def max_allowed_price(store: str, line: str) -> float:
    currency = STORES.get(store, {}).get("currency", "")
    return CURRENCY_LINE_MAX.get(
        (currency, line),
        LINE_MAX_PRICE.get(line, 99_999_999),
    )

SEED_QUERIES = [
    # ═══════════════════════════════════════════════════════════════════════════
    # 🛒 Supermercados (14 tiendas)
    # ═══════════════════════════════════════════════════════════════════════════
    ("leche","supermercados"),("arroz","supermercados"),("aceite","supermercados"),("azucar","supermercados"),("huevos","supermercados"),
    ("pan","supermercados"),("cafe","supermercados"),("pollo","supermercados"),("carne","supermercados"),("queso","supermercados"),
    ("yogur","supermercados"),("mantequilla","supermercados"),("detergente","supermercados"),("jabon","supermercados"),
    ("papel higienico","supermercados"),("pasta","supermercados"),("agua","supermercados"),("cerveza","supermercados"),("vino","supermercados"),
    ("gaseosa","supermercados"),("atun","supermercados"),("fideos","supermercados"),
    ("galletitas","supermercados"),("yerba","supermercados"),
    ("desodorante","supermercados"),("shampoo","supermercados"),
    ("jamon","supermercados"),("salchicha","supermercados"),
    ("helado","supermercados"),("congelados","supermercados"),
    ("harina","supermercados"),("salsa","supermercados"),("conservas","supermercados"),
    ("milk","supermercados"),("bread","supermercados"),("eggs","supermercados"),
    ("rice","supermercados"),("chicken","supermercados"),("coffee","supermercados"),
    ("oil","supermercados"),("sugar","supermercados"),("butter","supermercados"),
    ("leite","supermercados"),("açúcar","supermercados"),("queijo","supermercados"),
    ("frango","supermercados"),("cerveja","supermercados"),("pão","supermercados"),
    # ═══════════════════════════════════════════════════════════════════════════
    # 💊 Farmacias (2 tiendas: Pacheco BR, Farmatodo MX)
    # ═══════════════════════════════════════════════════════════════════════════
    ("paracetamol","farmacias"),("ibuprofeno","farmacias"),("vitamina c","farmacias"),
    ("protector solar","farmacias"),("shampoo","farmacias"),("crema","farmacias"),
    ("jabon liquido","farmacias"),("pañales","farmacias"),
    ("aspirina","farmacias"),("omeprazol","farmacias"),("loratadina","farmacias"),
    ("curitas","farmacias"),("alcohol","farmacias"),("termometro","farmacias"),
    ("mascarilla","farmacias"),("gel antibacterial","farmacias"),
    ("antigripal","farmacias"),("antidiarreico","farmacias"),
    ("preservativos","farmacias"),("prueba embarazo","farmacias"),
    ("agua oxigenada","farmacias"),("algodon","farmacias"),
    ("dipirona","farmacias"),("losartana","farmacias"),

    # ═══════════════════════════════════════════════════════════════════════════
    # ⚡ Electro (9 tiendas: Motorola AR/BR/MX/CL, Electrolux AR/CL, Whirlpool AR/IT/FR)
    # ═══════════════════════════════════════════════════════════════════════════
    # Smartphones / Motorola
    ("moto g","electro"),("moto edge","electro"),("razr","electro"),
    ("motorola","electro"),("celular","electro"),("telefono","electro"),
    ("smartphone","electro"),("phone","electro"),("iphone","electro"),
    ("xiaomi","electro"),("samsung galaxy","electro"),
    # Accesorios
    ("auriculares","electro"),("cargador","electro"),("funda","electro"),
    ("cable","electro"),("bateria","electro"),("headphones","electro"),
    ("parlante","electro"),("tablet","electro"),("laptop","electro"),
    ("smartwatch","electro"),("monitor","electro"),("teclado","electro"),
    ("mouse","electro"),("impresora","electro"),
    # Línea blanca — Electrolux / Whirlpool
    ("lavarropas","electro"),("heladera","electro"),("cocina","electro"),
    ("aspiradora","electro"),("refrigerador","electro"),("horno","electro"),
    ("microondas","electro"),("lavadora","electro"),("secarropas","electro"),
    ("lavaplatos","electro"),("freezer","electro"),("aire acondicionado","electro"),
    ("purificador","electro"),("cafetera","electro"),("licuadora","electro"),
    ("plancha","electro"),("tostadora","electro"),("batidora","electro"),
    # Samsung
    ("galaxy","electro"),("samsung tv","electro"),("samsung monitor","electro"),
    ("galaxy watch","electro"),("galaxy buds","electro"),("samsung tablet","electro"),
    ("samsung notebook","electro"),("samsung soundbar","electro"),
    # English
    ("fridge","electro"),("washer","electro"),("dryer","electro"),
    ("dishwasher","electro"),("stove","electro"),("vacuum","electro"),
    # Italiano (Whirlpool IT)
    ("frigorifero","electro"),("lavatrice","electro"),("aspirapolvere","electro"),
    ("forno","electro"),
    # Francés (Whirlpool FR)
    ("réfrigérateur","electro"),("lave-linge","electro"),("aspirateur","electro"),
    ("four","electro"),("téléphone","electro"),
    # Portugués (Motorola BR)
    ("celular motorola","electro"),("fone","electro"),("carregador","electro"),
    ("geladeira","electro"),("fogão","electro"),("aspirador","electro"),
    ("notebook","electro"),("monitor gamer","electro"),

    # ═══════════════════════════════════════════════════════════════════════════
    # 👗 Moda (2 tiendas: C&A BR, Hering BR)
    # ═══════════════════════════════════════════════════════════════════════════
    ("camiseta","moda"),("jeans","moda"),("vestido","moda"),("chaqueta","moda"),
    ("zapatos","moda"),("pantalon","moda"),("camisa","moda"),("short","moda"),
    ("blusa","moda"),("falda","moda"),("sueter","moda"),
    ("bufanda","moda"),("traje baño","moda"),("gorra","moda"),
    ("sandalias","moda"),("tenis","moda"),("accesorios","moda"),
    ("bolso mujer","moda"),("cinturon","moda"),("calcetines","moda"),
    ("ropa interior","moda"),("pijama","moda"),("bikini","moda"),
    # English / Portuguese
    ("tshirt","moda"),("dress","moda"),("shoes","moda"),("jacket","moda"),
    ("calça","moda"),("sapato","moda"),("bermuda","moda"),
    ("blusa feminina","moda"),("jaqueta","moda"),("saia","moda"),
    ("camisa social","moda"),("tenis esportivo","moda"),("chinelo","moda"),
    # Shopify
    ("leggings","moda"),("sneakers","moda"),("hoodie","moda"),
    ("running","moda"),("wool","moda"),("lipstick","moda"),
    ("eyeshadow","moda"),("shorts","moda"),("sports bra","moda"),

    # ═══════════════════════════════════════════════════════════════════════════
    # 🔨 Hogar (2 tiendas: Easy AR, Promart PE)
    # ═══════════════════════════════════════════════════════════════════════════
    ("taladro","hogar"),("sarten","hogar"),("silla","hogar"),("lampara","hogar"),
    ("cortina","hogar"),("pintura","hogar"),("martillo","hogar"),
    ("destornillador","hogar"),("toalla","hogar"),("sabana","hogar"),
    ("ceramica","hogar"),("griferia","hogar"),("inodoro","hogar"),
    ("lavatorio","hogar"),("ducha","hogar"),("piso flotante","hogar"),
    ("puerta","hogar"),("ventana","hogar"),("escalera","hogar"),
    ("tornillo","hogar"),("tarugo","hogar"),("cinta","hogar"),
    ("mueble","hogar"),("cama","hogar"),("colchon","hogar"),
    ("jardin","hogar"),("maceta","hogar"),("manguera","hogar"),
    ("pala","hogar"),("balde","hogar"),("clavo","hogar"),
    ("bisagra","hogar"),("cerradura","hogar"),("enchufe","hogar"),
    ("foco","hogar"),("cable electrico","hogar"),

    # ═══════════════════════════════════════════════════════════════════════════
    # 🏬 Departamentales (1 tienda: Coppell AR)
    # ═══════════════════════════════════════════════════════════════════════════
    ("sofa","departamentales"),("cama","departamentales"),
    ("perfume","departamentales"),("reloj","departamentales"),
    ("bolso","departamentales"),("mochila","departamentales"),
    ("juguete","departamentales"),("maquillaje","departamentales"),
    ("televisor","departamentales"),("celular","departamentales"),
    ("zapatillas","departamentales"),("ropa","departamentales"),
    ("mueble","departamentales"),("colchon","departamentales"),
    ("electrodomestico","departamentales"),("bicicleta","departamentales"),
    ("notebook","departamentales"),("auriculares","departamentales"),

    # ═══════════════════════════════════════════════════════════════════════════
    # 🚗 Automotriz (WooCommerce: Xray Chipped PE)
    # ═══════════════════════════════════════════════════════════════════════════
    ("ecu","automotriz"),("chip","automotriz"),("reprogramacion","automotriz"),
    ("diagnostico","automotriz"),("remap","automotriz"),("stage","automotriz"),
    ("performance","automotriz"),("tuning","automotriz"),("obd","automotriz"),
    ("centralita","automotriz"),("mapa","automotriz"),("potencia","automotriz"),
]

# ═══════════════════════════════════════════════════════════════════════════════
# 🔬 Query expansion — line-specific modifiers to increase surface area
# ═══════════════════════════════════════════════════════════════════════════════

EXPANSION_FACTOR = int(os.getenv("COLLECT_EXPANSION", "3"))

QUERY_MODIFIERS: dict[str, list[str]] = {
    "supermercados": [
        "entero", "descremado", "light", "integral", "sin lactosa",
        "organico", "premium", "familiar", "en polvo", "en lata",
        "sin tacc", "zero", "clasico", "suave", "fuerte",
        "x1", "x2", "x6", "pack",
    ],
    "farmacias": [
        "500mg", "100mg", "infantil", "adulto", "forte",
        "caja", "gel", "crema", "spray", "liquido",
        "24hs", "12hs", "dosis unica",
    ],
    "electro": [
        "5g", "128gb", "256gb", "pro", "lite", "ultra",
        "smart", "inalambrico", "bluetooth", "usb",
        "gamer", "4k", "portatil", "digital", "automatico",
        "60hz", "144hz", "ips", "oled",
    ],
    "moda": [
        "mujer", "hombre", "niño", "niña", "unisex",
        "talle m", "talle l", "talle xl", "talle s",
        "algodon", "jean", "cuero", "lino",
        "estampado", "liso", "bordado",
    ],
    "hogar": [
        "electrico", "manual", "profesional", "industrial",
        "12v", "220v", "acero", "plastico", "madera",
        "30cm", "50cm", "1m", "blanco", "negro",
        "impermeable", "antideslizante",
    ],
    "departamentales": [
        "2 plazas", "1 plaza", "king size", "queen",
        "led", "smart tv", "inalambrico", "bluetooth",
        "infantil", "adulto", "grande", "mediano",
    ],
}

EXACT_QUERIES: set[str] = {
    "moto g", "moto edge", "razr", "iphone", "xiaomi",
    "samsung galaxy", "celular motorola", "monitor gamer",
    "dipirona", "losartana", "omeprazol", "loratadina",
    "frigorifero", "lavatrice", "aspirapolvere",
    "réfrigérateur", "lave-linge", "aspirateur",
}

def expand_queries(base: list[tuple[str, str]], cycle: int = 0) -> list[tuple[str, str]]:
    """Expand base queries with line-specific modifiers. Rotates modifier subset per cycle."""
    if EXPANSION_FACTOR <= 0:
        return list(base)
    expanded = list(base)
    seen: set[str] = {q for q, _ in base}
    for query, line in base:
        if query in EXACT_QUERIES:
            continue
        modifiers = QUERY_MODIFIERS.get(line, [])
        if not modifiers:
            continue
        start = (cycle * EXPANSION_FACTOR) % max(len(modifiers), 1)
        picked = [modifiers[(start + i) % len(modifiers)] for i in range(EXPANSION_FACTOR)]
        for mod in picked:
            variant = f"{query} {mod}"
            if variant not in seen:
                seen.add(variant)
                expanded.append((variant, line))
    return expanded


# ═══════════════════════════════════════════════════════════════════════════════
# 🧠 Feedback loop — data-moat-driven queries from existing price_snapshots
# ═══════════════════════════════════════════════════════════════════════════════

FEEDBACK_LIMIT = int(os.getenv("COLLECT_FEEDBACK", "30"))
FEEDBACK_DAYS = int(os.getenv("COLLECT_FEEDBACK_DAYS", "7"))
FEEDBACK_MIN_COUNT = int(os.getenv("COLLECT_FEEDBACK_MIN", "3"))

def _dedup_seed_terms() -> set[str]:
    terms: set[str] = set()
    for q, _ in SEED_QUERIES:
        terms.add(q.lower())
        for word in q.lower().split():
            if len(word) >= 3:
                terms.add(word)
    return terms

def get_feedback_queries(db) -> list[tuple[str, str]]:
    """Extract top product names from data moat — unified PG/SQLite path."""
    feedback: list[tuple[str, str]] = []
    seed_terms = _dedup_seed_terms()
    per_line = max(1, FEEDBACK_LIMIT // max(len(QUERY_MODIFIERS), 1))
    lines = list(QUERY_MODIFIERS.keys())
    since = (datetime.now(timezone.utc) - timedelta(days=FEEDBACK_DAYS)).isoformat()
    for line in lines:
        try:
            rows = db.execute("""
                SELECT name, COUNT(*) as freq
                FROM price_snapshots
                WHERE line = ? AND price > 0
                  AND queried_at >= ?
                GROUP BY name
                HAVING COUNT(*) >= ?
                ORDER BY freq DESC
                LIMIT ?
            """, (line, since, FEEDBACK_MIN_COUNT, per_line + 2)).fetchall()
        except Exception:
            logger.debug("feedback query failed for line=%s", line, exc_info=True)
            continue
        for r in rows:
            name = str(r["name"]).strip().lower()
            query = " ".join(name.split()[:3])
            if len(query) < 3:
                continue
            if query in seed_terms:
                continue
            words = set(query.split())
            if len(words & seed_terms) >= len(words) * 0.75:
                continue
            if query not in seed_terms:
                seed_terms.add(query)
                feedback.append((query, line))
    return feedback[:FEEDBACK_LIMIT]


def _core_queries_by_line() -> dict[str, list[tuple[str, str]]]:
    """First N seed terms per line — always queried every cycle (stable yield)."""
    by_line: dict[str, list[tuple[str, str]]] = defaultdict(list)
    if CORE_QUERIES_PER_LINE <= 0:
        return {}
    for q, line in SEED_QUERIES:
        ln = line or "supermercados"
        if len(by_line[ln]) < CORE_QUERIES_PER_LINE:
            by_line[ln].append((q, line))
    return dict(by_line)


def cap_queries_for_cycle(queries: list[tuple[str, str]], cycle: int = 0) -> list[tuple[str, str]]:
    """Core terms per line + rotating window so each cycle stays fast and pool-friendly."""
    if MAX_QUERIES_PER_LINE <= 0:
        return queries
    by_line: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for q, line in queries:
        by_line[line or "supermercados"].append((q, line))
    cores = _core_queries_by_line()
    capped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line, items in by_line.items():
        if not items:
            continue
        for entry in cores.get(line, []):
            if entry not in seen:
                seen.add(entry)
                capped.append(entry)
        rotate_budget = max(0, MAX_QUERIES_PER_LINE - len(cores.get(line, [])))
        if rotate_budget <= 0:
            continue
        start = (cycle * rotate_budget) % len(items)
        for i in range(min(rotate_budget, len(items))):
            entry = items[(start + i) % len(items)]
            if entry not in seen:
                seen.add(entry)
                capped.append(entry)
    return capped


def _store_health_ok(*, collected: int, query_ok: int, query_empty: int, query_fail: int) -> bool:
    """Technical health: penalize only hard fetch failures, not rotation misses."""
    if collected > 0 or query_ok > 0:
        return True
    if query_empty > 0 and query_fail == 0:
        return True
    return False


def build_query_list(db=None, cycle: int = 0) -> list[tuple[str, str]]:
    """Build the full query list: expanded seed + feedback from data moat."""
    queries = expand_queries(SEED_QUERIES, cycle)
    if db and FEEDBACK_LIMIT > 0:
        try:
            fb = get_feedback_queries(db)
            if fb:
                logger.info("Feedback: %d data-moat queries injected", len(fb))
                queries.extend(fb)
        except Exception as e:
            logger.warning("Feedback queries failed: %s", e)
    queries = cap_queries_for_cycle(queries, cycle)
    return queries


def _get_feedback_db():
    """Get a DB connection for feedback queries. Returns None if DB unavailable."""
    try:
        from market_core import get_db
        db = get_db()
        # Verify the price_snapshots table exists
        db.execute("SELECT 1 FROM price_snapshots LIMIT 1")
        return db
    except Exception:
        return None


# ── Database: PostgreSQL or SQLite ──────────────────────────────────────────

def _pg_host_ok(url: str) -> bool:
    """When DATABASE_URL is set, always attempt PostgreSQL."""
    return bool(url)

USE_PG = bool(DATABASE_URL) and _pg_host_ok(DATABASE_URL)

if USE_PG:
    import asyncpg
    _pg_pool = None

    async def get_pool():
        global _pg_pool
        if _pg_pool is None:
            pool_size = max(PARALLEL + 2, 15)
            # SSL: respect sslmode in DATABASE_URL; default to 'prefer' so it
            # works on both Fly private networking (no SSL) and public URLs (SSL).
            # Override with PG_SSL_MODE env var if needed.
            ssl_mode = os.getenv("PG_SSL_MODE", "prefer")
            ssl_arg: object = ssl_mode if ssl_mode not in ("disable", "false") else False
            _pg_pool = await asyncpg.create_pool(
                DATABASE_URL, min_size=2, max_size=pool_size, command_timeout=60,
                ssl=ssl_arg,
            )
        return _pg_pool

    async def pg_try_daemon_lock(pool) -> bool:
        async with pool.acquire() as conn:
            return bool(await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", COLLECTOR_ADVISORY_LOCK,
            ))

    async def pg_release_daemon_lock(pool) -> None:
        async with pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_unlock($1)", COLLECTOR_ADVISORY_LOCK)

    async def init_schema():
        # Single source of truth: market_core.init_db() owns the DDL.
        # We call it synchronously here — it's idempotent and only runs once.
        ensure_db_initialized()
        try:
            from price_snapshots_schema import ensure_canonical_product_id_column

            ensure_canonical_product_id_column()
        except Exception as e:
            logger.warning("canonical_product_id migration skipped: %s", e)
        try:
            from collector_schema import ensure_collector_runs_columns

            ensure_collector_runs_columns()
        except Exception as e:
            logger.warning("collector_runs migration skipped: %s", e)
        try:
            from stock_history_schema import ensure_stock_history_table

            ensure_stock_history_table()
        except Exception as e:
            logger.warning("stock_history migration skipped: %s", e)

    async def pg_insert(conn, prod):
        from price_confidence import compute_snapshot_confidence

        discount = prod.get("discount")
        if discount is not None:
            discount = int(round(float(discount)))
        list_price = prod.get("list_price")
        confidence = compute_snapshot_confidence(
            float(prod["price"]),
            float(list_price) if list_price else None,
        )
        await conn.execute("""
            INSERT INTO price_snapshots (product_id,name,brand,price,list_price,discount,store,store_name,currency,line,line_name,category,stock,url,confidence)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (product_id,store) DO UPDATE SET
                name=EXCLUDED.name,
                brand=EXCLUDED.brand,
                price=EXCLUDED.price,
                list_price=EXCLUDED.list_price,
                discount=EXCLUDED.discount,
                stock=EXCLUDED.stock,
                confidence=EXCLUDED.confidence,
                queried_at=NOW()
        """, prod["product_id"],prod["name"],prod["brand"],prod["price"],prod["list_price"],discount,
           prod["store"],prod["store_name"],prod["currency"],prod["line"],prod["line_name"],prod["category"],prod["stock"],prod["url"], confidence)

        stock = prod.get("stock")
        await conn.execute(
            "INSERT INTO stock_history (product_id, store, in_stock) VALUES ($1, $2, $3)",
            prod["product_id"], prod["store"], 1 if (stock and stock > 0) else 0,
        )

    async def pg_health(conn, store, ok):
        if ok:
            await conn.execute(
                "INSERT INTO store_health (store,last_success,total_requests,total_successes,consecutive_failures) "
                "VALUES ($1,NOW(),1,1,0) ON CONFLICT(store) DO UPDATE SET "
                "last_success=NOW(),total_requests=store_health.total_requests+1,"
                "total_successes=store_health.total_successes+1,consecutive_failures=0",
                store,
            )
        else:
            await conn.execute(
                "INSERT INTO store_health (store,last_error,consecutive_failures,total_requests) "
                "VALUES ($1,NOW(),1,1) ON CONFLICT(store) DO UPDATE SET "
                "last_error=NOW(),consecutive_failures=store_health.consecutive_failures+1,"
                "total_requests=store_health.total_requests+1",
                store,
            )

    async def pg_run_start(conn, n):
        return (await conn.fetchrow("INSERT INTO collector_runs (stores_attempted) VALUES ($1) RETURNING id", n))["id"]

    async def pg_run_end(conn, rid, responded, total, errs, yielded=0):
        await conn.execute(
            "UPDATE collector_runs SET finished_at=NOW(), stores_succeeded=$1, "
            "prices_collected=$2, errors=$3, stores_with_yield=$4 WHERE id=$5",
            responded, total, errs, yielded, rid,
        )

def get_db_unified():
    """Return a unified DB handle (_DB) — works with both PG and SQLite."""
    ensure_db_initialized()
    try:
        from price_snapshots_schema import ensure_canonical_product_id_column

        ensure_canonical_product_id_column()
    except Exception as e:
        logger.warning("canonical_product_id migration skipped: %s", e)
    try:
        from collector_schema import ensure_collector_runs_columns

        ensure_collector_runs_columns()
    except Exception as e:
        logger.warning("collector_runs migration skipped: %s", e)
    try:
        from stock_history_schema import ensure_stock_history_table

        ensure_stock_history_table()
    except Exception as e:
        logger.warning("stock_history migration skipped: %s", e)
    return get_db()

def sq_insert(db, prod):
    from market_core import save_price_snapshot
    from stock_history_schema import append_stock_history

    save_price_snapshot(prod, db=db)
    stock = prod.get("stock")
    append_stock_history(db, prod["product_id"], prod["store"], bool(stock and stock > 0))

def sq_health(db, store, ok):
    if ok:
        db.execute(
            "INSERT INTO store_health (store,last_success,total_requests,total_successes,consecutive_failures) "
            "VALUES (?,datetime('now'),1,1,0) ON CONFLICT(store) DO UPDATE SET "
            "last_success=datetime('now'),total_requests=total_requests+1,"
            "total_successes=total_successes+1,consecutive_failures=0",
            (store,),
        )
    else:
        db.execute(
            "INSERT INTO store_health (store,last_error,consecutive_failures,total_requests) "
            "VALUES (?,datetime('now'),1,1) ON CONFLICT(store) DO UPDATE SET "
            "last_error=datetime('now'),consecutive_failures=consecutive_failures+1,"
            "total_requests=total_requests+1",
            (store,),
        )

def sq_run_start(db, n): return db.execute("INSERT INTO collector_runs (started_at,stores_attempted) VALUES (datetime('now'),?)",(n,)).lastrowid
def sq_run_end(db, rid, responded, total, errs, yielded=0):
    db.execute(
        "UPDATE collector_runs SET finished_at=datetime('now'), stores_succeeded=?, "
        "prices_collected=?, errors=?, stores_with_yield=? WHERE id=?",
        (responded, total, errs, yielded, rid),
    )

# ── Price normalization ─────────────────────────────────────────────────────

def parse_price(p):
    try: return float(p or 0)
    except: return 0.0

def _old_product_from_json(p, store):
    items = p.get("items",[]); item = items[0] if items else {}
    sellers = item.get("sellers",[]); seller = sellers[0] if sellers else {}
    offer = seller.get("commertialOffer",{})
    price = parse_price(offer.get("Price")); list_price = parse_price(offer.get("ListPrice"))
    discount = round((1-price/list_price)*100) if list_price>price>0 else None
    sid = p.get("productReference") or p.get("productId","")
    return {"product_id":sid,"name":p.get("productName","").replace("-"," "),"brand":p.get("brand") or "","category":p.get("categoryId",""),"price":price,"list_price":list_price,"discount":discount,"stock":offer.get("AvailableQuantity",0),"store":store,"store_name":STORES.get(store,{}).get("name",store),"currency":STORES.get(store,{}).get("currency",""),"line":STORES.get(store,{}).get("line",""),"line_name":LINES.get(STORES.get(store,{}).get("line",""),{}).get("name",""),"url":f"{STORES.get(store,{}).get('base','')}/{p.get('linkText','')}/p"}

# ── Circuit breaker ─────────────────────────────────────────────────────────

class CB:
    def __init__(s): s.f=defaultdict(int); s.o={}
    def ok(s,k):
        if k in s.o:
            if time.time()<s.o[k]: return False
            del s.o[k]; s.f[k]=0
        return True
    def win(s,k): s.f[k]=0
    def lose(s,k):
        s.f[k]+=1
        if s.f[k]>=CB_FAIL_THRESHOLD: s.o[k]=time.time()+CB_COOLDOWN
    def reset(s): s.f.clear(); s.o.clear()
cb=CB()

async def fetch_store_multi(client, store, term):
    return await _fetch_store(store, term, page=1, limit=10)

# ── Full catalog download ───────────────────────────────────────────────────

CATALOG_INTERVAL_MINS = int(os.getenv("COLLECT_CATALOG_INTERVAL", "60"))
_last_catalog_pull: float = 0.0

async def collect_full_catalog_pg(pool, store: str) -> int:
    from market_connectors import get_connector
    from store_credentials import resolve_store_config

    cfg = resolve_store_config(store)
    platform = cfg.get("platform", "vtex")
    if platform not in ("vtex", "woocommerce"):
        return 0
    connector = get_connector(platform)
    try:
        all_raw = await connector.fetch_all_products(cfg, max_pages=20)
    except Exception as e:
        logger.warning("full catalog %s: %s", store, str(e)[:80])
        return 0
    collected = 0
    line = cfg.get("line", "")
    line_name = LINES.get(line, {}).get("name", "")
    async with pool.acquire() as conn:
        for p in all_raw:
            prod = connector.normalize(p, store, cfg)
            prod["line"] = line
            prod["line_name"] = line_name
            if prod.get("price", 0) <= 0:
                continue
            if prod["price"] > max_allowed_price(store, line):
                continue
            try:
                from price_confidence import compute_snapshot_confidence

                list_price = prod.get("list_price")
                confidence = compute_snapshot_confidence(
                    float(prod["price"]),
                    float(list_price) if list_price else None,
                )
                await conn.execute("""
                    INSERT INTO price_snapshots (product_id,name,brand,price,list_price,discount,store,store_name,currency,line,line_name,category,stock,url,confidence)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    ON CONFLICT (product_id,store) DO UPDATE SET
                        name=EXCLUDED.name,
                        brand=EXCLUDED.brand,
                        price=EXCLUDED.price,
                        list_price=EXCLUDED.list_price,
                        discount=EXCLUDED.discount,
                        stock=EXCLUDED.stock,
                        confidence=EXCLUDED.confidence,
                        queried_at=NOW()
                """, prod["product_id"], prod["name"], prod["brand"], prod["price"],
                   prod.get("list_price", 0), prod.get("discount"), prod["store"],
                   prod["store_name"], prod["currency"], prod["line"], prod["line_name"],
                   prod.get("category", ""), prod.get("stock", 0), prod.get("url", ""), confidence)
                collected += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
    return collected

async def run_full_catalog_pg(pool, stores: list[str], *, force: bool = False) -> int:
    global _last_catalog_pull
    from store_credentials import resolve_store_config

    now = time.monotonic()
    if not force and now - _last_catalog_pull < CATALOG_INTERVAL_MINS * 60:
        return 0
    _last_catalog_pull = now
    total = 0
    for store in stores:
        if resolve_store_config(store).get("platform") not in ("vtex", "woocommerce"):
            continue
        n = await collect_full_catalog_pg(pool, store)
        print(f"    📦 {store}: {n:,} products (full catalog)")
        total += n
    return total



async def force_catalog_stores(stores: list[str]) -> dict:
    """Bypass catalog interval and upsert full catalog for given stores."""
    if not USE_PG:
        raise RuntimeError("force_catalog_stores requires PostgreSQL (DATABASE_URL)")
    pool = await get_pool()
    await init_schema()
    total = 0
    per_store: dict[str, int] = {}
    for store in stores:
        n = await collect_full_catalog_pg(pool, store)
        per_store[store] = n
        total += n
        print(f"    📦 {store}: {n:,} products (forced catalog)")
    return {"stores": per_store, "prices_collected": total}

# ── Collector core ──────────────────────────────────────────────────────────

async def _pg_consecutive_failures(pool, store: str) -> int:
    """Read persistent consecutive_failures from store_health (0 if not found)."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT consecutive_failures FROM store_health WHERE store=$1", store
            )
            return int(row["consecutive_failures"] or 0) if row else 0
    except Exception:
        return 0


async def collect_one_pg(pool, store, queries):
    if not cb.ok(store):
        logger.warning("circuit open — skipping %s", store)
        return 0
    consec = await _pg_consecutive_failures(pool, store)
    if consec >= CB_PERSIST_SKIP:
        logger.warning("persistent failures — skipping %s (%d consecutive cycles failed)", store, consec)
        return 0
    queries = queries_for_store(store, queries)
    line = _store_line(store)
    queries_for_line = sum(1 for _q, lf in queries if not lf or lf == line)
    if queries_for_line == 0:
        logger.info("store %s: no seed queries for line=%s — skipping", store, line)
        return 0
    collected = 0
    query_ok = 0
    query_fail = 0
    query_empty = 0
    pending: list[dict] = []
    insert_errors: list[str] = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(QUERY_TIMEOUT),
            headers={"User-Agent": "Mozilla/5.0 (compatible; CLI-Market-Collector/1.0; +https://cli-market.dev)"},
            follow_redirects=True,
        ) as client:
            for q, lf in queries:
                if lf and line != lf:
                    continue
                try:
                    raw = await fetch_store_multi(client, store, q)
                    if not raw:
                        # Empty result = store doesn't carry this category, not a
                        # failure. Counting it against the circuit breaker poisons
                        # the lifetime success_pct of stores with partial catalogs.
                        query_empty += 1
                        continue
                    cb.win(store)
                    query_ok += 1
                    for p in raw:
                        prod = _pfj(p, store)
                        prod["line"] = line
                        prod["line_name"] = LINES.get(line, {}).get("name", "")
                        if prod["price"] <= 0:
                            continue
                        if prod["price"] > max_allowed_price(store, line):
                            continue
                        pending.append(prod)
                    await asyncio.sleep(REQUEST_DELAY + STORE_EXTRA_DELAY.get(store, 0.0))
                except Exception as exc:
                    query_fail += 1
                    err = str(exc)
                    if "429" in err:
                        await asyncio.sleep(5.0)
                        try:
                            raw = await fetch_store_multi(client, store, q)
                            if not raw:
                                # Empty after a 429 retry: not a failure either.
                                query_empty += 1
                                continue
                            cb.win(store)
                            query_ok += 1
                            for p in raw:
                                prod = _pfj(p, store)
                                prod["line"] = line
                                prod["line_name"] = LINES.get(line, {}).get("name", "")
                                if prod["price"] <= 0:
                                    continue
                                if prod["price"] > max_allowed_price(store, line):
                                    continue
                                pending.append(prod)
                            await asyncio.sleep(REQUEST_DELAY + STORE_EXTRA_DELAY.get(store, 0.0))
                            continue
                        except Exception as retry_exc:
                            err = str(retry_exc)
                    logger.warning("collect %s/%s: %s", store, q, err[:120])
                    cb.lose(store)
        if pending:
            async with pool.acquire() as conn:
                for prod in pending:
                    try:
                        await pg_insert(conn, prod)
                        collected += 1
                    except Exception as exc:
                        insert_errors.append(str(exc)[:120])
                        logger.warning("insert %s: %s", store, str(exc)[:120])
                health_ok = _store_health_ok(
                    collected=collected,
                    query_ok=query_ok,
                    query_empty=query_empty,
                    query_fail=query_fail,
                )
                await pg_health(conn, store, health_ok)
        else:
            health_ok = _store_health_ok(
                collected=collected,
                query_ok=query_ok,
                query_empty=query_empty,
                query_fail=query_fail,
            )
            async with pool.acquire() as conn:
                await pg_health(conn, store, health_ok)
    except Exception as exc:
        logger.warning("collect_one_pg %s failed: %s", store, str(exc)[:160])
        cb.lose(store)
    if query_fail and query_ok == 0 and collected == 0:
        logger.warning("store %s: %d query failures, %d empty, 0 successes", store, query_fail, query_empty)
    elif query_ok == 0 and query_empty > 0 and query_fail == 0:
        logger.info("store %s: catalog returned no matches for %d queries (no errors)", store, query_empty)
    elif query_ok > 0 and collected == 0:
        logger.warning("store %s: %d queries OK but 0 prices saved", store, query_ok)
    elif collected > 0:
        logger.info("store %s: %d products from %d queries (%d empty)", store, collected, query_ok, query_empty)
    if insert_errors:
        logger.warning("store %s: %d insert errors (first: %s)", store, len(insert_errors), insert_errors[0])
    return collected

def _sq_consecutive_failures(db, store: str) -> int:
    """Read persistent consecutive_failures from store_health (0 if not found)."""
    try:
        row = db.execute(
            "SELECT consecutive_failures FROM store_health WHERE store=?", (store,)
        ).fetchone()
        return int(row["consecutive_failures"] or 0) if row else 0
    except Exception:
        return 0


async def collect_one_sqlite(db, store, queries):
    """Collect for one store, reusing a single SQLite connection across
    all inserts (orders of magnitude cheaper than open-per-row, and avoids
    `database is locked` storms under PARALLEL workers)."""
    if not cb.ok(store):
        logger.warning("circuit open — skipping %s", store)
        return 0
    consec = _sq_consecutive_failures(db, store)
    if consec >= CB_PERSIST_SKIP:
        logger.warning("persistent failures — skipping %s (%d consecutive cycles failed)", store, consec)
        return 0
    queries = queries_for_store(store, queries)
    line = STORES[store].get("line", "")
    collected = 0
    attempted = 0
    query_ok = 0
    query_fail = 0
    query_empty = 0
    for q, lf in queries:
        if lf and line != lf:
            continue
        attempted += 1
        try:
            raw = await _fetch_store(store, q, page=1, limit=10)
            if not raw:
                query_empty += 1
                continue
            cb.win(store)
            query_ok += 1
            for p in raw:
                prod = _pfj(p, store)
                prod["line"] = line
                prod["line_name"] = LINES.get(line, {}).get("name", "")
                if prod.get("price", 0) and prod["price"] > 0:
                    if prod["price"] > max_allowed_price(store, prod.get("line", "")):
                        continue
                    sq_insert(db, prod)
                    collected += 1
            await asyncio.sleep(REQUEST_DELAY)
        except Exception as _e:
            query_fail += 1
            logger.warning("collect %s/%s: %s", store, q, str(_e)[:200])
            cb.lose(store)
    if attempted > 0:
        sq_health(
            db,
            store,
            _store_health_ok(
                collected=collected,
                query_ok=query_ok,
                query_empty=query_empty,
                query_fail=query_fail,
            ),
        )
    if attempted > 0 and collected == 0:
        logger.warning("store %s: tried %d queries, 0 results (line=%s)", store, attempted, line)
    elif collected > 0:
        logger.info("store %s: %d products from %d queries", store, collected, attempted)
    return collected

async def run_collection(stores, queries):
    sl = list(stores)
    batch_size = max(1, min(PARALLEL, len(sl)))
    total = 0
    yielded = 0
    responded = 0
    errs: list[str] = []
    if USE_PG:
        pool = await get_pool()
        await init_schema()
        async with pool.acquire() as c:
            rid = await pg_run_start(c, len(sl))
        for i in range(0, len(sl), batch_size):
            batch = sl[i:i + batch_size]
            tasks = [collect_one_pg(pool, s, queries) for s in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for store, r in zip(batch, results, strict=True):
                if isinstance(r, Exception):
                    errs.append(f"{store}: {r}")
                    logger.warning("store %s exception: %s", store, str(r)[:160])
                else:
                    responded += 1
                    if r > 0:
                        total += r
                        yielded += 1
        async with pool.acquire() as c:
            await pg_run_end(c, rid, responded, total, json.dumps(errs[:100]), yielded)
        if total == 0 and len(sl) > 0:
            logger.warning(
                "collection cycle saved 0 prices for %d stores (%d hard errors)",
                len(sl), len(errs),
            )
    else:
        db = get_db_unified()
        rid = sq_run_start(db, len(sl))
        batch_size = max(1, min(PARALLEL, len(sl)))
        for i in range(0, len(sl), batch_size):
            batch = sl[i:i + batch_size]
            for store in batch:
                try:
                    r = await collect_one_sqlite(db, store, queries)
                    responded += 1
                    if r > 0:
                        total += r
                        yielded += 1
                except Exception as exc:
                    errs.append(f"{store}: {exc}")
        sq_run_end(db, rid, responded, total, json.dumps(errs[:100]), yielded)
        db.commit()
    return {
        "stores_attempted": len(sl),
        "stores_succeeded": responded,
        "stores_responded": responded,
        "stores_with_yield": yielded,
        "prices_collected": total,
        "errors": len(errs),
    }

# ── Status / Report ─────────────────────────────────────────────────────────

def do_status():
    """Print collector status — works with both PG and SQLite via unified DB."""
    db = get_db()
    backend = "PostgreSQL" if USE_PG else "SQLite"
    total = db.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
    stores = db.execute("SELECT COUNT(DISTINCT store) c FROM price_snapshots").fetchone()["c"]
    latest = db.execute("SELECT MAX(queried_at) m FROM price_snapshots").fetchone()["m"]
    runs = db.execute("SELECT COUNT(*) c FROM collector_runs").fetchone()["c"]
    print(f"═══ Price Collector ({backend}) ═══\n  Prices: {total:,} | Stores: {stores}/{len(STORES)} | Latest: {latest or 'never'} | Runs: {runs}")
    top = db.execute("SELECT store_name, COUNT(*) n FROM price_snapshots GROUP BY store_name ORDER BY n DESC LIMIT 5").fetchall()
    if top: print("  Top:"); [print(f"    {r['store_name'][:25]:<25} {r['n']:>6}") for r in top]
    db.close()

def _run_index_cycle(prices_collected: int) -> None:
    """Feed recent price_snapshots into the semantic index after a collection run."""
    if not INDEX_COLLECT_ENABLED or prices_collected <= 0:
        return
    try:
        from index_gate import certify_round

        stats = certify_round(prices_collected)
        resolved = stats.get("resolved", 0)
        registry = stats.get("registry_size", 0)
        linked = stats.get("linked", 0)
        if resolved:
            print(
                f"  🧠 Index: {resolved} snapshots resolved, {linked} linked → "
                f"{registry:,} Golden Records "
                f"({stats.get('exact', 0)} exact, {stats.get('auto', 0)} new)"
            )
        else:
            logger.info("Index cycle: 0 snapshots resolved (registry=%d)", registry)
    except Exception as exc:
        print(f"  ⚠ Index cycle skipped: {exc}")
        logger.warning("Index cycle failed: %s", exc)


def do_report():
    """Print latest prices by line — works with both PG and SQLite via unified DB."""
    db = get_db()
    rows = db.execute(
        "SELECT line,line_name,COUNT(*) c,MIN(price) mn,MAX(price) mx,AVG(price) av,MAX(queried_at) lt "
        "FROM price_snapshots WHERE price>0 GROUP BY line,line_name ORDER BY c DESC"
    ).fetchall()
    print("═══ Latest Prices by Line ═══\n")
    for row in rows:
        print(f"[{row['line_name']}]  {row['c']:,} prices | {row['mn']:.2f}–{row['mx']:.2f} | avg {row['av']:.2f} | {row['lt']}")
        ch = db.execute(
            "SELECT name,store_name,price,currency FROM price_snapshots WHERE line=? AND price>0 ORDER BY price ASC LIMIT 3",
            (row["line"],)
        ).fetchall()
        for p in ch:
            print(f"    ↓ {p['name'][:45]:<45} {p['store_name'][:15]:<15} {p['currency']} {p['price']:.2f}")
        print()
    db.close()

# ── Main ────────────────────────────────────────────────────────────────────

async def main():
    import argparse
    ap = argparse.ArgumentParser(description="CLI Market Price Collector")
    ap.add_argument("--daemon", action="store_true"); ap.add_argument("--interval", type=int, default=DAEMON_INTERVAL)
    ap.add_argument("--status", action="store_true"); ap.add_argument("--report", action="store_true")
    ap.add_argument("--stores", type=int, default=0); ap.add_argument("--queries", type=int, default=0)
    ap.add_argument("--parallel", type=int, default=50)
    ap.add_argument("--catalog-store", action="append", default=[], metavar="STORE",
                    help="Force full catalog pull for store(s); bypasses 60-min interval")
    args = ap.parse_args()
    global PARALLEL; PARALLEL = args.parallel
    ensure_db_initialized()
    # Do NOT reassign USE_PG from market_core here: the module-level USE_PG
    # already reflects DATABASE_URL. Re-reading after ensure_db_initialized()
    # could silently flip to SQLite if Postgres had a transient init error.
    if args.status: do_status(); return
    if args.report: do_report(); return
    if args.catalog_store:
        if not USE_PG:
            print("✗ --catalog-store requires PostgreSQL (DATABASE_URL)")
            return
        r = await force_catalog_stores(args.catalog_store)
        print(f"  ✓ Forced catalog: {r['prices_collected']:,} prices across {len(r['stores'])} store(s)")
        for sk, n in r["stores"].items():
            print(f"    {sk}: {n:,}")
        do_status()
        return
    stores = get_default_stores()
    stores = stores[:args.stores] if args.stores else stores
    label = "PostgreSQL" if USE_PG else "SQLite"

    if args.daemon:
        print(f"🔄 Daemon: every {args.interval}h | expansion: ×{EXPANSION_FACTOR} | max {MAX_QUERIES_PER_LINE}/line")
        cycle = 0
        print(f"🚀 Collector daemon started — {label} backend — PID {os.getpid()}")
        while True:
            lock_ok = True
            pool = None
            try:
                cb.reset()
                if USE_PG:
                    for _pg_attempt in range(1, 6):
                        try:
                            pool = await get_pool()
                            break
                        except Exception as _pg_err:
                            global _pg_pool
                            _pg_pool = None
                            if _pg_attempt == 5:
                                raise
                            wait = _pg_attempt * 10
                            print(f"  ⚠ PG connect attempt {_pg_attempt}/5 failed: {_pg_err} — retry in {wait}s")
                            await asyncio.sleep(wait)
                    lock_ok = await pg_try_daemon_lock(pool)
                    if not lock_ok:
                        print(f"⏭ Another collector holds the lock — skipping cycle {cycle}")
                        cycle += 1
                        await asyncio.sleep(max(args.interval * 3600, 60))
                        continue
                db = _get_feedback_db()
                queries = build_query_list(db=db, cycle=cycle)
                if db:
                    db.close()
                if args.queries:
                    queries = queries[:args.queries]
                print(f"\n─── {datetime.now(timezone.utc).isoformat()} [cycle {cycle}] ───")
                print(f"🔍 {label} | {len(stores)} stores × {len(queries)} queries (capped rotation)")
                t0 = time.monotonic()
                r = await run_collection(stores, queries)
                responded = r.get("stores_responded", r.get("stores_succeeded", 0))
                yielded = r.get("stores_with_yield", 0)
                attempted = r["stores_attempted"]
                print(
                    f"  ✓ {r['prices_collected']:,} prices | "
                    f"yield {yielded}/{attempted} | "
                    f"responded {responded}/{attempted} | "
                    f"{time.monotonic()-t0:.1f}s | {r['errors']} errors"
                )
                # Evaluate price alerts after every collection cycle
                try:
                    from market_alerts import evaluate_alerts
                    fired = evaluate_alerts()
                    if fired:
                        print(f"  🔔 Alerts: {fired} fired")
                except Exception as _ae:
                    print(f"  ⚠ Alert evaluation skipped: {_ae}")
                if USE_PG and pool:
                    cat_count = await run_full_catalog_pg(pool, stores)
                    if cat_count:
                        print(f"  📦 Full catalog: {cat_count:,} new products")
                    # Refresh enrichment indicators every 6 cycles (24h) for all countries
                    if cycle % 6 == 0:
                        try:
                            from market_indicators import refresh_after_collection
                            result = refresh_after_collection()
                            total = result.get("enrichment_written", 0)
                            print(f"  📡 Indicators refreshed: {result.get('internal_written',0)} internal + {result.get('external_written',0)} external + {total} enrichment ({len(result.get('countries',[]))} countries)")
                        except Exception as e:
                            print(f"  ⚠ Indicator refresh skipped: {e}")
            except Exception as e:
                print(f"  ✗ Cycle {cycle} crashed: {e}")
                import traceback
                traceback.print_exc()
            finally:
                if USE_PG and pool and lock_ok:
                    await pg_release_daemon_lock(pool)
            cycle += 1
            wait_s = max(args.interval * 3600, 60)
            # Poll for dashboard refresh triggers every 30 s
            poll_interval = 30
            elapsed = 0
            while elapsed < wait_s:
                await asyncio.sleep(min(poll_interval, wait_s - elapsed))
                elapsed += poll_interval
                try:
                    db_trig = get_db()
                    pending = db_trig.execute(
                        "SELECT id FROM collector_triggers WHERE fulfilled_at IS NULL ORDER BY id ASC LIMIT 1"
                    ).fetchone()
                    if pending:
                        print(f"  ⚡ Dashboard trigger received (id={pending['id']}) — skipping wait, starting cycle now")
                        db_trig.execute(
                            "UPDATE collector_triggers SET fulfilled_at = NOW() WHERE id = %s",
                            (pending["id"],),
                        ) if USE_PG else db_trig.execute(
                            "UPDATE collector_triggers SET fulfilled_at = datetime('now') WHERE id = ?",
                            (pending["id"],),
                        )
                        db_trig.commit()
                        db_trig.close()
                        break
                    db_trig.close()
                except Exception:
                    pass  # Don't crash the daemon over trigger polling
    else:
        db = _get_feedback_db()
        queries = build_query_list(db=db, cycle=0)
        if db: db.close()
        if args.queries: queries = queries[:args.queries]
        print(f"🔍 {label} | {len(stores)} stores × {len(queries)} queries (seed+feedback)")
        t0=time.monotonic(); r=await run_collection(stores, queries)
        responded = r.get("stores_responded", r.get("stores_succeeded", 0))
        yielded = r.get("stores_with_yield", 0)
        print(
            f"  ✓ {r['prices_collected']:,} prices | yield {yielded}/{r['stores_attempted']} | "
            f"responded {responded}/{r['stores_attempted']} | {time.monotonic()-t0:.1f}s | {r['errors']} errors"
        )
        do_status()

if __name__ == "__main__":
    asyncio.run(main())