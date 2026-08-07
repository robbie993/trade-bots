"""Spec component 2 — the Economic Calculator."""

from __future__ import annotations

from decimal import Decimal

from src.config import CashConfig, DiscoveryConfig
from src.agents.economic_calculator import EconomicCalculator
from src.agents.scout import ProductCandidate

from .conftest import make_experiment


def calc(**discovery) -> EconomicCalculator:
    return EconomicCalculator(CashConfig(), DiscoveryConfig(**discovery))


def test_landed_cost_includes_shipping():
    assert calc().landed_cost("5.00", "2.35") == Decimal("7.35")


def test_processor_fee_is_percentage_plus_fixed():
    # Stripe default: 2.9% + $0.30 on $40 = $1.46
    assert calc().processor_fee("40.00") == Decimal("1.46")


def test_suggested_price_marks_up_landed_cost_and_charm_prices():
    price = calc().suggest_price("5.00", "2.00")  # landed 7.00 * 3 = 21.00
    assert price == Decimal("21.99")


def test_suggested_price_never_lands_below_the_markup_target():
    price = calc().suggest_price("3.00", "0.30")  # landed 3.30 * 3 = 9.90
    assert price >= Decimal("9.90")
    assert str(price).endswith(".99")


def test_unit_economics_accounts_for_every_cost():
    e = calc().unit_economics(unit_cost="10", selling_price="40", shipping_cost="5")
    assert e.landed_cost == Decimal("15.00")
    assert e.gross_margin_abs == Decimal("25.00")
    assert e.processor_fee == Decimal("1.46")
    assert e.assumed_ad_cost == Decimal("10.00")
    assert e.net_margin_abs == Decimal("13.54")
    # Breakeven CAC: what you could pay per order before contribution hits zero.
    assert e.breakeven_cac == Decimal("23.54")
    assert e.viable is True


def test_a_product_with_no_room_for_ads_is_not_viable():
    e = calc().unit_economics(unit_cost="4.00", selling_price="5.20", shipping_cost="1.00")
    assert e.breakeven_cac < 0
    assert e.viable is False


def test_is_worth_testing_rejects_thin_margins():
    thin = ProductCandidate(
        product_id="p1", product_name="thin", source_platform="aliexpress",
        unit_cost="4.00", shipping_cost="1.00", selling_price="5.20",
    )
    worth, why = calc().is_worth_testing(thin)
    assert worth is False
    assert "margin" in why.lower() or "cac" in why.lower()


def test_is_worth_testing_accepts_a_real_margin():
    good = ProductCandidate(
        product_id="p2", product_name="good", source_platform="aliexpress",
        unit_cost="5.00", shipping_cost="2.00",
    )
    worth, why = calc().is_worth_testing(good)
    assert worth is True
    assert "breakeven CAC" in why


def test_minimum_margin_is_configurable():
    good = ProductCandidate(
        product_id="p3", product_name="good", source_platform="aliexpress",
        unit_cost="5.00", shipping_cost="2.00",
    )
    assert calc(min_margin_pct=Decimal("99")).is_worth_testing(good)[0] is False


def test_calculate_margin_uses_a_suggested_price_when_none_is_given():
    candidate = ProductCandidate(
        product_id="p4", product_name="x", source_platform="temu", unit_cost="6.40",
        shipping_cost="2.00",
    )
    margin = calc().calculate_margin(candidate)
    assert margin > Decimal("60")  # 3x markup implies ~67% gross


def test_store_pnl_rolls_experiments_up():
    experiments = [
        make_experiment(product_id="a", revenue=Decimal("2400"), ad_spend=Decimal("600")),
        make_experiment(product_id="b", revenue=Decimal("1000"), ad_spend=Decimal("900")),
    ]
    pnl = calc().store_pnl(experiments)
    # COGS: 10 * 60 orders each = 1200
    assert pnl["revenue"] == Decimal("3400.00")
    assert pnl["cogs"] == Decimal("1200.00")
    assert pnl["ad_spend"] == Decimal("1500.00")
    assert pnl["contribution_margin"] == Decimal("700.00")
    assert pnl["experiments"] == 2


def test_store_pnl_of_an_empty_store_is_zero_not_an_error():
    pnl = calc().store_pnl([])
    assert pnl["contribution_margin"] == Decimal("0.00")
    assert pnl["contribution_margin_pct"] == Decimal("0.00")
