"""Moat spread analytics: subcategory buckets, unit prices, marketing-ready spreads."""

from __future__ import annotations

import difflib
import re
from collections import defaultdict

from market_units import is_standard_canasta_pack, price_per_base_unit
from price_confidence import spread_public_ok

CANASTA_ITEMS = [
    "leche", "arroz", "aceite", "azucar", "huevos", "pan", "cafe", "pollo", "queso", "jabon",
]

# SQL pre-filter patterns (must be superset of _CANASTA_ITEM_PATTERNS; matcher refines).
CANASTA_SQL_LIKE: dict[str, tuple[str, ...]] = {
    "leche": ("%leche%",),
    "arroz": ("%arroz%",),
    "aceite": ("%aceite%",),
    "azucar": ("%azucar%", "%azúcar%", "%açúcar%"),
    "huevos": ("%huevo%", "%huevos%"),
    "pan": ("%pan%", "%pão%"),
    "cafe": ("%cafe%", "%café%"),
    "pollo": ("%pollo%", "%frango%"),
    "queso": ("%queso%", "%queijo%"),
    "jabon": ("%jabon%", "%jabón%", "%sabon%"),
}

# Substrings that indicate a title is NOT the canasta SKU (validated against live compare data).
_CANASTA_EXCLUDE: dict[str, frozenset[str]] = {
    "leche": frozenset({
        "chocolate", "chocolatada", "condensada", "crema de leche", "dulce de leche", "caramelo",
        "mayonesa", "leche de tigre", "saborizante", "saborizador", "tableta", "snickers",
        "hershey", "nesquik", "bombon", "bombón", "espumador", "yogur",
    }),
    "pollo": frozenset({
        "apollo", "alimento para", "gato", "perro", "churu", "consome", "consomé", "cubo",
        "rostizado", "broaster", "+ papas", "caldo", "desodorante", "sabonete", "crema",
    }),
    "queso": frozenset({
        "doritos", "cheetos", "pringles", "ritz", "galleta", "mani", "maní", "snack",
        "tortilla chips", "cortador", "especiero", "easy", "sqweezy", "palito", "bolitas",
        "doypack",
    }),
    "pan": frozenset({
        "pannello", "anello", "panel", "suavipan", "pancha", "lavavajilla", "bolinho",
        "croissant", "cachito", "rosquita", "brioche", "baguette",
    }),
    "jabon": frozenset({"dispensador", "jabonera", "porta"}),
    "aceite": frozenset({"atun", "atún", "filete", "conserva", "esencial", "corporal", "motor"}),
    "huevos": frozenset({"chocolate", "kinder", "pascua", "organizador", "pintar"}),
    "cafe": frozenset({"cafetera", "saborizante", "licor", "filtro"}),
    "azucar": frozenset({"sin azucar", "sin azúcar", "edulcorante", "splenda", "stevia"}),
    "arroz": frozenset({
        "vinagre", "leche de arroz", "pasta de arroz", "harina", "fideo", "yamani",
        "risotto", "carnaroli",
    }),
}

# Word-boundary patterns (accent variants) — blocks apollo/pannello/suavipan false hits.
_CANASTA_ITEM_PATTERNS: dict[str, re.Pattern[str]] = {
    item: re.compile(pat, re.I)
    for item, pat in {
        "leche": r"\bleche\b",
        "arroz": r"\barroz\b",
        "aceite": r"\baceite\b",
        "azucar": r"\b(azucar|azúcar|açúcar)\b",
        "huevos": r"\b(huevos|huevo)\b",
        "pan": r"\b(pan|pão)\b",
        "cafe": r"\b(cafe|café)\b",
        "pollo": r"\b(pollo|frango)\b",
        "queso": r"\b(queso|queijo)\b",
        "jabon": r"\b(jabon|jabón|sabon)\b",
    }.items()
}

MARKETING_CANASTA_MIN_SPREAD = 2.5
MARKETING_FARMACIA_MIN_SPREAD = 10.0

WIDE_LINES = frozenset({"supermercados", "farmacias", "electro", "hogar", "departamentales"})

