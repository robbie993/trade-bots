"""Indicators — including what they do when there is not enough data."""

from __future__ import annotations

from decimal import Decimal

from src.money import D
from src.trading.indicators import (
    average_range,
    drawdown_pct,
    ibs,
    max_drawdown_pct,
    momentum_pct,
    rsi,
    sharpe,
    sma,
    stdev,
    volatility_pct,
    win_rate_pct,
    zscore,
)


def series(*values):
    return [D(v) for v in values]


def test_sma_needs_a_full_window():
    assert sma(series(1, 2, 3), 5) is None
    assert sma(series(1, 2, 3), 3) == D(2)


def test_momentum_is_a_percentage_change():
    assert momentum_pct(series(100, 105, 110), 2) == D("10.00")
    assert momentum_pct(series(100), 2) is None
    # A zero starting price is undefined, not a 100% move.
    assert momentum_pct(series(0, 5, 10), 2) is None


def test_rsi_degenerate_cases_are_explicit():
    rising = series(*range(1, 20))
    assert rsi(rising, 14) == D(100)
    falling = series(*range(20, 1, -1))
    assert rsi(falling, 14) == D(0)
    flat = series(*([5] * 20))
    assert rsi(flat, 14) == D(50)
    assert rsi(series(1, 2), 14) is None


def test_stdev_and_zscore_need_two_points():
    assert stdev(series(1)) is None
    assert zscore(series(1)) is None
    assert stdev(series(2, 4, 4, 4, 5, 5, 7, 9)).quantize(D("0.01")) == D("2.14")
    # A flat series has no dispersion, so a z-score is undefined rather than 0.
    assert zscore(series(3, 3, 3)) is None


def test_sharpe_is_none_without_dispersion():
    from src.trading.indicators import MIN_RETURNS

    # Flat series: no dispersion to divide by, at any length.
    assert sharpe(series(*(["0.01"] * MIN_RETURNS))) is None
    assert sharpe(series("0.01")) is None
    # With dispersion *and* enough observations to be a sample, it measures.
    # This used to pass four returns, which is below `MIN_RETURNS`: a two- or
    # four-point Sharpe is an arithmetic accident rather than a measurement,
    # and at two points with one zero it has a fixed point of ±1/sqrt(2) that
    # paused the best firm in the village. See the sample-gate test in
    # test_trading_resolution.py.
    varied = series(*(["0.01", "-0.005", "0.02", "0.001"] * 6))
    assert len(varied) >= MIN_RETURNS
    result = sharpe(varied)
    assert result is not None and isinstance(result, Decimal)


def test_volatility_is_annualised():
    daily = series("0.01", "-0.01", "0.02", "-0.02")
    assert volatility_pct(daily) > D(10)
    assert volatility_pct(series("0.01")) is None


def test_max_drawdown_finds_the_worst_peak_to_trough():
    assert max_drawdown_pct(series(100, 120, 60, 80)) == D("50.00")
    assert max_drawdown_pct(series(100, 110, 120)) == D("0.00")
    assert max_drawdown_pct([]) == D("0.00")


def test_drawdown_against_a_high_water_mark():
    assert drawdown_pct(D(80), D(100)) == D("20.00")
    # Above the mark is not a negative drawdown.
    assert drawdown_pct(D(120), D(100)) == D("0.00")
    assert drawdown_pct(D(80), D(0)) == D("0")


def test_a_fall_of_more_than_everything_is_capped_at_100():
    """Negative equity used to print drawdowns in the millions of percent.

    `firm_i_memecoins_ii` carries -$3,749.62 against a near-zero high-water
    mark, and `trade live-status` printed `Drawdown 2304568.75` in a column
    headed `<= 10.0%`. The formula is only bounded by 100 while equity stays
    non-negative, and a wound-up firm's does not.
    """
    # Total loss is 100%, and so is worse than total.
    assert drawdown_pct(D(0), D(100)) == D("100.00")
    assert drawdown_pct(D(-3749), D("0.16")) == D("100.00")
    assert drawdown_pct(D(-1), D(100)) == D("100.00")
    # The ordinary range is untouched.
    assert drawdown_pct(D(80), D(100)) == D("20.00")
    assert drawdown_pct(D(1), D(100)) == D("99.00")


def test_win_rate_ignores_flat_trades_and_returns_none_when_empty():
    assert win_rate_pct(series(1, -1, 1, 1)) == D("75.00")
    assert win_rate_pct(series(0, 0)) is None
    assert win_rate_pct([]) is None


def test_ibs_places_the_close_within_the_bars_own_range():
    # Closed at the high: maximum strength.
    assert ibs(D(110), D(100), D(110)) == D(1)
    # Closed at the low: minimum strength.
    assert ibs(D(110), D(100), D(100)) == D(0)
    # Closed in the middle.
    assert ibs(D(110), D(100), D(105)) == D("0.5")
    # No range to place the close within.
    assert ibs(D(100), D(100), D(100)) is None
    assert ibs(D(99), D(100), D(99)) is None  # a malformed bar, high < low


def test_average_range_mirrors_veritas_bots_avg_rng25():
    highs = series(102, 104, 103)
    lows = series(100, 101, 99)
    # (2 + 3 + 4) / 3
    assert average_range(highs, lows, 3) == D(3)
    assert average_range(highs, lows, 5) is None  # not enough bars
    assert average_range([], [], 0) is None


def test_average_range_rejects_an_inverted_bar():
    # A high below its own low is not data the average should silently eat.
    assert average_range(series(100), series(105), 1) is None
