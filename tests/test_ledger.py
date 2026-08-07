"""Spec component 3 — the Experiment Ledger as single source of truth."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.experiment_ledger import LedgerError
from src.models import Order, Status

from .conftest import make_experiment


def test_create_and_read_back(ledger):
    stored = ledger.create_experiment(make_experiment())
    assert stored.id is not None
    assert ledger.exists("test-1") is True
    assert ledger.get(stored.id).product_name == "Test Product"


def test_money_survives_the_round_trip(ledger):
    stored = ledger.create_experiment(make_experiment(unit_cost=Decimal("12.34"), revenue=Decimal("9999.99")))
    read = ledger.get(stored.id)
    assert read.unit_cost == Decimal("12.34")
    assert read.revenue == Decimal("9999.99")
    assert isinstance(read.unit_cost, Decimal)


def test_duplicate_product_id_is_refused(ledger):
    ledger.create_experiment(make_experiment())
    with pytest.raises(LedgerError):
        ledger.create_experiment(make_experiment(product_name="Same product, again"))


def test_derived_columns_are_persisted_for_auditors(ledger, db):
    stored = ledger.create_experiment(make_experiment())
    row = db.query_one("SELECT * FROM experiments WHERE id = ?", (stored.id,))
    assert Decimal(str(row["ctr"])) == Decimal("2.00")
    assert Decimal(str(row["cac"])) == Decimal("10.00")
    assert Decimal(str(row["contribution_margin"])) == Decimal("1200.00")
    assert bool(row["meets_sample_gate"]) is True


def test_record_metrics_set_and_add(ledger):
    exp = ledger.create_experiment(
        make_experiment(impressions=0, clicks=0, sessions=0, orders=0,
                        revenue=Decimal("0"), ad_spend=Decimal("0"))
    )
    ledger.record_metrics(exp.id, impressions=1000, orders=10, revenue=Decimal("400"))
    ledger.record_metrics(exp.id, impressions=500, orders=5, revenue=Decimal("200"), mode="add")
    updated = ledger.get(exp.id)
    assert updated.impressions == 1500
    assert updated.orders == 15
    assert updated.revenue == Decimal("600.00")


def test_average_delivery_is_replaced_never_summed(ledger):
    exp = ledger.create_experiment(make_experiment(avg_delivery_days=Decimal("6")))
    ledger.record_metrics(exp.id, avg_delivery_days=Decimal("8"), mode="add")
    assert ledger.get(exp.id).avg_delivery_days == Decimal("8")


def test_every_change_writes_an_event(ledger):
    exp = ledger.create_experiment(make_experiment())
    ledger.record_metrics(exp.id, orders=61)
    ledger.pause_ads(exp.id, "testing")
    types = [e["event_type"] for e in ledger.events(exp.id)]
    assert "created" in types
    assert "metrics_updated" in types


# -- lifecycle ------------------------------------------------------------
def test_lifecycle_transitions(ledger):
    exp = ledger.create_experiment(make_experiment(status=Status.DISCOVERED.value))
    assert ledger.mark_sampled(exp.id).status == "sampled"
    launched = ledger.mark_launched(exp.id, Decimal("10"))
    assert launched.status == "launched"
    assert launched.ads_paused is False
    assert launched.daily_ad_budget == Decimal("10.00")
    assert ledger.mark_active(exp.id).status == "active"


def test_pausing_ads_needs_no_permission_and_is_idempotent(ledger):
    exp = ledger.create_experiment(make_experiment())
    ledger.mark_launched(exp.id, Decimal("10"))
    paused = ledger.pause_ads(exp.id, "kill trigger")
    assert paused.ads_paused is True
    assert paused.daily_ad_budget == Decimal("0.00")
    assert ledger.pause_ads(exp.id, "again").ads_paused is True


def test_resuming_ads_requires_an_approval_id(ledger):
    exp = ledger.create_experiment(make_experiment())
    with pytest.raises(LedgerError):
        ledger.resume_ads(exp.id, Decimal("25"), approval_id=None)
    resumed = ledger.resume_ads(exp.id, Decimal("25"), approval_id=7)
    assert resumed.ads_paused is False
    assert resumed.daily_ad_budget == Decimal("25.00")


def test_flagging_a_kill_pauses_ads_but_does_not_kill(ledger):
    exp = ledger.create_experiment(make_experiment())
    ledger.mark_launched(exp.id, Decimal("10"))
    flagged = ledger.flag_pending_kill(exp.id, "CTR too low")
    assert flagged.pending_kill_reason == "CTR too low"
    assert flagged.ads_paused is True
    assert flagged.status == Status.LAUNCHED.value  # still alive
    assert flagged.killed_at is None


def test_kill_requires_a_reason(ledger):
    exp = ledger.create_experiment(make_experiment())
    with pytest.raises(LedgerError):
        ledger.kill(exp.id, "")


def test_kill_records_the_reason_and_the_moment(ledger):
    exp = ledger.create_experiment(make_experiment())
    killed = ledger.kill(exp.id, "CTR too low", decided_by="robbie")
    assert killed.status == Status.KILLED.value
    assert killed.kill_reason == "CTR too low"
    assert killed.killed_at is not None
    assert killed.ads_paused is True
    event = [e for e in ledger.events(exp.id) if e["event_type"] == "killed"][0]
    assert "robbie" in event["payload"]


def test_killed_experiments_are_not_resurrected(ledger):
    exp = ledger.create_experiment(make_experiment())
    ledger.kill(exp.id, "Refund rate too high")
    with pytest.raises(LedgerError):
        ledger.set_status(exp.id, Status.ACTIVE.value)


def test_continue_clears_the_flag_but_leaves_ads_paused(ledger):
    exp = ledger.create_experiment(make_experiment())
    ledger.mark_launched(exp.id, Decimal("10"))
    ledger.flag_pending_kill(exp.id, "CTR too low")
    resumed = ledger.continue_experiment(exp.id)
    assert resumed.pending_kill_reason is None
    assert resumed.status == Status.LAUNCHED.value
    assert resumed.ads_paused is True  # restarting spend is a separate approval


def test_get_active_only_returns_live_experiments(ledger):
    a = ledger.create_experiment(make_experiment(product_id="a", status=Status.ACTIVE.value))
    ledger.create_experiment(make_experiment(product_id="b", status=Status.DISCOVERED.value))
    c = ledger.create_experiment(make_experiment(product_id="c", status=Status.ACTIVE.value))
    ledger.kill(c.id, "Delivery too slow")
    assert [e.id for e in ledger.get_active()] == [a.id]


# -- orders ---------------------------------------------------------------
def test_recompute_from_orders(ledger):
    exp = ledger.create_experiment(
        make_experiment(orders=0, revenue=Decimal("0"), refunds=0, ad_spend=Decimal("500"))
    )
    for i in range(3):
        ledger.record_order(
            Order(
                experiment_id=exp.id, external_id=f"o{i}", customer_paid=Decimal("40"),
                supplier_cost=Decimal("10"), delivered=True, days_to_delivery=6 + i,
            )
        )
    ledger.record_order(
        Order(experiment_id=exp.id, external_id="o3", customer_paid=Decimal("40"), refunded=True)
    )
    updated = ledger.recompute_from_orders(exp.id)
    assert updated.orders == 4
    assert updated.revenue == Decimal("160.00")
    assert updated.refunds == 1
    assert updated.avg_delivery_days == Decimal("7")
    # ad_spend is platform data, not order data — it must not be overwritten.
    assert updated.ad_spend == Decimal("500.00")


def test_count_by_status(ledger):
    ledger.create_experiment(make_experiment(product_id="a", status=Status.ACTIVE.value))
    b = ledger.create_experiment(make_experiment(product_id="b", status=Status.ACTIVE.value))
    ledger.kill(b.id, "CTR too low")
    assert ledger.count() == 2
    assert ledger.count(Status.KILLED.value) == 1


def test_recompute_refuses_to_discard_metrics_from_another_source(ledger):
    """Manual totals and row-by-row orders are alternatives, not layers.

    An experiment carrying 55 orders typed off a dashboard must not be
    rewritten to 1 order because one order row happens to exist.
    """
    exp = ledger.create_experiment(make_experiment(orders=55, revenue=Decimal("1429.45")))
    ledger.record_order(Order(experiment_id=exp.id, customer_paid=Decimal("25.99")))
    with pytest.raises(LedgerError) as err:
        ledger.recompute_from_orders(exp.id)
    assert "55 orders" in str(err.value)
    unchanged = ledger.get(exp.id)
    assert unchanged.orders == 55
    assert unchanged.revenue == Decimal("1429.45")


def test_recompute_can_be_forced_when_orders_are_the_source_of_truth(ledger):
    exp = ledger.create_experiment(make_experiment(orders=55, revenue=Decimal("1429.45")))
    ledger.record_order(Order(experiment_id=exp.id, customer_paid=Decimal("25.99")))
    updated = ledger.recompute_from_orders(exp.id, allow_decrease=True)
    assert updated.orders == 1
    assert updated.revenue == Decimal("25.99")
