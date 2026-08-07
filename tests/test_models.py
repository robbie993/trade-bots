"""Spec 3.1 / 11.1 — derived metric arithmetic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from mvv.models import CashFlowEntry, Experiment, Order, Status
from mvv.money import D, money, percent, ratio_pct, safe_div

from .conftest import make_experiment


def test_derived_metrics_match_the_spec_formulas():
    exp = make_experiment(
        impressions=100_000, clicks=2_000, sessions=1_500, orders=60,
        revenue=Decimal("2400"), ad_spend=Decimal("600"), unit_cost=Decimal("10"),
    )
    assert exp.ctr == Decimal("2.00")  # 2000/100000
    assert exp.conversion_rate == Decimal("4.00")  # 60/1500
    assert exp.cac == Decimal("10.00")  # 600/60
    assert exp.aov == Decimal("40.00")  # 2400/60
    assert exp.contribution_margin == Decimal("1200.00")  # 2400 - 600 - 600
    assert exp.refund_rate == Decimal("1.67")  # 1/60
    assert exp.chargeback_rate == Decimal("0.00")


def test_unit_margin():
    exp = make_experiment(unit_cost=Decimal("12.50"), selling_price=Decimal("39.99"))
    assert exp.margin_abs == Decimal("27.49")
    assert exp.margin_pct == Decimal("68.74")


def test_zero_denominators_do_not_explode():
    exp = Experiment(product_id="new", selling_price=Decimal("20"))
    assert exp.ctr == 0
    assert exp.conversion_rate == 0
    assert exp.cac == 0
    assert exp.aov == 0
    assert exp.refund_rate == 0
    assert exp.meets_sample_gate is False
    # No price yet -> no margin, rather than a division error.
    assert Experiment(product_id="unpriced").margin_pct == 0


def test_money_never_becomes_a_float():
    exp = make_experiment(unit_cost=0.1, revenue=0.2, ad_spend=0.1)
    assert isinstance(exp.unit_cost, Decimal)
    assert isinstance(exp.contribution_margin, Decimal)
    # 0.2 - 0.1*60 - 0.1 in binary float is -5.900000000000001
    assert exp.contribution_margin == Decimal("-5.90")


def test_safe_div_distinguishes_no_data_from_zero():
    assert safe_div(1, 0) is None
    assert safe_div(0, 1) == Decimal("0")


def test_contribution_margin_pct_is_share_of_revenue():
    exp = make_experiment(revenue=Decimal("1000"), ad_spend=Decimal("200"), unit_cost=Decimal("5"), orders=60)
    # 1000 - 300 - 200 = 500 -> 50%
    assert exp.contribution_margin_pct == Decimal("50.00")


def test_status_helpers():
    assert Status.ACTIVE.value in Status.live()
    assert Status.KILLED.value not in Status.live()
    assert Status.DISCOVERED.value not in Status.live()


def test_row_roundtrip_preserves_values():
    exp = make_experiment()
    restored = Experiment.from_row({**exp.to_row(), "id": 7})
    assert restored.product_id == exp.product_id
    assert restored.unit_cost == exp.unit_cost
    assert restored.contribution_margin == exp.contribution_margin
    assert restored.id == 7


def test_persisted_row_includes_derived_columns():
    row = make_experiment().to_row()
    for column in ("ctr", "conversion_rate", "cac", "aov", "contribution_margin", "meets_sample_gate"):
        assert column in row


# -- orders ---------------------------------------------------------------
def test_order_net_contribution_subtracts_every_cost():
    order = Order(
        experiment_id=1, customer_paid=Decimal("49.99"), supplier_cost=Decimal("12.00"),
        shipping_cost=Decimal("4.50"), ad_cost=Decimal("11.00"),
        payment_processor_fee=Decimal("1.75"),
    )
    assert order.net_contribution == Decimal("20.74")


def test_refunded_order_loses_the_costs_not_just_the_revenue():
    """A refund is not a no-op: the supplier, the courier and the ad platform
    all keep their money."""
    order = Order(
        experiment_id=1, customer_paid=Decimal("49.99"), supplier_cost=Decimal("12.00"),
        shipping_cost=Decimal("4.50"), ad_cost=Decimal("11.00"),
        payment_processor_fee=Decimal("1.75"), refunded=True,
    )
    assert order.net_contribution == Decimal("-29.25")


def test_chargeback_is_treated_like_a_refund():
    order = Order(experiment_id=1, customer_paid=Decimal("50"), supplier_cost=Decimal("15"), chargeback=True)
    assert order.net_contribution == Decimal("-15.00")


# -- cash entries ---------------------------------------------------------
def test_held_entry_is_unavailable_until_its_release_date():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    entry = CashFlowEntry(
        description="settling", category="customer_revenue", amount=Decimal("50"),
        hold=True, release_date=now + timedelta(days=7),
    )
    assert entry.is_available_at(now) is False
    assert entry.is_available_at(now + timedelta(days=8)) is True


def test_held_entry_with_no_release_date_is_never_available():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    entry = CashFlowEntry(description="reserve", category="processor_hold", amount=Decimal("5"), hold=True)
    assert entry.is_available_at(now + timedelta(days=3650)) is False


def test_money_helpers_round_half_up():
    assert money("2.345") == Decimal("2.35")
    assert percent("0.005") == Decimal("0.01")
    assert ratio_pct(1, 3) == Decimal("33.33")
    with pytest.raises(TypeError):
        D(True)
