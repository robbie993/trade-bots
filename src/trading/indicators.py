"""Indicators and performance statistics — pure functions over Decimal.

No database, no clock, no network, in the spirit of ``src/kill_criteria.py``:
these numbers decide whether money moves, so they are kept small enough to
read in one sitting and are covered directly by tests.

Everything returns ``None`` rather than a zero when there is not enough data.
A missing indicator is not a neutral indicator — reading an absent RSI as 50
is how a system talks itself into a trade it has no evidence for.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from ..money import D, ZERO, percent

TRADING_DAYS = 252


def sma(values: Sequence[Decimal], window: int) -> Optional[Decimal]:
    if window <= 0 or len(values) < window:
        return None
    chunk = [D(v) for v in values[-window:]]
    return sum(chunk, ZERO) / D(window)


def momentum_pct(values: Sequence[Decimal], window: int) -> Optional[Decimal]:
    """Percentage change over the window."""
    if len(values) <= window or window <= 0:
        return None
    start, end = D(values[-window - 1]), D(values[-1])
    if start == 0:
        return None
    return percent((end - start) / start * D(100))


def rsi(values: Sequence[Decimal], window: int = 14) -> Optional[Decimal]:
    """Wilder's RSI, computed with a simple average over the window.

    Returns 100 when there were no losses in the window (and 0 for no gains)
    instead of dividing by zero — the classic degenerate cases, made explicit.
    """
    if len(values) < window + 1 or window <= 0:
        return None
    gains, losses = ZERO, ZERO
    for previous, current in zip(values[-window - 1 :], values[-window:]):
        change = D(current) - D(previous)
        if change >= 0:
            gains += change
        else:
            losses += -change
    if losses == 0:
        return D(100) if gains > 0 else D(50)
    if gains == 0:
        return ZERO
    rs = (gains / D(window)) / (losses / D(window))
    return percent(D(100) - (D(100) / (D(1) + rs)))


def ibs(high: Decimal, low: Decimal, close: Decimal) -> Optional[Decimal]:
    """Internal Bar Strength: where the close sits in the bar's own range.

    0 is the low, 1 is the high. This is the published Connors definition read
    directly off one bar's high/low/close — no averaging window, no proxy.
    `None` when the bar has no range to place the close within (high == low).
    """
    h, l, c = D(high), D(low), D(close)
    if h <= l:
        return None
    return ((c - l) / (h - l)).quantize(D("0.0001"))


def average_range(highs: Sequence[Decimal], lows: Sequence[Decimal], window: int) -> Optional[Decimal]:
    """Mean high-low range over the trailing window.

    This mirrors ``veritas_bot.py``'s own ``avg_rng25`` exactly — the simple
    per-bar range, not Wilder's true range (which also charges for a gap from
    the prior close). VERITAS was pre-registered and passed its five criteria
    against this definition; substituting true ATR would be a different number
    sitting behind the same gene name, which is the "different strategy
    wearing a validated strategy's name" mistake this repository keeps
    re-discovering (see ``bots/veritas_reversion.py``).
    """
    if window <= 0 or len(highs) < window or len(lows) < window:
        return None
    h_chunk, l_chunk = highs[-window:], lows[-window:]
    ranges = [D(h) - D(l) for h, l in zip(h_chunk, l_chunk)]
    if any(r < 0 for r in ranges):
        return None
    return sum(ranges, ZERO) / D(window)


def stdev(values: Sequence[Decimal]) -> Optional[Decimal]:
    """Sample standard deviation, via Decimal's sqrt (no float round-trip)."""
    if len(values) < 2:
        return None
    nums = [D(v) for v in values]
    mean = sum(nums, ZERO) / D(len(nums))
    variance = sum(((v - mean) ** 2 for v in nums), ZERO) / D(len(nums) - 1)
    return variance.sqrt()


def volatility_pct(returns: Sequence[Decimal],
                   periods_per_year: Decimal = D(TRADING_DAYS)) -> Optional[Decimal]:
    """Annualised volatility of period returns, in percent.

    `periods_per_year` is how many of these observations a year holds. It is a
    parameter rather than a constant because the caller is the only thing that
    knows how long a bar is — see src/trading/resolution.py.
    """
    sd = stdev(returns)
    if sd is None:
        return None
    per_year = D(periods_per_year)
    if per_year <= 0:
        return None
    return percent(sd * per_year.sqrt() * D(100))


