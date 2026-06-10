"""Pricing helpers and PayPal subscription response labels."""

from __future__ import annotations

from market_billing import (
    FOUNDING_PROMO_CODE,
    checkout_upgrade_detail,
    normalize_billing_plan,
    price_label_for_plan,
    tier_for_billing_plan,
    validate_founding_available,
)


def test_normalize_billing_plan_aliases():
    assert normalize_billing_plan("founding") == "pro_founding"
    assert normalize_billing_plan("annual") == "pro_annual"
    assert normalize_billing_plan("unknown") == "pro"


def test_tier_for_billing_plan():
    assert tier_for_billing_plan("starter") == "starter"
    assert tier_for_billing_plan("pro_founding") == "pro"
    assert tier_for_billing_plan("pro_annual") == "pro"


def test_price_labels():
    assert price_label_for_plan("starter") == "$24/mo"
    assert price_label_for_plan("pro") == "$39/mo"
    assert price_label_for_plan("pro_founding") == "$29/mo"
    assert price_label_for_plan("pro_annual") == "$390/yr"


def test_checkout_upgrade_detail_uses_pro_price():
    assert "$39/mo" in checkout_upgrade_detail()


def test_founding_validation_wrong_code(isolated_db):
    ok, err = validate_founding_available("user-test", "wrong-code")
    assert not ok
    assert FOUNDING_PROMO_CODE in err