# line -> (keyword, bucket) longest keywords first
_SUBCATEGORY_KEYWORDS: dict[str, list[tuple[str, str]]] = {
    "supermercados": [
        ("leche en polvo", "leche"), ("sin lactosa", "leche"), ("leche", "leche"),
        ("arroz", "arroz"), ("aceite", "aceite"), ("azucar", "azucar"), ("açúcar", "azucar"),
        ("huevos", "huevos"), ("huevo", "huevos"), ("pan", "pan"), ("pão", "pan"),
        ("cafe", "cafe"), ("café", "cafe"), ("pollo", "pollo"), ("frango", "pollo"),
        ("queso", "queso"), ("queijo", "queso"), ("jabon", "jabon"), ("harina", "harina"),
        ("yogurt", "yogurt"), ("yogur", "yogurt"), ("pasta", "pasta"), ("fideos", "pasta"),
        ("atun", "conservas"), ("atún", "conservas"), ("conserva", "conservas"),
        ("agua", "bebidas"), ("gaseosa", "bebidas"), ("cerveza", "bebidas"), ("cerveja", "bebidas"),
        ("milk", "leche"), ("rice", "arroz"), ("bread", "pan"), ("eggs", "huevos"),
        ("oil", "aceite"), ("sugar", "azucar"), ("coffee", "cafe"), ("chicken", "pollo"),
        ("leite", "leche"), ("frango", "pollo"),
    ],
    "farmacias": [
        ("paracetamol", "paracetamol"), ("ibuprofeno", "ibuprofeno"), ("aspirina", "aspirina"),
        ("omeprazol", "omeprazol"), ("loratadina", "loratadina"), ("dipirona", "dipirona"),
        ("losartana", "losartana"), ("vitamina c", "vitaminas"), ("protector solar", "dermocosmetica"),
        ("shampoo", "higiene"), ("jabon liquido", "higiene"), ("pañales", "bebes"),
        ("gel antibacterial", "higiene"), ("antigripal", "otc"),
    ],
    "electro": [
        ("lavarropas", "linea_blanca"), ("heladera", "linea_blanca"), ("refrigerador", "linea_blanca"),
        ("microondas", "linea_blanca"), ("lavaplatos", "linea_blanca"), ("freezer", "linea_blanca"),
        ("celular", "telefonia"), ("smartphone", "telefonia"), ("telefono", "telefonia"),
        ("motorola", "telefonia"), ("moto g", "telefonia"), ("iphone", "telefonia"),
        ("auriculares", "accesorios"), ("cargador", "accesorios"), ("cable", "accesorios"),
        ("funda", "accesorios"), ("tablet", "computacion"), ("notebook", "computacion"),
        ("monitor", "computacion"), ("televisor", "tv"), ("smart tv", "tv"),
    ],
    "hogar": [
        ("taladro", "herramientas"), ("martillo", "herramientas"), ("destornillador", "herramientas"),
        ("tornillo", "ferreteria"), ("tarugo", "ferreteria"), ("pintura", "construccion"),
        ("ceramica", "construccion"), ("griferia", "banos"), ("inodoro", "banos"),
        ("sarten", "cocina"), ("olla", "cocina"), ("mueble", "muebles"), ("colchon", "muebles"),
        ("sabana", "textil"), ("toalla", "textil"), ("lampara", "iluminacion"),
    ],
    "departamentales": [
        ("perfume", "perfumeria"), ("reloj", "accesorios"), ("televisor", "electro"),
        ("celular", "electro"), ("juguete", "juguetes"), ("maquillaje", "belleza"),
    ],
}

FUZZY_THRESHOLD = 0.70


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-záéíóúñ0-9]", "", (name or "").lower())


def compare_key(name: str, brand: str = "") -> str:
    return f"{(brand or '').lower()}|{_norm_name(name)}"


def infer_subcategory(line: str, name: str, category: str = "") -> str:
    text = (name or "").lower()
    for kw, bucket in _SUBCATEGORY_KEYWORDS.get(line, []):
        if kw in text:
            return bucket
    cat = str(category or "").strip()
    if cat:
        return f"cat:{cat}"
    return "otros"


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _spread_status(ratio: float) -> str:
    if ratio > 10:
        return "crit"
    if ratio > 2:
        return "warn"
    return "ok"


