"""The firm kill switch — the trading edition of tests/test_kill_criteria.py.

Two properties matter more than any individual threshold:

1. Statistical judgements wait for the sample gate; the account emptying does
   not. A 40% drawdown is a kill on trade three.
2. The reason attached to a kill is reproducible from the stored metrics.
"""

from __future__ import annotations

from src.money import D
from src.trading.config import FirmKillConfig
from src.trading.firms.kill_switch import (
    INSUFFICIENT_DATA,
    FirmMetrics,
    KillSwitch,
    kill_check_table,
    meets_sample_gate,
    should_kill_firm,
)


def healthy(**overrides) -> FirmMetrics:
    defaults = dict(
        trades=50,
        drawdown_pct=D("5"),
        win_rate_pct=D("55"),
        sharpe=D("1.2"),
        worst_trade_pct=D("2"),
        consecutive_losses=1,
    )
    defaults.update(overrides)
    return FirmMetrics(**defaults)


def test_a_healthy_firm_is_not_killed():
    assert should_kill_firm(healthy()) == (False, None)


def test_below_the_sample_gate_there_is_no_verdict():
    should, reason = should_kill_firm(healthy(trades=3, win_rate_pct=D("0"), sharpe=D("-2")))
    assert should is False
    assert reason == INSUFFICIENT_DATA


def test_drawdown_kills_before_the_sample_gate():
    should, reason = should_kill_firm(healthy(trades=2, drawdown_pct=D("45")))
    assert should is True
    assert "Drawdown" in reason


def test_a_single_catastrophic_loss_kills_immediately():
    should, reason = should_kill_firm(healthy(trades=1, worst_trade_pct=D("25")))
    assert should is True
    assert "Single trade" in reason


def test_a_losing_streak_kills_immediately():
    should, reason = should_kill_firm(healthy(trades=6, consecutive_losses=9))
    assert should is True
    assert "consecutive" in reason


def test_win_rate_and_sharpe_only_apply_above_the_gate():
    assert should_kill_firm(healthy(trades=5, win_rate_pct=D("10")))[1] == INSUFFICIENT_DATA
    should, reason = should_kill_firm(healthy(win_rate_pct=D("10")))
    assert should and "Win rate" in reason
    should, reason = should_kill_firm(healthy(sharpe=D("0.1")))
    assert should and "Sharpe" in reason


def test_missing_statistics_are_not_treated_as_failures():
    assert should_kill_firm(healthy(win_rate_pct=None, sharpe=None)) == (False, None)


def test_conditions_are_evaluated_in_a_fixed_order():
    # Every condition trips at once; drawdown is the one that must be reported.
    everything = FirmMetrics(
        trades=50,
        drawdown_pct=D("90"),
        win_rate_pct=D("0"),
        sharpe=D("-3"),
        worst_trade_pct=D("50"),
        consecutive_losses=20,
    )
    assert "Drawdown" in should_kill_firm(everything)[1]


def test_thresholds_are_configurable():
    strict = FirmKillConfig(max_drawdown_pct=D("1"))
    assert should_kill_firm(healthy(drawdown_pct=D("2")), strict)[0] is True
    assert should_kill_firm(healthy(drawdown_pct=D("2")))[0] is False


def test_the_check_table_shows_every_condition():
    table = kill_check_table(healthy(win_rate_pct=D("10")))
    conditions = [row["condition"] for row in table]
    assert conditions == [
        "Drawdown",
        "Worst single trade",
        "Consecutive losses",
        "Win rate",
        "Sharpe",
        "Trades (sample gate)",
    ]
    assert [row["triggered"] for row in table] == [False, False, False, True, False, False]


def test_the_check_table_does_not_trigger_below_the_gate():
    table = kill_check_table(healthy(trades=2, win_rate_pct=D("5"), sharpe=D("-9")))
    assert not any(row["triggered"] for row in table)


def test_the_check_table_renders_missing_values():
    table = kill_check_table(healthy(sharpe=None))
    assert next(r for r in table if r["condition"] == "Sharpe")["value"] == "—"


def test_the_switch_reports_but_never_kills():
    switch = KillSwitch()
    result = switch.trigger("firm_a", "Drawdown too deep")
    assert result["status"] == "triggered"
    assert "paused" in result["action"]
    assert "human approval" in result["action"]
    assert switch.triggered == [result]


def test_the_switch_lists_its_conditions():
    conditions = KillSwitch().conditions
    assert len(conditions) == 5
    assert any("drawdown" in c for c in conditions)
    assert any("consecutive_losses" in c for c in conditions)


def test_sample_gate_helper():
    assert meets_sample_gate(healthy(trades=20)) is True
    assert meets_sample_gate(healthy(trades=19)) is False
