"""VERITAS — RSI(2) + IBS mean reversion, gated by a long-run regime filter.

Ported from `veritas_bot.py` in the sibling repo, where it passed all five
pre-registered criteria on 2026-07-28. It is here rather than a fresh idea for
one reason: it is the only strategy in this project that has ever been declared
in advance and then passed its own test. Everything else invented since has
been a null — the scanner tied a random control, Kronos returned four nulls,
short-horizon crypto TA came back an anti-edge, and the cross-sectional
momentum file submitted to this court an hour ago lost 3.57% with no gross
profit before costs.

**Every constant comes from published sources.** RSI(2) < 10 is Connors. The
IBS < 0.3 rule and the 2.5x average-range pullback come from the same
literature. The SMA200 regime gate is Connors again, and it exists because the
ungated version held a losing trade for 925 days through 2008. None of these
were fitted by the author, here or in the original, and changing them to make a
backtest look better would destroy the only property that makes this worth
running.

**The exit refuses the win-rate dial.** Close above SMA5, or a ten-day time
stop, or the regime breaks. No profit target and no tight stop, because both
are ways to buy a flattering win rate with a worse expectancy — which is
exactly what the Tritonix audit found being sold.

## The honest problem with running it here

VERITAS is a **daily** strategy. SMA200 means two hundred daily closes; the ten
day stop means ten sessions. This village runs on fifteen-minute bars and keeps
720 of them, which is 27 trading days — so a faithful SMA200 needs roughly
5,200 bars and it does not have them.

The tempting move is to compute "SMA200" over 200 fifteen-minute bars and call
it the same rule. That is 50 hours, not 200 days, and it is a different
strategy wearing a validated strategy's name — the same unit confusion that has
produced nine separate bugs in this codebase. So the windows below are stated
in **bars** with the conversion written down, and the strategy **returns
nothing at all** when the history cannot support them rather than computing a
number it should not trust.

To run this as designed, either set `TRADE_BAR=1d`, or raise
`TRADE_HISTORY_DAYS` until `closes()` can return `REGIME_BARS`. Until then it
will decline, and declining is the correct output.
"""

import os
from decimal import Decimal

#: Bars per trading day at the resolution this is configured for. 26 is a
#: 6.5-hour session in fifteen-minute bars; 1 is a daily village.
#: Declared rather than inferred because the strategy context deliberately does
#: not expose the village's resolution, and guessing it is how a window silently
#: changes meaning.
#:
#: Read from the environment so one file can serve both villages, but it is
#: still *declared* — somebody sets it per instance, and a village that says
#: nothing gets the intraday default it has always had. A daily instance sets
#: ``VERITAS_BARS_PER_DAY=1``, which is the resolution this strategy was
#: validated at and the only one where its windows mean what their names say.
BARS_PER_DAY = int(os.environ.get("VERITAS_BARS_PER_DAY", "26"))

REGIME_BARS = 200 * BARS_PER_DAY     # SMA200, in days
EXIT_SMA_BARS = 5 * BARS_PER_DAY     # SMA5
TIME_STOP_BARS = 10 * BARS_PER_DAY   # ten trading days
RSI_BARS = 2 * BARS_PER_DAY          # RSI(2)
RANGE_BARS = 25 * BARS_PER_DAY       # 25-day average range
HIGH_BARS = 10 * BARS_PER_DAY        # 10-day high

GENOME = {
    "rsi_entry": 10.0,
    "ibs_entry": 0.3,
    "pullback_atr": 2.5,
    "max_positions": 4,
    "bars_per_day": 26,
}

UNIVERSE = ["SPY", "QQQ", "IWM", "DIA"]

#: The seat that reads rsi_entry/ibs_entry/pullback_atr. Declared rather than
#: left to the court's default, for the same reason the file was convicted
#: the first time: the default three analysts (technical/sentiment/macro)
#: do not read this genome at all, so a backtest run without this line scores
#: a different strategy and calls it VERITAS's verdict.
ANALYSTS = ("reversion",)


def _sma(values, n):
    if not values or len(values) < n:
        return None
    window = values[-n:]
    return sum(window) / Decimal(len(window))


