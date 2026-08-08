"""Indicators — including what they do when there is not enough data."""

from __future__ import annotations

from decimal import Decimal

from src.money import D
from src.trading.indicators import (
    drawdown_pct,
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
    assert sharpe(series("0.01", "0.01", "0.01")) is None
    assert sharpe(series("0.01")) is None
    result = sharpe(series("0.01", "-0.005", "0.02", "0.001"))
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


def test_win_rate_ignores_flat_trades_and_returns_none_when_empty():
    assert win_rate_pct(series(1, -1, 1, 1)) == D("75.00")
    assert win_rate_pct(series(0, 0)) is None
    assert win_rate_pct([]) is None
