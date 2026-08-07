"""Spec component 5 / section 11.2 — the loop, end to end.

These tests drive the whole machine: discovery through kill, with the human
gate in the middle, on a real (SQLite) database.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from mvv.config import CashConfig, Config, DiscoveryConfig, GateConfig, StopConfig
from mvv.human_gate import ApprovalAction, HumanGate
from mvv.metrics import MetricsSnapshot, StaticMetricsProvider
from mvv.models import Status
from mvv.orchestrator import Orchestrator
from mvv.scout import FixtureSource, Scout

from .conftest import make_experiment


@pytest.fixture
def orch(db, tmp_path, fixture_file, notifier) -> Orchestrator:
    config = Config(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        notification_log=tmp_path / "notifications.log",
        cash=CashConfig(),
        gate=GateConfig(),
        discovery=DiscoveryConfig(),
        stop=StopConfig(),
    )
    scout = Scout([FixtureSource(fixture_file)])
    gate = HumanGate(db, notifier, config.gate, config)
    return Orchestrator(db, config, scout=scout, notifier=notifier, gate=gate)


# =========================================================================
# 1. DISCOVER
# =========================================================================
def test_discovery_creates_experiments_for_viable_products(orch):
    report = orch.tick()
    products = {e.product_id for e in orch.ledger.all()}
    assert "ae-1" in products  # good margin
    assert "ae-2" not in products  # no room for ad spend
    assert report.discovered


def test_discovery_uses_landed_cost_as_cogs(orch):
    orch.tick()
    exp = orch.ledger.get_by_product_id("ae-1")
    assert exp.unit_cost == Decimal("7.00")  # $5 unit + $2 shipping


def test_unapproved_platforms_are_refused_and_escalated(orch):
    orch.tick()
    assert orch.ledger.get_by_product_id("x-1") is None
    pending = [p for p in orch.gate.pending() if p.action == ApprovalAction.ADD_PLATFORM.value]
    assert pending and pending[0].details["platform"] == "wish"


def test_discovery_is_idempotent(orch):
    orch.tick()
    before = orch.ledger.count()
    orch.tick()
    assert orch.ledger.count() == before


# =========================================================================
# 2. ADVANCE — nothing moves without approval
# =========================================================================
def test_a_new_experiment_asks_before_ordering_a_sample(orch):
    orch.tick()
    exp = orch.ledger.get_by_product_id("ae-1")
    assert exp.status == Status.DISCOVERED.value
    assert any(
        p.action == ApprovalAction.ORDER_SAMPLE.value and p.experiment_id == exp.id
        for p in orch.gate.pending()
    )


def test_no_cash_moves_before_the_sample_is_approved(orch):
    orch.cash.open_account()
    before = orch.cash.total_balance()
    orch.tick()
    orch.tick()
    assert orch.cash.total_balance() == before


def test_approved_sample_is_ordered_and_paid_for(orch):
    orch.cash.open_account()
    orch.tick()
    exp = orch.ledger.get_by_product_id("ae-1")
    approval = orch.gate.find_pending(ApprovalAction.ORDER_SAMPLE.value, exp.id)
    orch.gate.approve(approval.id, "robbie")

    orch.tick()
    assert orch.ledger.get(exp.id).status == Status.SAMPLED.value
    assert orch.cash.total_balance() == Decimal("993.00")  # $1000 - $7 landed cost


def test_launch_needs_both_a_listing_and_a_spend_approval(orch):
    orch.cash.open_account()
    orch.tick()
    exp = orch.ledger.get_by_product_id("ae-1")
    orch.gate.approve(orch.gate.find_pending(ApprovalAction.ORDER_SAMPLE.value, exp.id).id)
    orch.tick()  # -> sampled, asks for a listing approval

    listing = orch.gate.find_pending(ApprovalAction.LIST_PRODUCT.value, exp.id)
    assert listing is not None
    orch.gate.approve(listing.id)
    orch.tick()  # listing approved, now asks for ad spend
    assert orch.ledger.get(exp.id).status == Status.SAMPLED.value  # still not live

    spend = orch.gate.find_pending(ApprovalAction.AD_SPEND.value, exp.id)
    assert spend is not None
    orch.gate.approve(spend.id)
    orch.tick()

    launched = orch.ledger.get(exp.id)
    assert launched.status == Status.LAUNCHED.value
    assert launched.ads_paused is False
    assert launched.daily_ad_budget == Decimal("10.00")


def test_launch_is_blocked_when_the_cash_is_not_there(orch):
    orch.cash.open_account(Decimal("155"))  # $5 spendable over the buffer
    orch.tick()
    exp = orch.ledger.get_by_product_id("ae-1")
    orch.gate.approve(orch.gate.find_pending(ApprovalAction.ORDER_SAMPLE.value, exp.id).id)
    report = orch.tick()
    # $7 sample against $5 spendable: approved, but the money isn't there.
    assert orch.ledger.get(exp.id).status == Status.DISCOVERED.value
    assert any("spendable" in why for _, why in report.skipped)


# =========================================================================
# 3-4. MEASURE and EVALUATE
# =========================================================================
def launch(orch, product_id="ae-1"):
    """Walk a product all the way to LAUNCHED, approving as we go."""
    orch.cash.open_account()
    orch.tick()
    exp = orch.ledger.get_by_product_id(product_id)
    for action in (
        ApprovalAction.ORDER_SAMPLE.value,
        ApprovalAction.LIST_PRODUCT.value,
        ApprovalAction.AD_SPEND.value,
    ):
        for _ in range(3):
            pending = orch.gate.find_pending(action, exp.id)
            if pending:
                orch.gate.approve(pending.id, "robbie")
                break
            orch.tick()
        orch.tick()
    return orch.ledger.get(exp.id)


def test_metrics_sync_updates_the_ledger_and_books_the_cash(orch):
    exp = launch(orch)
    assert exp.status == Status.LAUNCHED.value
    orch.metrics = StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=20_000, clicks=600, sessions=500, orders=55,
            revenue=Decimal("1200"), ad_spend=Decimal("300"),
        )}
    )
    before = orch.cash.total_balance()
    orch.tick()

    updated = orch.ledger.get(exp.id)
    assert updated.impressions == 20_000
    assert updated.orders == 55
    assert updated.meets_sample_gate is True
    # Ads were charged and revenue arrived; both are on the books.
    assert orch.cash.total_balance() != before
    assert orch.cash.by_category()["ad_spend"] == Decimal("-300.00")


def test_a_kill_trigger_pauses_ads_immediately_but_does_not_kill(orch):
    exp = launch(orch)
    orch.metrics = StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=200_000, clicks=400,  # CTR 0.2%
            sessions=500, orders=55, revenue=Decimal("1200"), ad_spend=Decimal("300"),
        )}
    )
    report = orch.tick()

    flagged = orch.ledger.get(exp.id)
    assert report.kill_triggered[0]["reason"] == "CTR too low"
    assert flagged.ads_paused is True  # autonomous — stop the bleeding
    assert flagged.status != Status.KILLED.value  # gated — not our decision
    assert flagged.pending_kill_reason == "CTR too low"
    assert orch.gate.find_pending(ApprovalAction.KILL_EXPERIMENT.value, exp.id) is not None


def test_the_human_kill_decision_is_what_kills(orch):
    exp = launch(orch)
    orch.metrics = StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=200_000, clicks=400, sessions=500, orders=55,
            revenue=Decimal("1200"), ad_spend=Decimal("300"),
        )}
    )
    orch.tick()
    approval = orch.gate.find_pending(ApprovalAction.KILL_EXPERIMENT.value, exp.id)
    orch.gate.approve(approval.id, "robbie")
    report = orch.tick()

    killed = orch.ledger.get(exp.id)
    assert exp.id in report.killed
    assert killed.status == Status.KILLED.value
    assert killed.kill_reason == "CTR too low"
    assert killed.killed_at is not None


def test_continue_keeps_the_experiment_alive_with_ads_still_paused(orch):
    exp = launch(orch)
    orch.metrics = StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=200_000, clicks=400, sessions=500, orders=55,
            revenue=Decimal("1200"), ad_spend=Decimal("300"),
        )}
    )
    orch.tick()
    approval = orch.gate.find_pending(ApprovalAction.KILL_EXPERIMENT.value, exp.id)
    orch.gate.reject(approval.id, "robbie", "seasonal creative, retesting")
    report = orch.tick()

    alive = orch.ledger.get(exp.id)
    assert exp.id in report.continued
    assert alive.status != Status.KILLED.value
    assert alive.pending_kill_reason is None
    assert alive.ads_paused is True


def test_an_undecided_kill_is_not_re_requested_every_tick(orch):
    exp = launch(orch)
    orch.metrics = StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=200_000, clicks=400, sessions=500, orders=55,
            revenue=Decimal("1200"), ad_spend=Decimal("300"),
        )}
    )
    orch.tick()
    orch.tick()
    orch.tick()
    kills = [p for p in orch.gate.pending() if p.action == ApprovalAction.KILL_EXPERIMENT.value]
    assert len(kills) == 1


def test_below_the_sample_gate_nothing_is_ever_killed(orch):
    """The false-positive guard: awful numbers, not enough of them."""
    exp = launch(orch)
    orch.metrics = StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=900, clicks=1, sessions=90, orders=5,
            revenue=Decimal("40"), ad_spend=Decimal("800"), refunds=3,
        )}
    )
    report = orch.tick()
    assert report.kill_triggered == []
    assert orch.ledger.get(exp.id).status != Status.KILLED.value
    assert orch.ledger.get(exp.id).ads_paused is False


# =========================================================================
# 5. SCALE
# =========================================================================
def test_a_healthy_experiment_gets_a_scale_proposal_not_a_scale(orch):
    exp = launch(orch)
    orch.metrics = StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=100_000, clicks=3_000, sessions=2_000, orders=60,
            revenue=Decimal("1500"), ad_spend=Decimal("400"),
        )}
    )
    report = orch.tick()
    assert report.scale_requests
    assert orch.ledger.get(exp.id).daily_ad_budget == Decimal("10.00")  # unchanged

    approval_id = report.scale_requests[0]["approval_id"]
    orch.gate.approve(approval_id, "robbie")
    orch.tick()
    assert orch.ledger.get(exp.id).daily_ad_budget == Decimal("20.00")


# =========================================================================
# 6. STOP CONDITION
# =========================================================================
def test_kill_all_fires_after_twenty_unprofitable_experiments(orch):
    for i in range(20):
        orch.ledger.create_experiment(
            make_experiment(
                product_id=f"loss-{i}", revenue=Decimal("100"), ad_spend=Decimal("300"),
                orders=5, unit_cost=Decimal("10"),
            )
        )
    report = orch.tick()
    assert report.stop_triggered is True
    assert "KILL_ALL" in report.stop_reason
    assert any(p.action == ApprovalAction.KILL_ALL.value for p in orch.gate.pending())


def test_kill_all_does_not_fire_while_the_store_makes_money(orch):
    for i in range(25):
        orch.ledger.create_experiment(
            make_experiment(product_id=f"win-{i}", revenue=Decimal("2400"), ad_spend=Decimal("600"))
        )
    report = orch.tick()
    assert report.stop_triggered is False


def test_kill_all_pauses_every_live_experiment(orch):
    exp = launch(orch)
    for i in range(20):
        orch.ledger.create_experiment(
            make_experiment(
                product_id=f"loss-{i}", revenue=Decimal("10"), ad_spend=Decimal("900"),
                orders=2, unit_cost=Decimal("10"),
            )
        )
    orch.tick()
    assert orch.ledger.get(exp.id).ads_paused is True


# =========================================================================
def test_tick_reports_alerts(orch):
    orch.cash.open_account()
    report = orch.tick()
    assert report.alerts
    assert any("Cash:" in str(a) for a in report.alerts)


# =========================================================================
# The CONTINUE override
# =========================================================================
def failing_metrics(exp):
    return StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=200_000, clicks=400,  # CTR 0.2%
            sessions=500, orders=55, revenue=Decimal("1200"), ad_spend=Decimal("300"),
        )}
    )


def test_continue_is_not_a_one_hour_snooze(orch):
    """After CONTINUE, the same trigger on the same data must not re-escalate
    every tick — that trains the operator to ignore the alerts."""
    exp = launch(orch)
    orch.metrics = failing_metrics(exp)
    orch.tick()
    approval = orch.gate.find_pending(ApprovalAction.KILL_EXPERIMENT.value, exp.id)
    orch.gate.reject(approval.id, "robbie", "new creative in flight")
    orch.tick()

    report = orch.tick()
    assert report.kill_triggered == []
    assert orch.ledger.get(exp.id).pending_kill_reason is None
    assert orch.gate.find_pending(ApprovalAction.KILL_EXPERIMENT.value, exp.id) is None


def test_a_different_kill_condition_still_escalates_after_an_override(orch):
    exp = launch(orch)
    orch.metrics = failing_metrics(exp)
    orch.tick()
    approval = orch.gate.find_pending(ApprovalAction.KILL_EXPERIMENT.value, exp.id)
    orch.gate.reject(approval.id, "robbie")
    orch.tick()

    # CTR recovers, refunds blow up instead.
    orch.metrics = StaticMetricsProvider(
        {exp.product_id: MetricsSnapshot(
            impressions=100_000, clicks=3_000, sessions=1_000, orders=60,
            revenue=Decimal("1500"), ad_spend=Decimal("400"), refunds=20,
        )}
    )
    report = orch.tick()
    assert report.kill_triggered[0]["reason"] == "Refund rate too high"


def test_a_stale_approval_cannot_restart_ads_the_system_paused(orch):
    """The pause is the system's one autonomous power over spend. An approval
    granted before the pause must not quietly undo it on the next tick."""
    exp = launch(orch)
    orch.metrics = failing_metrics(exp)
    orch.tick()
    assert orch.ledger.get(exp.id).ads_paused is True
    orch.tick()
    orch.tick()
    assert orch.ledger.get(exp.id).ads_paused is True
