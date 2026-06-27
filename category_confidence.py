"""Product category confidence tiers for safe checkout (A/B/C)."""

from __future__ import annotations

from typing import Literal, TypedDict

ConfidenceTier = Literal["A", "B", "C"]

TIER_C_KEYWORDS: tuple[str, ...] = (
    "laptop",
    "notebook",
    "macbook",
    "computadora",
    "pc gamer",
    "celular",
    "smartphone",
    "iphone",
    "ipad",
    "tablet",
    "televisor",
    " tv ",
    "monitor",
    "impresora",
    "electrodomest",
    "refrigeradora",
    "lavadora",
    "microondas",
    "playstation",
    "xbox",
    "nintendo",
    "audifono",
    "headphone",
    "camara",
    "cámara",
    "drone",
    "smartwatch",
    "reloj inteligente",
    "tarjeta de video",
    "procesador",
    "motherboard",
)

TIER_C_CATEGORIES: tuple[str, ...] = (
    "tecnologia",
    "tecnología",
    "electronica",
    "electrónica",
    "computacion",
    "computación",
    "informatica",
    "informática",
    "celulares",
    "laptops",
    "computadores",
)

TIER_A_KEYWORDS: tuple[str, ...] = (
    "arroz",
    "leche",
    "aceite",
    "azucar",
    "azúcar",
    "fideo",
    "pasta",
    "atun",
    "atún",
    "detergente",
    "papel higienico",
    "papel higiénico",
    "servilleta",
    "cloro",
    "jabon",
    "jabón",
    " harina",
    "yogurt",
    "yogur",
    "mantequilla",
    "cafe ",
    "café ",
    "galleta",
    "avena",
    "quinoa",
    "lenteja",
    "poroto",
    "frijol",
    "sal ",
    "azucar rubia",
    "azúcar rubia",
)


class ConfidenceResult(TypedDict):
    tier: ConfidenceTier
    label: str
    checkout_allowed: bool


def classify_category_confidence(
    *,
    name: str = "",
    category: str = "",
    brand: str = "",
) -> ConfidenceResult:
    text = f" {name} {category} {brand} ".lower()

    for kw in TIER_C_KEYWORDS:
        if kw in text:
            return {
                "tier": "C",
                "label": "Alto riesgo de equivalencia (electro/tecnología)",
                "checkout_allowed": False,
            }

    for cat in TIER_C_CATEGORIES:
        if cat in text:
            return {
                "tier": "C",
                "label": "Categoría de alto riesgo para checkout automático",
                "checkout_allowed": False,
            }

    for kw in TIER_A_KEYWORDS:
        if kw in text:
            return {
                "tier": "A",
                "label": "Canasta homogénea (FMCG)",
                "checkout_allowed": True,
            }

    return {
        "tier": "B",
        "label": "Verificar specs, entrega y garantía con el retailer",
        "checkout_allowed": True,
    }
