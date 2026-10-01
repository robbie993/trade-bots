"""Scratches: a trade that closed within its own costs of breakeven.

Decided 2026-10-01. The take-profit firm's breakeven stop sells a winner that
has come back to what it cost, and every such sale books at least the exit's
spread below zero. Counted as a loss, a firm doing exactly what it was built
to do was walking toward the six-in-a-row kill one flat trade at a time, and
its comparison with its control twin would have ended with it.

So a scratch is neither a win nor a loss: not in the losing streak (where it
neither adds to the run nor ends it), not in the win rate, and not in the
sample the win rate is judged on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from src.money import D
from src.trading.backtest import BacktestResult, Backtester
from src.trading.brain.evolver import BASE_GENOME
from src.trading.brokerage.evaluator import Evaluator, Scorecard, _consecutive_losing_bars
from src.trading.config import DataConfig, TradingConfig
from src.trading.court.jury import Jury
from src.trading.data.market_data import MarketData
from src.trading.firms.kill_switch import (
    FirmMetrics,
    kill_check_table,
    meets_win_rate_sample,
    should_kill_firm,
)
from src.trading.indicators import is_scratch, round_trip_cost, win_rate_pct
from src.trading.models import Fill, Side
from src.trading.resolution import parse

BAR = parse("15m")
OPEN = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)


def close(bar: int, pnl: str, fee: str = "1.00", slippage: str = "0.25"):
    """A closing fill in the given 15m bar. Its round trip costs $2.50."""
    return SimpleNamespace(realized_pnl=D(pnl), fee=D(fee), slippage=D(slippage),
                           as_of=OPEN + timedelta(minutes=15 * bar))


def fill_for(firm, side=Side.BUY, quantity="10", price="100", fee="1"):
    return Fill(firm_id=firm.id, symbol="SPY", side=side.value,
                quantity=quantity, price=price, fee=fee)


# =========================================================================
# the band
# =========================================================================
def test_a_round_trip_costs_twice_its_closing_leg():
    assert round_trip_cost(D("1.00"), D("0.25")) == D("2.50")
    assert round_trip_cost(None, None) == D("0.00")


def test_a_scratch_is_anything_within_its_costs_either_way():
    assert is_scratch(D("-2.50"), D("2.50"))
    assert is_scratch(D("2.50"), D("2.50"))
    assert is_scratch(D("0"), D("2.50"))
    assert not is_scratch(D("-2.51"), D("2.50"))
    assert not is_scratch(D("2.51"), D("2.50"))


def test_with_no_costs_recorded_nothing_changes():
    """A venue that records no fee or spread leaves every close decided, as
    it always was."""
    assert not is_scratch(D("-0.01"), round_trip_cost(D("0"), D("0")))


# =========================================================================
# the losing streak
# =========================================================================
def test_a_breakeven_exit_neither_adds_to_a_losing_run_nor_ends_it():
    fills = [close(0, "-40"), close(1, "-35"), close(2, "-1.20"), close(3, "-50")]
    assert _consecutive_losing_bars(fills, BAR) == 3


def test_a_win_smaller_than_its_costs_does_not_end_the_run():
    fills = [close(0, "-40"), close(1, "1.00"), close(2, "-50")]
    assert _consecutive_losing_bars(fills, BAR) == 2


def test_a_real_win_still_ends_the_run():
    fills = [close(0, "-40"), close(1, "30"), close(2, "-50")]
    assert _consecutive_losing_bars(fills, BAR) == 1


def test_a_loss_past_its_costs_is_still_a_loss():
    assert _consecutive_losing_bars([close(0, "-2.51")], BAR) == 1
    assert _consecutive_losing_bars([close(0, "-2.50")], BAR) == 0


def test_a_bar_is_judged_on_what_it_netted():
    """Two closes in one bar net to one result, and the band is both of
    their costs: -$30 and +$29 is a dollar down against $5 of costs."""
    fills = [close(0, "-40"), close(1, "-30"), close(1, "29")]
    assert _consecutive_losing_bars(fills, BAR) == 1


def test_the_take_profit_firm_is_not_killed_for_standing_still():
    """Five losses with a breakeven stop among them is five, not six."""
    fills = [close(0, "-40"), close(1, "-35"), close(2, "-60"), close(3, "-0.40"),
             close(4, "-45"), close(5, "-30")]
    run = _consecutive_losing_bars(fills, BAR)
    assert run == 5
    assert should_kill_firm(FirmMetrics(trades=6, consecutive_losses=run))[0] is False
    # What counting the breakeven exit as a loss used to do:
    assert should_kill_firm(FirmMetrics(trades=6, consecutive_losses=run + 1))[0] is True


# =========================================================================
# the win rate, measured
# =========================================================================
def test_the_win_rate_leaves_scratches_out(store, firm_record, market, trading_config):
    market.seek(150)
    store.settle(firm_record, fill_for(firm_record))
    for price in ("110", "90", "99.50"):          # +$20, -$20, and -$1 on $2 of costs
        firm = store.require_firm_by_id(firm_record.id)
        store.settle(firm, fill_for(firm, side=Side.SELL, quantity="2", price=price))

    card = Evaluator(store, trading_config).evaluate(
        store.require_firm_by_id(firm_record.id), market)
    assert card.closed_trades == 3
    assert card.decided_trades == 2
    assert card.win_rate_pct == D("50.00")        # was 33.33, the scratch read as a loss
    assert card.to_metrics().decided_trades == 2


def test_the_stored_counter_skips_a_scratch_too(store, firm_record):
    store.settle(firm_record, fill_for(firm_record))
    firm = store.require_firm_by_id(firm_record.id)
    store.settle(firm, fill_for(firm, side=Side.SELL, quantity="5", price="90"))
    assert store.require_firm_by_id(firm_record.id).consecutive_losses == 1

    firm = store.require_firm_by_id(firm_record.id)
    store.settle(firm, fill_for(firm, side=Side.SELL, quantity="2", price="99.80"))
    assert store.require_firm_by_id(firm_record.id).consecutive_losses == 1

    firm = store.require_firm_by_id(firm_record.id)
    store.settle(firm, fill_for(firm, side=Side.SELL, quantity="3", price="120"))
    assert store.require_firm_by_id(firm_record.id).consecutive_losses == 0


# =========================================================================
# the win rate, judged
# =========================================================================
def metrics(**overrides) -> FirmMetrics:
    values = dict(trades=25, win_rate_pct=D("10"), sharpe=D("1.2"),
                  drawdown_pct=D("5"), worst_trade_pct=D("2"), consecutive_losses=1)
    values.update(overrides)
    return FirmMetrics(**values)


def test_the_win_rate_waits_for_twenty_trades_won_or_lost():
    """Twenty-five closes of which nineteen were decided is not yet a sample."""
    assert not meets_win_rate_sample(metrics(decided_trades=19))
    assert should_kill_firm(metrics(decided_trades=19)) == (False, None)
    killed, why = should_kill_firm(metrics(decided_trades=20))
    assert killed and "Win rate" in why


def test_without_a_decided_count_the_closed_count_stands():
    """The court's old results and anything else that does not count
    scratches apart are judged exactly as before."""
    killed, why = should_kill_firm(metrics(decided_trades=None))
    assert killed and "Win rate" in why


def test_the_check_table_agrees_with_the_switch():
    def fired(m):
        return next(r for r in kill_check_table(m) if r["condition"] == "Win rate")["triggered"]

    assert fired(metrics(decided_trades=19)) is False
    assert fired(metrics(decided_trades=20)) is True


def test_the_score_leaves_out_a_win_rate_measured_on_too_few(store, trading_config):
    evaluator = Evaluator(store, trading_config)
    few = Scorecard(firm_key="x", firm_id=1, win_rate_pct=D("90"), sufficient_data=True,
                    closed_trades=30, decided_trades=5)
    enough = Scorecard(firm_key="x", firm_id=1, win_rate_pct=D("90"), sufficient_data=True,
                       closed_trades=30, decided_trades=20)
    assert "win_rate" not in evaluator.score(few)[1]
    assert "win_rate" in evaluator.score(enough)[1]


# =========================================================================
# the court asks the same question
# =========================================================================
def test_the_court_judges_a_backtest_by_the_same_rule(trading_config):
    jury = Jury(trading_config)
    flat = BacktestResult(firm_key="x", bars=100, closed_trades=30, decided_trades=4,
                          win_rate_pct=D("0"), max_drawdown_pct=D("2"), sharpe=D("1"))
    assert jury._kill_criteria(flat).is_for
    flat.decided_trades = 30
    assert jury._kill_criteria(flat).is_against


def test_a_backtest_with_no_costs_decides_every_close(feed, tmp_path):
    free = TradingConfig(
        firms_config=tmp_path / "firms.yaml", audit_vault=tmp_path / "vault",
        vendor_dir=tmp_path / "vendor",
        data=DataConfig(source="synthetic", seed=12345, history_days=180,
                        slippage_bps=Decimal("0"), fee_bps=Decimal("0")),
    )
    backtester = Backtester(free)
    result = backtester.run("x", ["SPY", "QQQ"], MarketData(feed, ["SPY", "QQQ"]),
                            genome=BASE_GENOME)
    assert result.closed_trades > 0
    assert result.decided_trades == result.closed_trades
    assert result.win_rate_pct == win_rate_pct(backtester.last_realized)


def test_a_backtest_with_costs_never_decides_more_than_it_closed(feed, trading_config):
    result = Backtester(trading_config).run(
        "x", ["SPY", "QQQ"], MarketData(feed, ["SPY", "QQQ"]), genome=BASE_GENOME)
    assert 0 <= result.decided_trades <= result.closed_trades
