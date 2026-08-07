"""Spec section 4 — the kill criteria, condition by condition.

These are the tests that matter most: this logic is the only thing standing
between the operator and burning $1,000 on a product nobody wants.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.kill_criteria import (
    INSUFFICIENT_DATA,
    KillThresholds,
    SampleGate,
    kill_check_table,
    meets_sample_gate,
    missing_sample,
    should_kill_experiment,
)

from .conftest import make_experiment


# -- 4.1 sample gates -----------------------------------------------------
def test_healthy_experiment_is_not_killed(healthy):
    should_kill, reason = should_kill_experiment(healthy)
    assert should_kill is False
    assert reason is None


@pytest.mark.parametrize(
    "field,value",
    [("impressions", 999), ("sessions", 99), ("orders", 49)],
)
def test_each_gate_alone_blocks_a_decision(field, value):
    exp = make_experiment(**{field: value})
    assert meets_sample_gate(exp) is False
    should_kill, reason = should_kill_experiment(exp)
    assert should_kill is False
    assert reason == INSUFFICIENT_DATA


def test_gates_are_inclusive_at_the_threshold():
    exp = make_experiment(impressions=1000, sessions=100, orders=50)
    assert meets_sample_gate(exp) is True


def test_terrible_metrics_below_the_gate_are_never_killed():
    """The single most important behaviour: no decision without data.

    Nothing about this product looks good, but with 5 orders none of it is
    distinguishable from noise, so the answer is 'wait', not 'kill'.
    """
    exp = make_experiment(
        impressions=800, clicks=1, sessions=40, orders=5, revenue=Decimal("50"),
        ad_spend=Decimal("900"), refunds=4, chargebacks=3,
    )
    should_kill, reason = should_kill_experiment(exp)
    assert should_kill is False
    assert reason == INSUFFICIENT_DATA


def test_missing_sample_reports_the_gap():
    exp = make_experiment(impressions=400, sessions=60, orders=12)
    assert missing_sample(exp) == {"impressions": 600, "sessions": 40, "orders": 38}


def test_per_experiment_gates_override_defaults():
    exp = make_experiment(
        impressions=200, sessions=20, orders=10,
        minimum_impressions=100, minimum_sessions=10, minimum_orders=5,
    )
    assert meets_sample_gate(exp) is True


# -- 4.2 kill conditions, in order ---------------------------------------
def test_kill_on_low_ctr():
    exp = make_experiment(impressions=100_000, clicks=400)  # 0.4%
    assert should_kill_experiment(exp) == (True, "CTR too low")


def test_ctr_exactly_at_threshold_survives():
    exp = make_experiment(impressions=100_000, clicks=500)  # exactly 0.5%
    assert should_kill_experiment(exp)[0] is False


def test_kill_on_low_conversion_rate():
    exp = make_experiment(sessions=10_000, orders=60)  # 0.6%
    assert should_kill_experiment(exp) == (True, "Conversion rate too low")


def test_kill_on_cac_above_half_the_price():
    # $40 price -> CAC ceiling $20. $1,260/60 = $21.
    exp = make_experiment(ad_spend=Decimal("1260"), orders=60)
    assert should_kill_experiment(exp) == (True, "CAC exceeds 50% of price")


def test_cac_exactly_at_half_the_price_survives():
    exp = make_experiment(ad_spend=Decimal("1200"), orders=60)  # CAC exactly $20
    assert should_kill_experiment(exp)[0] is False


def test_kill_on_zero_contribution_margin():
    # revenue 2400 - COGS 600 - ads 1800 = 0, which is not positive.
    exp = make_experiment(revenue=Decimal("2400"), ad_spend=Decimal("1800"), selling_price=Decimal("400"))
    should_kill, reason = should_kill_experiment(exp)
    assert (should_kill, reason) == (True, "No positive contribution margin")


def test_kill_on_high_refund_rate():
    exp = make_experiment(orders=60, refunds=6)  # 10%
    assert should_kill_experiment(exp) == (True, "Refund rate too high")


def test_refund_rate_at_threshold_survives():
    exp = make_experiment(orders=100, refunds=8, sessions=2000)  # exactly 8%
    assert should_kill_experiment(exp)[0] is False


def test_kill_on_high_chargeback_rate():
    exp = make_experiment(orders=60, chargebacks=1)  # 1.67%
    assert should_kill_experiment(exp) == (True, "Chargeback rate too high")


def test_kill_on_slow_delivery():
    exp = make_experiment(avg_delivery_days=Decimal("14"))
    assert should_kill_experiment(exp) == (True, "Delivery too slow")


def test_delivery_at_ten_days_survives():
    exp = make_experiment(avg_delivery_days=Decimal("10"))
    assert should_kill_experiment(exp)[0] is False


def test_conditions_are_evaluated_in_spec_order():
    """When several conditions fire, the reason is the first one in the spec's
    table — so the same data always produces the same recorded reason."""
    exp = make_experiment(
        impressions=100_000, clicks=300,  # CTR 0.3% (condition 1)
        sessions=10_000,                  # CR 0.6%  (condition 2)
        refunds=30,                       # 50%      (condition 5)
    )
    assert should_kill_experiment(exp) == (True, "CTR too low")


# -- thresholds are configurable, defaults are the spec's -----------------
def test_thresholds_can_be_tightened():
    exp = make_experiment()  # CTR 2%
    strict = KillThresholds(min_ctr_pct=Decimal("3.0"))
    assert should_kill_experiment(exp, strict) == (True, "CTR too low")


def test_default_thresholds_match_the_spec():
    t = KillThresholds()
    assert (t.min_ctr_pct, t.min_conversion_rate_pct) == (Decimal("0.5"), Decimal("1.0"))
    assert t.max_cac_as_fraction_of_price == Decimal("0.5")
    assert t.max_refund_rate_pct == Decimal("8.0")
    assert t.max_chargeback_rate_pct == Decimal("1.0")
    assert t.max_avg_delivery_days == Decimal("10")
    g = SampleGate()
    assert (g.minimum_impressions, g.minimum_sessions, g.minimum_orders) == (1000, 100, 50)


def test_check_table_shows_every_condition(healthy):
    table = kill_check_table(healthy)
    assert len(table) == 7
    assert all(row["triggered"] is False for row in table)
    assert [row["condition"] for row in table][0] == "CTR"


def test_check_table_marks_the_conditions_that_fired():
    exp = make_experiment(refunds=20, chargebacks=5)
    fired = {row["condition"] for row in kill_check_table(exp) if row["triggered"]}
    assert fired == {"Refund rate", "Chargeback rate"}
