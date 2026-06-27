"""Tests for category_confidence tier classification."""

from category_confidence import classify_category_confidence


def test_tier_a_fmcg():
    r = classify_category_confidence(name="Arroz Costeño 5kg")
    assert r["tier"] == "A"
    assert r["checkout_allowed"] is True


def test_tier_c_laptop():
    r = classify_category_confidence(name="Laptop Lenovo IdeaPad 15")
    assert r["tier"] == "C"
    assert r["checkout_allowed"] is False


def test_tier_b_default():
    r = classify_category_confidence(name="Insumo hotelero genérico")
    assert r["tier"] == "B"
    assert r["checkout_allowed"] is True