def _spread_from_prices(prices: list[float]) -> dict:
    if not prices:
        return {"spread_ratio": 0.0, "status": "ok", "avg_price": 0.0, "min_price": 0.0, "max_price": 0.0}
    mn, mx = min(prices), max(prices)
    avg = sum(prices) / len(prices)
    ratio = round((mx - mn) / avg, 2) if avg > 0 else 0.0
    return {
        "avg_price": round(avg, 2),
        "min_price": round(mn, 2),
        "max_price": round(mx, 2),
        "spread_ratio": ratio,
        "status": _spread_status(ratio),
    }


def _canasta_name_matches(name: str, item: str) -> bool:
    """Word-boundary match + exclusion list for one canasta seed."""
    text = (name or "").lower()
    pat = _CANASTA_ITEM_PATTERNS.get(item)
    if not pat or not pat.search(text):
        return False
    for ex in _CANASTA_EXCLUDE.get(item, ()):
        if ex in text:
            return False
    return True


def matches_canasta_item(row: dict, item: str, *, standard_pack_only: bool = False) -> bool:
    """True when a product row is a comparable canasta SKU (supermercados only)."""
    if (row.get("line") or "") != "supermercados":
        return False
    name = row.get("name") or ""
    if not _canasta_name_matches(name, item):
        return False
    if standard_pack_only and not is_standard_canasta_pack(name, item):
        return False
    return True


def _canasta_matches(products: list[dict], item: str, *, standard_pack_only: bool) -> list[dict]:
    return [
        p for p in products
        if matches_canasta_item(p, item, standard_pack_only=standard_pack_only)
    ]


def _canasta_store_best(rows: list[dict]) -> dict[str, dict]:
    store_best: dict[str, dict] = {}
    for r in rows:
        store = r.get("store") or r.get("store_name") or "?"
        val, basis = _effective_price(r)
        if val <= 0:
            continue
        cur = store_best.get(store)
        if not cur or val < cur["_val"]:
            store_best[store] = {**r, "_val": val, "_basis": basis}
    return store_best


def _effective_price(row: dict) -> tuple[float, str]:
    """Return (value, basis) using unit price when parseable."""
    ppu = price_per_base_unit(float(row.get("price") or 0), row.get("name") or "")
    if ppu:
        return ppu["price_per"], f"per_{ppu['basis']}"
    return float(row.get("price") or 0), "nominal"


