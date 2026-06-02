"""Tests for unit parsing and spread analytics."""

from market_spread import (
    build_spread_analytics,
    compute_canasta_spreads,
    infer_subcategory,
    matches_canasta_item,
)
from market_units import is_standard_canasta_pack, parse_pack_size, price_per_base_unit


def test_parse_pack_size_grams_and_liters():
    assert parse_pack_size("Arroz Costeño 750g") == (0.75, "kg")
    assert parse_pack_size("Leche Gloria 1L entera") == (1.0, "L")
    assert parse_pack_size("Aceite 900 ml") == (0.9, "L")


def test_price_per_base_unit():
    ppu = price_per_base_unit(7.5, "Arroz superior 750g")
    assert ppu is not None
    assert ppu["basis"] == "kg"
    assert abs(ppu["price_per"] - 10.0) < 0.01


def test_infer_subcategory_supermercados():
    assert infer_subcategory("supermercados", "Arroz Extra Costeño 1kg", "") == "arroz"
    assert infer_subcategory("supermercados", "TV Samsung 50 pulgadas", "") == "otros"


def test_dispersion_uses_subcategory_not_whole_supermercados():
    products = [
        {"line": "supermercados", "line_name": "Supermercados", "currency": "PEN",
         "category": "", "name": "Arroz 1kg", "brand": "", "price": 4.0, "store": "wong"},
        {"line": "supermercados", "line_name": "Supermercados", "currency": "PEN",
         "category": "", "name": "Arroz premium 1kg", "brand": "", "price": 5.0, "store": "metro"},
        {"line": "supermercados", "line_name": "Supermercados", "currency": "PEN",
         "category": "", "name": "Arroz familiar 1kg", "brand": "", "price": 6.0, "store": "plazavea"},
        {"line": "supermercados", "line_name": "Supermercados", "currency": "PEN",
         "category": "", "name": "Televisor 55", "brand": "", "price": 2500.0, "store": "wong"},
    ]
    data = build_spread_analytics(products)
    arroz = [d for d in data["dispersion"] if d.get("subcategory") == "arroz"]
    assert len(arroz) == 1
    assert arroz[0]["status"] != "crit"
    assert data["dispersion_crit_count"] == 0


def test_is_standard_canasta_pack():
    assert is_standard_canasta_pack("Arroz Costeño 1kg", "arroz")
    assert is_standard_canasta_pack("Leche Gloria 1L", "leche")
    assert is_standard_canasta_pack("Aceite 900 ml", "aceite")
    assert not is_standard_canasta_pack("Arroz 5kg", "arroz")
    assert not is_standard_canasta_pack("Arroz suelto", "arroz")


def test_canasta_spreads_need_two_stores():
    products = [
        {"line": "supermercados", "line_name": "Super", "currency": "PEN", "category": "",
         "name": "Arroz superior 1kg", "brand": "", "price": 4.0, "store": "wong", "store_name": "Wong"},
        {"line": "supermercados", "line_name": "Super", "currency": "PEN", "category": "",
         "name": "Arroz 1 kg", "brand": "", "price": 6.0, "store": "metro", "store_name": "Metro"},
    ]
    spreads = compute_canasta_spreads(products)
    arroz = [s for s in spreads if s["item"] == "arroz"]
    assert len(arroz) == 1
    assert arroz[0]["stores"] == 2
    assert arroz[0]["pack_filter"] == "standard_1kg_1L"
    assert arroz[0]["price_basis"] == "per_kg"


def test_marketing_spreads_canasta_at_2_5x():
    from market_spread import compute_marketing_spreads

    base = {"line": "supermercados", "line_name": "Super", "currency": "PEN", "category": "", "brand": ""}
    products = [
        {**base, "name": "Arroz 1kg", "price": 4.0, "store": "wong", "store_name": "Wong"},
        {**base, "name": "Arroz 1 kg", "price": 4.0, "store": "metro", "store_name": "Metro"},
        {**base, "name": "Arroz extra 1kg", "price": 4.0, "store": "plazavea", "store_name": "Plaza Vea"},
        {**base, "name": "Arroz 1000g", "price": 40.0, "store": "tottus", "store_name": "Tottus"},
    ]
    mkt = compute_marketing_spreads(products)
    assert len(mkt) == 1
    assert mkt[0]["seed"] == "arroz"
    assert mkt[0]["spread_ratio"] >= 2.5


def test_canasta_matcher_rejects_documented_false_positives():
    sm = {"line": "supermercados"}
    assert not matches_canasta_item({**sm, "name": "Desodorante Axe Apollo 150ml"}, "pollo")
    assert not matches_canasta_item({**sm, "name": "Chocolate con leche Hershey tableta"}, "leche")
    assert not matches_canasta_item({**sm, "name": "Doritos Queso Nacho 150g"}, "queso")
    assert not matches_canasta_item({**sm, "name": "Atún en Aceite Flor 170g"}, "aceite")
    assert not matches_canasta_item({**sm, "name": "Dispensador de Jabón líquido"}, "jabon")
    assert not matches_canasta_item({**sm, "name": "Lavavajillas Ña Pancha 750ml"}, "pan")
    assert not matches_canasta_item({**sm, "name": "Anello panel decorativo"}, "pan")
    assert not matches_canasta_item({**sm, "name": "Alimento para Gato Pollo 85g"}, "pollo")
    assert matches_canasta_item({**sm, "name": "Leche Gloria entera 1L"}, "leche")
    assert matches_canasta_item({**sm, "name": "Pollo entero fresco kg"}, "pollo")
    assert matches_canasta_item({**sm, "name": "Pan de molde Bimbo 450g"}, "pan")


def test_canasta_matcher_rejects_non_supermercados():
    assert not matches_canasta_item({"line": "electro", "name": "Lavadora Whirlpool 10kg"}, "leche")