#: Returns needed before a Sharpe ratio is a measurement rather than an
#: arithmetic accident.
#:
#: **Two is not a sample, and at two the ratio has a fixed point.** For any
#: two-return series where one return is zero, ``mean / sd`` is exactly
#: ±1/√2 — so at 15m bars the answer is ±57.2364 whatever the other return
#: was. A move of one billionth of a percent produces the same number as a
#: move of fifty percent. It encodes the *sign* of a single bar and nothing
#: else.
#:
#: That is not hypothetical. On 2026-09-01 five unrelated firms carried
#: |57.2364| at once, and the kill switch paused ``firm_d_value`` — up
#: $38,413 on $80,000, the best desk in the village — because its unrealised
#: P&L moved -0.0069% in one bar and the next bar was flat. This is the third
#: time this repository has killed a firm with a units-or-sample artifact,
#: after the per-tick annualisation and the per-fill loss counter.
#:
#: Twenty matches ``FirmKillConfig.minimum_trades``: the system already
#: refuses to judge a firm on fewer than twenty trades, and judging it on
#: fewer than twenty observations is the same claim about the same evidence.
MIN_RETURNS = 20


def sharpe(returns: Sequence[Decimal], risk_free_annual: Decimal = ZERO,
           periods_per_year: Decimal = D(TRADING_DAYS)) -> Optional[Decimal]:
    """Annualised Sharpe ratio. None when there is no dispersion to divide by.

    **`periods_per_year` has to match the spacing of `returns`.** Getting it
    wrong is not a rounding error, it is a scale error: annualising hourly
    observations as though they were daily overstates the ratio by root-6.5,
    and this ratio is read by the kill switch. The default is daily because
    that is what every caller meant before bars could be anything else.
    """
    if len(returns) < MIN_RETURNS:
        return None
    nums = [D(r) for r in returns]
    mean = sum(nums, ZERO) / D(len(nums))
    sd = stdev(nums)
    if sd is None or sd == 0:
        return None
    per_year = D(periods_per_year)
    if per_year <= 0:
        return None
    period_rf = D(risk_free_annual) / per_year
    return ((mean - period_rf) / sd * per_year.sqrt()).quantize(D("0.0001"))


def max_drawdown_pct(equity_curve: Sequence[Decimal]) -> Decimal:
    """Worst peak-to-trough fall, as a positive percentage."""
    peak = None
    worst = ZERO
    for value in equity_curve:
        v = D(value)
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            fall = (peak - v) / peak * D(100)
            worst = max(worst, fall)
    return percent(worst)


def drawdown_pct(current: Decimal, high_water_mark: Decimal) -> Decimal:
    """Current fall from the high-water mark, as a positive percentage.

    **Capped at 100, because a fall of more than everything is not a fall.**
    The formula is bounded by 100 only while equity stays non-negative, and a
    wound-up firm's equity does not: `firm_i_memecoins_ii` carries -$3,749.62
    against a near-zero high-water mark and this reported a drawdown of
    **2,304,568.75%**, which `trade live-status` then printed in a column
    beside "<= 10.0%" as though it were a measurement.

    Nothing decided differently — every thresholdreading this sits far below
    100 (promotion 10, the kill switch 20-40, the council's juror 5 and 20,
    and the score caps it at 50), so 100 and 2.3 million both fail everything
    they touch. The cap changes no decision anywhere. What it changes is
    whether a human reading a kill reason is looking at a number or at a
    division artefact, and "Drawdown 2304568.75% exceeds 20%" is not a reason,
    it is noise wearing one.

    A firm that owes more than it holds reads as 100% down, which is the
    honest summary of "the stake is gone".
    """
    hwm = D(high_water_mark)
    if hwm <= 0:
        return ZERO
    fall = (hwm - D(current)) / hwm * D(100)
    return percent(min(D(100), max(ZERO, fall)))


def win_rate_pct(pnls: Sequence[Decimal]) -> Optional[Decimal]:
    """Share of closed trades that made money. None with no closed trades."""
    closed = [D(p) for p in pnls if D(p) != 0]
    if not closed:
        return None
    wins = sum(1 for p in closed if p > 0)
    return percent(D(wins) / D(len(closed)) * D(100))


def zscore(values: Sequence[Decimal]) -> Optional[Decimal]:
    """How far the last value sits from the mean, in standard deviations."""
    if len(values) < 2:
        return None
    nums = [D(v) for v in values]
    mean = sum(nums, ZERO) / D(len(nums))
    sd = stdev(nums)
    if sd is None or sd == 0:
        return None
    return ((nums[-1] - mean) / sd).quantize(D("0.0001"))


__all__ = [
    "average_range",
    "drawdown_pct",
    "ibs",
    "max_drawdown_pct",
    "momentum_pct",
    "rsi",
    "sharpe",
    "sma",
    "stdev",
    "volatility_pct",
    "win_rate_pct",
    "zscore",
]
