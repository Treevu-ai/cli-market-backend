"""Parse pack sizes from product titles and normalize to price per base unit."""

from __future__ import annotations

import re

_NUM = r"(\d+(?:[.,]\d+)?)"


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "."))


def parse_pack_size(name: str) -> tuple[float, str] | None:
    """Return (quantity, base_unit) where base_unit is kg, L, or unit."""
    if not name:
        return None
    text = name.lower()

    if re.search(r"\bmedia\s+docena\b", text):
        return 6.0, "unit"
    if re.search(r"\bdocena\b", text):
        return 12.0, "unit"

    patterns: list[tuple[re.Pattern[str], str, float]] = [
        (re.compile(rf"{_NUM}\s*(?:kg|kilo|kilos|kilogramo?s?)\.?\b", re.I), "kg", 1.0),
        (re.compile(rf"{_NUM}\s*(?:g(?:ramos?)?|grs?)\.?\b", re.I), "kg", 0.001),
        (re.compile(rf"{_NUM}\s*(?:l|lt|lts|litro?s?)\.?\b", re.I), "L", 1.0),
        (re.compile(rf"{_NUM}\s*ml\.?\b", re.I), "L", 0.001),
        (re.compile(rf"{_NUM}\s*cc\.?\b", re.I), "L", 0.001),
        (re.compile(rf"{_NUM}\s*(?:un|und|uni|u)\.?\b", re.I), "unit", 1.0),
        (re.compile(rf"\bx\s*{_NUM}\.?\b", re.I), "unit", 1.0),
        (re.compile(rf"maple\s+(?:x\s*)?{_NUM}\b", re.I), "unit", 1.0),
        (re.compile(rf"bandeja\s+(?:x\s*)?{_NUM}\b", re.I), "unit", 1.0),
        (re.compile(rf"cart[oó]n\s+{_NUM}\s*(?:uni|un|u)\.?\b", re.I), "unit", 1.0),
        (re.compile(rf"{_NUM}\s*x\s*{_NUM}\s*(?:g|ml|cc|kg|l)\.?\b", re.I), "unit", 1.0),
    ]

    for pat, base, mult in patterns:
        m = pat.search(text)
        if m:
            qty = _to_float(m.group(1)) * mult
            if qty > 0:
                return qty, base
    return None


def price_per_base_unit(price: float, name: str) -> dict | None:
    parsed = parse_pack_size(name)
    if not parsed or price <= 0:
        return None
    qty, base = parsed
    return {
        "basis": base,
        "pack_qty": qty,
        "price_per": round(price / qty, 4),
    }


# Comparable retail packs — normalized via price_per_base_unit (per L / kg).
_LIQUID_MIN_L = 0.85
_LIQUID_MAX_L = 1.6
_LIQUID_ITEMS = frozenset({"leche", "aceite"})
_WEIGHT_1KG_ITEMS = frozenset({"arroz", "azucar"})


def is_standard_canasta_pack(name: str, item: str) -> bool:
    """Keep canasta rows comparable: ~1 kg, ~1 L, or item-specific unit packs."""
    parsed = parse_pack_size(name)
    if not parsed:
        return False
    qty, base = parsed
    if item in _LIQUID_ITEMS:
        return base == "L" and _LIQUID_MIN_L <= qty <= _LIQUID_MAX_L
    if item in _WEIGHT_1KG_ITEMS:
        return base == "kg" and 0.9 <= qty <= 1.15
    if item == "pan":
        return (base == "kg" and 0.4 <= qty <= 1.15) or (base == "unit" and 1 <= qty <= 2)
    if item in ("cafe", "queso"):
        return base == "kg" and 0.2 <= qty <= 1.15
    if item == "pollo":
        return base == "kg" and 0.7 <= qty <= 2.5
    if item == "huevos":
        return base == "unit" and 6 <= qty <= 30
    if item == "jabon":
        return (base == "kg" and 0.07 <= qty <= 0.25) or (base == "unit" and qty <= 3)
    return False
