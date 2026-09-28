"""Take-profit rules for the volatility rotation bots.

The rotation engine in ``vol_rotation.py`` has no profit target. Its exits are
all about *leaving*: rotated out, distribution on volume, momentum flipped,
a volatility spike. Each of those fires just as readily on a winner as on a
loser, so a trade that is up rarely gets to stay up long. Wins stay small.

A take-profit rule changes that in two ways, and both matter:

1. **A winner stops listening to the soft exits.** While a position is above
   its average entry, rotation, distribution, a momentum flip, a vol spike
   and the vol trim are all ignored. Only the take-profit rule can close it.
   Once the price falls back under entry the position is a loser again, and
   every original exit applies. Protecting a loser is still the engine's job.
2. **The rule decides where "enough" is.** Ten different ideas of where,
   below. All of them sit far above where the soft exits used to take the
   trade off.

Each rule is a function of the bars, the average entry and the engine's
reading of the latest bar, and returns a ``Decision``. They are stateless on
purpose: the adapter reloads the bot every tick, so nothing survives between
bars except the position itself, and a rule that needed memory would quietly
lose it.

=====================  ==================================================
``atr_target``         entry + ``tp_atr`` x ATR
``vol_target``         entry x exp(``tp_sigma`` sigma over ``tp_horizon`` bars)
``r_multiple``         risk 1R = entry to the swing low before the run
                       (at least ``tp_stop_atr`` ATR); target ``tp_r`` R
``swing_high``         the ``tp_lookback`` high before the run, then
                       ``tp_atr`` ATR past it
``measured_move``      entry + ``tp_mult`` x the ``tp_lookback`` range
                       before the run
``vwap_band``          VWAP + ``tp_sigma`` standard deviations
``chandelier``         no target: trail ``tp_trail_atr`` ATR under the high
``ladder``             a third off at half the target, a third at the
                       target, the last third trailed
``volume_climax``      out into a blow-off: ``tp_climax_rvol`` x volume
``fib_extension``      the ``tp_mult`` extension of the leg into entry
=====================  ==================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

MODES = (
    "atr_target", "vol_target", "r_multiple", "swing_high", "measured_move",
    "vwap_band", "chandelier", "ladder", "volume_climax", "fib_extension",
)


@dataclass(frozen=True)
class Decision:
    """What the rule wants done with a position this bar."""

    sell_fraction: float = 0.0      # of what is held now; 1.0 is everything
    why: str = ""
    allow_adds: bool = True         # may the engine scale further in?
    target: Optional[float] = None  # the price being aimed at, for the rationale


HOLD = Decision()


# =========================================================================
# what the rules measure
# =========================================================================
def atr(bars: list, window: int) -> float:
    """Average true range over `window` bars. Zero with too little history."""
    if len(bars) < window + 1:
        return 0.0
    ranges = []
    for previous, bar in zip(bars[-window - 1:-1], bars[-window:]):
        high, low, prior = float(bar.high), float(bar.low), float(previous.close)
        ranges.append(max(high - low, abs(high - prior), abs(low - prior)))
    return sum(ranges) / len(ranges)


def highest(bars: list, window: int) -> float:
    span = bars[-window:]
    return max(float(b.high) for b in span) if span else 0.0


def lowest(bars: list, window: int) -> float:
    span = bars[-window:]
    return min(float(b.low) for b in span) if span else 0.0


def before_the_run(bars: list, entry: float) -> list:
    """The bars up to the last close at or under entry.

    A level measured over *recent* bars includes the rally the trade is
    riding, so it moves up with the price. A target set from it recedes as
    fast as the trade approaches, and is never reached. The first version of
    `swing_high` did exactly that and booked no wins in five runs. Measuring
    from before the run fixes the level where it was when the run began.
    """
    for index in range(len(bars) - 1, -1, -1):
        if float(bars[index].close) <= entry:
            return bars[: index + 1]
    return bars[:1]


def swing_low_risk(bars: list, entry: float, lookback: int, floor: float) -> float:
    """1R for `r_multiple`: entry down to the swing low the run started from.

    Anchored to the last bar that closed *above* entry. Measured from the
    latest bars instead, a trade falling through its low would keep finding
    a lower low, and the stop would slide down with the price and never
    fire. A trade that has not closed above entry inside the window has no
    run to anchor to, and gets `floor`.
    """
    for index in range(len(bars) - 1, -1, -1):
        if float(bars[index].close) > entry:
            base = before_the_run(bars[: index + 1], entry)
            return max(entry - lowest(base, lookback), floor)
    return floor


# =========================================================================
# the ten rules
# =========================================================================
def _at_target(price: float, target: float, name: str) -> Decision:
    if price >= target:
        return Decision(1.0, f"take profit: {name} {target:.2f} reached", target=target)
    return Decision(target=target)


def _chandelier(bars, price, entry, a, p, armed_at: float) -> Decision:
    """Out when the price closes `tp_trail_atr` ATRs under the recent high."""
    if price - entry < armed_at * a:
        return HOLD                              # not far enough up to trail yet
    stop = highest(bars, p.tp_lookback) - p.tp_trail_atr * a
    if price < stop:
        return Decision(1.0, f"take profit: trailed out {p.tp_trail_atr:g} ATR under "
                             f"the high, stop {stop:.2f}")
    return HOLD


def decide(bars: list, entry: float, reading, p, held_value: float,
           full_value: float) -> Decision:
    """Apply `p.take_profit` to one position. HOLD when there is nothing to do."""
    mode = p.take_profit
    price = reading.price
    a = atr(bars, p.atr_window)
    if a <= 0 or entry <= 0:
        return HOLD

    if mode == "atr_target":
        return _at_target(price, entry + p.tp_atr * a, f"{p.tp_atr:g} ATR target")

    if mode == "vol_target":
        per_bar = reading.annual_vol / math.sqrt(p.bars_per_year)
        move = p.tp_sigma * per_bar * math.sqrt(p.tp_horizon)
        return _at_target(price, entry * math.exp(move),
                          f"{p.tp_sigma:g}-sigma {p.tp_horizon}-bar move")

    if mode == "r_multiple":
        # Risk is set by structure, not by a volatility unit: 1R is the drop
        # from entry to the swing low the run started from, where the trade
        # is plainly wrong. Never under `tp_stop_atr` ATR, so a trade entered
        # right on its low does not get a hair-trigger stop and a tiny target.
        risk = swing_low_risk(bars, entry, p.tp_lookback, p.tp_stop_atr * a)
        if price <= entry - risk:
            return Decision(1.0, f"stop: 1R ({risk:.2f}) under entry {entry:.2f}")
        return _at_target(price, entry + p.tp_r * risk, f"{p.tp_r:g}R target")

    if mode == "swing_high":
        resistance = highest(before_the_run(bars, entry), p.tp_lookback)
        return _at_target(price, max(resistance, entry) + p.tp_atr * a,
                          f"{p.tp_lookback}-bar high + {p.tp_atr:g} ATR")

    if mode == "measured_move":
        base = before_the_run(bars, entry)
        height = highest(base, p.tp_lookback) - lowest(base, p.tp_lookback)
        return _at_target(price, entry + p.tp_mult * max(height, a),
                          f"{p.tp_mult:g}x the {p.tp_lookback}-bar range")

    if mode == "vwap_band":
        window = bars[-p.tp_lookback:]
        volume = sum(float(b.volume) for b in window)
        closes = [float(b.close) for b in window]
        vwap = (sum(float(b.close) * float(b.volume) for b in window) / volume
                if volume > 0 else sum(closes) / len(closes))
        spread = math.sqrt(sum((c - vwap) ** 2 for c in closes) / len(closes))
        # Never a target under entry + 2 ATR: a band that has not caught up
        # with the trade would otherwise sell it for pennies.
        return _at_target(price, max(vwap + p.tp_sigma * spread, entry + 2 * a),
                          f"VWAP + {p.tp_sigma:g} sd band")

    if mode == "chandelier":
        return _chandelier(bars, price, entry, a, p, armed_at=p.tp_arm_atr)

    if mode == "ladder":
        risk = p.tp_stop_atr * a
        first, second = entry + p.tp_r * risk / 2, entry + p.tp_r * risk
        if price < first:
            return Decision(target=first)
        keep = 2 / 3 if price < second else 1 / 3
        if price >= second:
            trailed = _chandelier(bars, price, entry, a, p, armed_at=0.0)
            if trailed.sell_fraction:
                return trailed
        allowed = keep * full_value
        if held_value > allowed * 1.05:
            rung = "second" if price >= second else "first"
            return Decision(
                (held_value - allowed) / held_value,
                f"take profit: {rung} rung of the ladder "
                f"({p.tp_r / 2 if rung == 'first' else p.tp_r:g}R), keep "
                f"{keep:.0%} of a full position",
                allow_adds=False, target=second,
            )
        return Decision(allow_adds=False, target=second)

    if mode == "volume_climax":
        if (price - entry >= p.tp_arm_atr * a and reading.up_bar
                and reading.rvol >= p.tp_climax_rvol):
            return Decision(1.0, f"take profit: sold into a {reading.rvol:.1f}x "
                                 "volume climax")
        # The backstop, for a trend that ends quietly instead of in a blow-off.
        return _chandelier(bars, price, entry, a, p, armed_at=p.tp_arm_atr)

    if mode == "fib_extension":
        low = lowest(bars, p.tp_lookback)
        leg = max(entry - low, a)
        return _at_target(price, low + p.tp_mult * leg,
                          f"{p.tp_mult:g} extension of the leg from {low:.2f}")

    raise ValueError(f"unknown take_profit {mode!r}; expected one of {', '.join(MODES)}")


__all__ = ["Decision", "HOLD", "MODES", "atr", "decide", "highest", "lowest"]