def compute_dispersion(products: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in products:
        line = row.get("line") or ""
        currency = row.get("currency") or "???"
        if line in WIDE_LINES:
            sub = infer_subcategory(line, row.get("name") or "", row.get("category") or "")
            key = (line, row.get("line_name") or line, currency, sub)
        else:
            key = (line, row.get("line_name") or line, currency, "")
        groups[key].append(row)

    out: list[dict] = []
    for (line, line_name, currency, sub), rows in groups.items():
        if len(rows) < 3:
            continue
        by_basis: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            val, basis = _effective_price(r)
            if val > 0:
                by_basis[basis].append(val)

        # Prefer unit-price basis if most rows share the same non-nominal basis
        basis_pick = "nominal"
        best_n = 0
        for basis, vals in by_basis.items():
            if basis != "nominal" and len(vals) > best_n:
                basis_pick = basis
                best_n = len(vals)
        if basis_pick == "nominal" or best_n < max(3, len(rows) // 4):
            prices = by_basis.get("nominal") or [float(r["price"]) for r in rows if r.get("price")]
            basis_pick = "nominal"
        else:
            prices = by_basis[basis_pick]

        stats = _spread_from_prices(prices)
        out.append({
            "line": line_name,
            "line_key": line,
            "currency": currency,
            "subcategory": sub or None,
            "count": len(rows),
            "price_basis": basis_pick,
            **stats,
        })
    out.sort(key=lambda x: (-x["spread_ratio"], x["line"], x.get("subcategory") or ""))
    return out


def find_median_outliers(
    products: list[dict],
    *,
    min_group: int = 5,
    band: float = 5.0,
    limit: int = 10,
) -> list[dict]:
    """Bidirectional outliers vs group median (line + currency + subcategory)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in products:
        line = row.get("line") or ""
        currency = row.get("currency") or "???"
        if line in WIDE_LINES:
            sub = infer_subcategory(line, row.get("name") or "", row.get("category") or "")
            key = (line, currency, sub)
        else:
            key = (line, currency, "")
        groups[key].append(row)

    outliers: list[dict] = []
    for (_line, currency, sub), rows in groups.items():
        if len(rows) < min_group:
            continue
        by_basis: dict[str, list[tuple[dict, float]]] = defaultdict(list)
        for r in rows:
            val, basis = _effective_price(r)
            if val > 0:
                by_basis[basis].append((r, val))

        basis_pick = "nominal"
        best_n = 0
        for basis, pairs in by_basis.items():
            if basis != "nominal" and len(pairs) > best_n:
                basis_pick = basis
                best_n = len(pairs)
        if basis_pick == "nominal" or best_n < max(3, len(rows) // 4):
            pairs = by_basis.get("nominal") or []
            basis_pick = "nominal"
        else:
            pairs = by_basis[basis_pick]

        if len(pairs) < min_group:
            continue
        median = _median([v for _, v in pairs])
        if median <= 0:
            continue
        lo, hi = median / band, median * band
        for r, val in pairs:
            if lo <= val <= hi:
                continue
            ratio = max(val / median, median / val)
            outliers.append({
                "name": r.get("name"),
                "store_name": r.get("store_name"),
                "store": r.get("store"),
                "price": r.get("price"),
                "currency": currency,
                "line_name": r.get("line_name") or r.get("line"),
                "line_key": _line,
                "subcategory": sub or None,
                "price_basis": basis_pick,
                "group_median": round(median, 2),
                "group_n": len(pairs),
                "band": band,
                "lo": round(lo, 2),
                "hi": round(hi, 2),
                "trigger_price": r.get("price"),
                "confidence": "suspect",
                "deviation": "low" if val < lo else "high",
                "_extreme_ratio": ratio,
                "queried_at": r.get("queried_at"),
                "url": r.get("url"),
                "product_id": r.get("product_id"),
            })

    outliers.sort(key=lambda x: x["_extreme_ratio"], reverse=True)
    for item in outliers:
        item.pop("_extreme_ratio", None)
    return outliers[:limit]


def compute_canasta_spreads(products: list[dict]) -> list[dict]:
    results: list[dict] = []
    for item in CANASTA_ITEMS:
        matches = _canasta_matches(products, item, standard_pack_only=True)
        by_currency: dict[str, list[dict]] = defaultdict(list)
        for m in matches:
            by_currency[m.get("currency") or "???"].append(m)

        for currency, rows in by_currency.items():
            store_best = _canasta_store_best(rows)
            vals = [v["_val"] for v in store_best.values()]
            if len(vals) < 2:
                continue
            stats = _spread_from_prices(vals)
            bases = {v["_basis"] for v in store_best.values()}
            rep = min(store_best.values(), key=lambda x: x["_val"])
            results.append({
                "item": item,
                "currency": currency,
                "stores": len(vals),
                "price_basis": bases.pop() if len(bases) == 1 else "mixed",
                "pack_filter": "standard_1kg_1L",
                "sample_name": (rep.get("name") or "")[:60],
                **stats,
            })
    results.sort(key=lambda x: -x["spread_ratio"])
    return results


def _fuzzy_clusters(rows: list[dict]) -> list[list[dict]]:
    """Cluster products by fuzzy name match within one currency bucket."""
    keys: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        k = compare_key(r.get("name") or "", r.get("brand") or "")
        keys[k] = r
        order.append(k)

    clusters: list[list[dict]] = []
    used: set[str] = set()
    for i, ka in enumerate(order):
        if ka in used:
            continue
        cluster = [keys[ka]]
        used.add(ka)
        for kb in order[i + 1 :]:
            if kb in used:
                continue
            score = difflib.SequenceMatcher(None, ka, kb).ratio()
            if score >= FUZZY_THRESHOLD:
                cluster.append(keys[kb])
                used.add(kb)
        clusters.append(cluster)
    return clusters


def compute_marketing_spreads(products: list[dict]) -> list[dict]:
    """Spreads safe for copy: standard canasta packs (≥2.5x) or farmacia fuzzy (≥10x)."""
    out: list[dict] = []

    for item in CANASTA_ITEMS:
        matches = _canasta_matches(products, item, standard_pack_only=True)
        by_currency: dict[str, list[dict]] = defaultdict(list)
        for m in matches:
            by_currency[m.get("currency") or "???"].append(m)

        for currency, rows in by_currency.items():
            store_best = _canasta_store_best(rows)
            if len(store_best) < 2:
                continue
            bases = {v["_basis"] for v in store_best.values()}
            if len(bases) != 1:
                continue
            vals = [v["_val"] for v in store_best.values()]
            stats = _spread_from_prices(vals)
            if stats["spread_ratio"] < MARKETING_CANASTA_MIN_SPREAD:
                continue
            if not spread_public_ok(stats["spread_ratio"]):
                continue
            rep = min(store_best.values(), key=lambda x: x["_val"])
            out.append({
                "seed": item,
                "line": rep.get("line_name") or rep.get("line"),
                "currency": currency,
                "subcategory": item,
                "price_basis": bases.pop(),
                "stores": len(store_best),
                "products": len(rows),
                "sample_name": (rep.get("name") or "")[:60],
                "pack_filter": "standard_1kg_1L",
                "marketing_threshold": MARKETING_CANASTA_MIN_SPREAD,
                "marketing_ready": True,
                **stats,
            })

    for seed in ("paracetamol", "ibuprofeno"):
        line_hint = "farmacias"
        candidates = [
            p for p in products
            if seed in (p.get("name") or "").lower()
            and (
                p.get("line") == line_hint
                or infer_subcategory(p.get("line") or "", p.get("name") or "") == seed
            )
        ]
        by_currency: dict[str, list[dict]] = defaultdict(list)
        for c in candidates:
            by_currency[c.get("currency") or "???"].append(c)

        for currency, rows in by_currency.items():
            for cluster in _fuzzy_clusters(rows):
                if len(cluster) < 2:
                    continue
                store_rows: dict[str, dict] = {}
                for r in cluster:
                    st = r.get("store") or "?"
                    val, basis = _effective_price(r)
                    if val <= 0:
                        continue
                    prev = store_rows.get(st)
                    if not prev or val < prev["_val"]:
                        store_rows[st] = {**r, "_val": val, "_basis": basis}

                if len(store_rows) < 2:
                    continue
                bases = {v["_basis"] for v in store_rows.values()}
                if len(bases) != 1:
                    continue
                vals = [v["_val"] for v in store_rows.values()]
                stats = _spread_from_prices(vals)
                if stats["spread_ratio"] < MARKETING_FARMACIA_MIN_SPREAD:
                    continue
                if not spread_public_ok(stats["spread_ratio"]):
                    continue
                rep = next(iter(store_rows.values()))
                out.append({
                    "seed": seed,
                    "line": rep.get("line_name") or rep.get("line"),
                    "currency": currency,
                    "subcategory": infer_subcategory(rep.get("line") or "", rep.get("name") or ""),
                    "price_basis": bases.pop(),
                    "stores": len(store_rows),
                    "products": len(cluster),
                    "sample_name": (rep.get("name") or "")[:60],
                    "pack_filter": "fuzzy_same_unit",
                    "marketing_threshold": MARKETING_FARMACIA_MIN_SPREAD,
                    "marketing_ready": True,
                    **stats,
                })
    out.sort(key=lambda x: -x["spread_ratio"])
    return out[:20]


def build_spread_analytics(products: list[dict]) -> dict:
    dispersion = compute_dispersion(products)
    canasta_spreads = compute_canasta_spreads(products)
    marketing_spreads = compute_marketing_spreads(products)
    crit = [
        d for d in dispersion
        if d["status"] == "crit" and (d.get("subcategory") or "") not in ("", "otros")
    ]
    return {
        "dispersion": dispersion,
        "canasta_spreads": canasta_spreads,
        "marketing_spreads": marketing_spreads,
        "dispersion_crit_count": len(crit),
        "marketing_crit_count": len(marketing_spreads),
        "marketing_canasta_min_spread": MARKETING_CANASTA_MIN_SPREAD,
        "marketing_farmacia_min_spread": MARKETING_FARMACIA_MIN_SPREAD,
    }