def _rsi(closes, n):
    """Wilder's RSI. None when there is not enough history to compute it."""
    if len(closes) < n + 1:
        return None
    gains, losses = Decimal(0), Decimal(0)
    for before, after in zip(closes[-(n + 1):], closes[-n:]):
        move = after - before
        if move > 0:
            gains += move
        else:
            losses -= move
    if losses == 0:
        return Decimal(100)
    rs = (gains / Decimal(n)) / (losses / Decimal(n))
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))


def _ibs(high, low, close):
    """Internal Bar Strength: where the close sits inside its own bar.

    ``(close - low) / (high - low)``, which is the published rule and the one
    the pre-registration was written against.

    **This used to be a proxy, and the proxy is why the strategy could not run
    at the resolution it was designed for.** The context served closes and
    nothing else, so the old version stood the recent *close* range in for the
    bar's own range — declared, but a different calculation. It survived on
    fifteen-minute bars because 26 intraday closes make a plausible-looking
    pseudo-range. On the daily clock this strategy was actually validated at,
    ``BARS_PER_DAY`` is 1, the window became ``closes[-1:]``, high equalled low,
    and it returned ``None`` for every symbol on every bar — so the conjunction
    could never be true and VERITAS could never open a position. It failed
    silently and looked exactly like a quiet market.

    The context now carries highs and lows, so the proxy is gone and this is
    the rule as published. A bar with no range still returns ``None``: a close
    that is also the high and the low says nothing about where it sat.
    """
    if high is None or low is None or close is None:
        return None
    if high == low:
        return None
    return (close - low) / (high - low)


def propose(context):
    orders = []
    held = [s for s in context.universe if context.quantity(s) > 0]

    for symbol in context.universe:
        closes = context.closes(symbol, REGIME_BARS)

        # **Refuse rather than approximate.** Without REGIME_BARS of history
        # there is no SMA200, and a shorter average is a different rule.
        if not closes or len(closes) < REGIME_BARS:
            continue

        price = closes[-1]
        regime = _sma(closes, REGIME_BARS)
        exit_sma = _sma(closes, EXIT_SMA_BARS)
        if regime is None or exit_sma is None or price <= 0:
            continue

        quantity = context.quantity(symbol)

        # -- exits, first and unconditionally -------------------------------
        # Above SMA5, or the regime broke. The ten-day time stop lives in the
        # village's own stop machinery rather than here, because a strategy
        # that counts its own holding period needs state it is not given.
        if quantity > 0:
            if price > exit_sma or price < regime:
                orders.append({
                    "symbol": symbol,
                    "side": "sell",
                    "quantity": quantity,
                    "rationale": ("closed above SMA5" if price > exit_sma
                                  else "regime broke: close below SMA200"),
                })
            continue

        # -- entries --------------------------------------------------------
        if len(held) + sum(1 for o in orders if o["side"] == "buy") >= 4:
            continue                        # max four concurrent, equal weight
        if price <= regime:
            continue                        # the gate: no knife-catching in a bear

        rsi = _rsi(closes, RSI_BARS)
        # The bar's own high and low, not a range assembled from closes. A
        # close-only feed serves these as `[]` by design rather than as zeros,
        # so an empty series means "this feed cannot express IBS" and the
        # honest response is to decline — the same answer as too little
        # history, for the same reason.
        highs = context.highs(symbol, REGIME_BARS)
        lows = context.lows(symbol, REGIME_BARS)
        if not highs or not lows:
            continue
        ibs = _ibs(highs[-1], lows[-1], price)
        if rsi is None or ibs is None:
            continue

        # Variant C, the pre-declared primary: the conjunction, not either
        # alone. Loosening this to an OR is the first thing a tuner would try
        # and would abandon the pre-registration that makes this worth running.
        if rsi >= Decimal("10") or ibs >= Decimal("0.3"):
            continue

        orders.append({
            "symbol": symbol,
            "side": "buy",
            "notional": context.equity * Decimal("0.25"),
            "rationale": (f"RSI(2) {rsi:.1f} < 10 and IBS {ibs:.2f} < 0.30, "
                          "above SMA200"),
        })
    return orders
