"""Spec sections 8 and 9 — alerts, weekly report, success criteria, stop."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mvv.config import Config, StopConfig
from mvv.monitoring import HealthMonitor, format_alerts, format_weekly_summary

from .conftest import make_experiment


@pytest.fixture
def monitor(ledger, cash, config) -> HealthMonitor:
    return HealthMonitor(ledger, cash, config)


def test_health_check_reports_cash_first(monitor, cash):
    cash.open_account()
    alerts = monitor.daily_health_check()
    assert "Cash:" in str(alerts[0])


def test_negative_cash_is_critical(monitor, cash):
    cash.debit("ads", Decimal("50"), "ad_spend")
    alerts = monitor.daily_health_check()
    assert alerts[0].level == "critical"
    assert "CASH NEGATIVE" in str(alerts[0])


def test_buffer_breach_is_critical(monitor, cash):
    cash.open_account(Decimal("100"))  # below the $150 buffer
    alerts = monitor.daily_health_check()
    assert alerts[0].level == "critical"
    assert "buffer" in str(alerts[0])


def test_early_warning_when_approaching_the_gate(monitor, ledger, cash):
    cash.open_account()
    ledger.create_experiment(make_experiment(orders=25, sessions=90, impressions=900))
    warnings = [a for a in monitor.daily_health_check() if a.level == "warn"]
    assert warnings and "need 50" in str(warnings[0])


def test_collecting_data_is_informational_not_a_warning(monitor, ledger, cash):
    cash.open_account()
    ledger.create_experiment(make_experiment(orders=3, sessions=20, impressions=200))
    assert [a.level for a in monitor.daily_health_check()] == ["info", "info"]


def test_kill_trigger_shows_up_as_critical(monitor, ledger, cash):
    cash.open_account()
    ledger.create_experiment(make_experiment(impressions=200_000, clicks=100))
    criticals = [a for a in monitor.daily_health_check() if a.level == "critical"]
    assert "KILL trigger — CTR too low" in str(criticals[0])


def test_an_overridden_trigger_is_still_reported(monitor, ledger, cash):
    """CONTINUE stops the escalation, not the reporting."""
    cash.open_account()
    exp = ledger.create_experiment(make_experiment(impressions=200_000, clicks=100))
    ledger.flag_pending_kill(exp.id, "CTR too low")
    ledger.continue_experiment(exp.id)
    criticals = [a for a in monitor.daily_health_check() if a.level == "critical"]
    assert any("CTR too low" in str(a) for a in criticals)


def test_pending_decision_is_surfaced(monitor, ledger, cash):
    cash.open_account()
    exp = ledger.create_experiment(make_experiment())
    ledger.flag_pending_kill(exp.id, "Refund rate too high")
    assert any("awaiting human decision" in str(a) for a in monitor.daily_health_check())


def test_healthy_experiment_reports_its_margin(monitor, ledger, cash):
    cash.open_account()
    ledger.create_experiment(make_experiment())
    assert any("healthy" in str(a) for a in monitor.daily_health_check())


# -- weekly summary -------------------------------------------------------
def test_weekly_summary_counts_and_tallies(monitor, ledger, cash):
    cash.open_account()
    ledger.create_experiment(make_experiment(product_id="a"))
    b = ledger.create_experiment(make_experiment(product_id="b"))
    c = ledger.create_experiment(make_experiment(product_id="c"))
    ledger.kill(b.id, "CTR too low")
    ledger.kill(c.id, "CTR too low")

    summary = monitor.weekly_summary()
    assert summary["total_experiments"] == 3
    assert summary["active"] == 1
    assert summary["killed_total"] == 2
    assert summary["kill_reasons"] == {"CTR too low": 2}
    assert len(summary["killed_this_week"]) == 2
    assert "WEEKLY SUMMARY" in format_weekly_summary(summary)


def test_weekly_summary_of_an_empty_store(monitor):
    summary = monitor.weekly_summary()
    assert summary["total_experiments"] == 0
    assert format_weekly_summary(summary)  # renders without exploding


# -- success criteria (spec 9) -------------------------------------------
def test_success_criteria_track_the_phase_1_bar(monitor, ledger):
    for i in range(12):
        ledger.create_experiment(make_experiment(product_id=f"p{i}"))
    criteria = monitor.success_criteria()
    assert criteria["experiments_run"]["pass"] is True
    assert criteria["data_quality"]["value"] == Decimal("100.00")
    assert criteria["store_profitable"]["pass"] is True


def test_kill_accuracy_measures_whether_kills_were_right(monitor, ledger):
    # Two killed experiments that were genuinely losing money...
    for i in range(2):
        e = ledger.create_experiment(
            make_experiment(product_id=f"bad{i}", revenue=Decimal("100"), ad_spend=Decimal("900"))
        )
        ledger.kill(e.id, "CAC exceeds 50% of price")
    # ...and one killed while actually profitable (a false positive).
    good = ledger.create_experiment(make_experiment(product_id="good"))
    ledger.kill(good.id, "Delivery too slow")

    accuracy = monitor.success_criteria()["kill_accuracy"]
    assert accuracy["sample"] == 3
    assert accuracy["value"] == Decimal("66.67")
    assert accuracy["pass"] is False  # target is >70%


def test_data_quality_counts_experiments_that_reached_the_gate(monitor, ledger):
    ledger.create_experiment(make_experiment(product_id="full"))
    ledger.create_experiment(make_experiment(product_id="thin", orders=2, sessions=10, impressions=50))
    assert monitor.success_criteria()["data_quality"]["value"] == Decimal("50.00")


# -- stop condition (spec 9) ---------------------------------------------
def test_stop_condition_waits_for_twenty_experiments(monitor, ledger):
    for i in range(19):
        ledger.create_experiment(
            make_experiment(product_id=f"p{i}", revenue=Decimal("0"), ad_spend=Decimal("100"))
        )
    triggered, reason = monitor.check_stop_condition()
    assert triggered is False
    assert "19/20" in reason


def test_stop_condition_fires_on_an_unprofitable_store(monitor, ledger):
    for i in range(20):
        ledger.create_experiment(
            make_experiment(product_id=f"p{i}", revenue=Decimal("0"), ad_spend=Decimal("100"))
        )
    triggered, reason = monitor.check_stop_condition()
    assert triggered is True
    assert "KILL_ALL" in reason


def test_stop_condition_stays_quiet_when_the_store_works(monitor, ledger):
    for i in range(20):
        ledger.create_experiment(make_experiment(product_id=f"p{i}"))
    assert monitor.check_stop_condition()[0] is False


def test_stop_threshold_is_configurable(ledger, cash):
    config = Config(stop=StopConfig(kill_all_after_experiments=2))
    monitor = HealthMonitor(ledger, cash, config)
    for i in range(2):
        ledger.create_experiment(
            make_experiment(product_id=f"p{i}", revenue=Decimal("0"), ad_spend=Decimal("50"))
        )
    assert monitor.check_stop_condition()[0] is True


# -- scale candidates (spec 5.6) -----------------------------------------
def test_scale_candidates_need_the_gate_and_the_margin(monitor, ledger):
    # 50% contribution margin, gates met
    ledger.create_experiment(make_experiment(product_id="scale-me"))
    # good margin but not enough data
    ledger.create_experiment(make_experiment(product_id="too-early", orders=10, sessions=50, impressions=500))
    # enough data but thin margin: 2400 - 600 - 1750 = 50 -> ~2%
    ledger.create_experiment(make_experiment(product_id="thin", ad_spend=Decimal("1750"), selling_price=Decimal("400")))

    candidates = monitor.scale_candidates()
    assert [c["product"] for c in candidates] == ["Test Product"]
    assert candidates[0]["experiment_id"] == 1


def test_a_flagged_experiment_is_never_a_scale_candidate(monitor, ledger):
    exp = ledger.create_experiment(make_experiment())
    ledger.flag_pending_kill(exp.id, "Refund rate too high")
    assert monitor.scale_candidates() == []


def test_format_alerts_handles_nothing(monitor):
    assert format_alerts([]) == "no alerts"
