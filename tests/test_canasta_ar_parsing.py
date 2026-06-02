"""Pack parsing and canasta matcher cases from live AR trio data."""

import pytest

from market_spread import matches_canasta_item
from market_units import is_standard_canasta_pack, parse_pack_size

_SM = {"line": "supermercados"}


@pytest.mark.parametrize(
    "name,item",
    [
        ("Aceite de girasol Cocinero 900 cc.", "aceite"),
        ("Aceite de girasol Pureza 900 cc.", "aceite"),
        ("Aceite de maíz Arcor 900 cc.", "aceite"),
        ("Aceite de girasol Cocinero 1.5 l.", "aceite"),
        ("Aceite de girasol Natura 1.5 l.", "aceite"),
        ("Huevos blancos Avicoper 6 u.", "huevos"),
        ("Huevos Blancos 30 Un", "huevos"),
        ("Huevo blanco El Mercado carton 30 uni", "huevos"),
    ],
)
def test_standard_pack_accepts_real_ar_formats(name: str, item: str):
    assert parse_pack_size(name) is not None, name
    assert is_standard_canasta_pack(name, item), name


@pytest.mark.parametrize(
    "name,item",
    [
        ("Aceite de girasol Cocinero 900 cc.", "aceite"),
        ("Aceite de girasol Cocinero 1.5 l.", "aceite"),
        ("Huevos blancos Avicoper 6 u.", "huevos"),
    ],
)
def test_parse_cc_and_trailing_period(name: str, item: str):
    qty, base = parse_pack_size(name)  # type: ignore[misc]
    if item == "aceite":
        assert base == "L"
        assert 0.85 <= qty <= 1.6
    if item == "huevos":
        assert base == "unit"
        assert qty >= 6


@pytest.mark.parametrize(
    "name,item",
    [
        ("Crema de leche Ilolay 200 cc.", "leche"),
        ("Leche condensada Carrefour tetra 395 g.", "leche"),
        ("Yogur entero Tregar dulce de leche", "leche"),
        ("Espumador De Leche Ariete Negro", "leche"),
        ("Leche chocolatada Cindor 1 lt.", "leche"),
        ("Lomito de atún en aceite Carrefour 170 grs", "aceite"),
        ("Papas fritas Lays clásicas 85 g.", "aceite"),
        ("Huevos de pascuas para pintar", "huevos"),
        ("Organizador plástico para 17 huevos", "huevos"),
    ],
)
def test_canasta_matcher_rejects_real_ar_false_positives(name: str, item: str):
    assert not matches_canasta_item({**_SM, "name": name}, item)


def test_docena_parsing():
    assert parse_pack_size("Huevos Blancos Docena") == (12.0, "unit")
    assert parse_pack_size("Huevos media docena frescos") == (6.0, "unit")
    assert is_standard_canasta_pack("Huevos Blancos Docena", "huevos")
