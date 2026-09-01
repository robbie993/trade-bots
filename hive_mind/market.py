"""The market the hive mind lives in — and the fills it actually gets.

Two jobs, and the second one is the important one.

**The tape.** A seeded generator that produces bars with a regime attached:
price, VIX and a news-sentiment reading that move together the way they do in
life — VIX rises when returns go negative and jumpy, sentiment lags price.
Regimes differ in the thing that decides whether a strategy lives: whether the
next move is more likely to continue (trend) or reverse (mean reversion). A
genome tuned on chop and dropped into a straight-line rally does not get
unlucky, it gets steamrolled, and that has to be reproducible here or the
crucible in ``lock.py`` has nothing to catch.

**The fills.** ``Venue`` is deliberately hostile. A market order crosses the
spread, eats the book, and pays a fee. Size hurts: taking more than the
displayed depth walks the price away from you.

That second part is not a detail. A paper engine that fills the whole order at
the last printed close reports an edge that does not exist, and — this is the
part that ends badly — an evolver scored on those fills will *learn to trade
more*, because in that world size is free. It then arrives live with a genome
tuned for a market where slippage does not exist, and the slippage eats the
edge it was built to have. Perfect fills do not merely flatter a backtest;
they teach a bad habit. So there are no perfect fills in here at all.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterator, Optional, Sequence

# =========================================================================
# bars
# =========================================================================


@dataclass(frozen=True)
class Bar:
    """One day. ``regime`` is the label, and nothing that trades may read it."""

    index: int
    day: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    vix: float
    sentiment: float
    regime: str = ""

    @property
    def depth(self) -> float:
        """Shares available at the touch. Scales with volume, as it does live."""
        return max(200.0, self.volume * 0.004)

    @property
    def spread(self) -> float:
        """Quoted spread. Widens with fear — the moment you most want to trade."""
        base = self.close * 0.0002
        return base * (1.0 + max(0.0, self.vix - 15.0) / 12.0)


@dataclass(frozen=True)
class Regime:
    """What kind of market this is, in the only terms that change an outcome."""

    name: str
    drift: float  # daily expected return
    vol: float  # daily standard deviation
    reversion: float  # >0 mean-reverting, <0 trend-following
    vix_base: float

    def __str__(self) -> str:
        return self.name


REGIMES = {
    "calm_bull": Regime("calm_bull", 0.00045, 0.007, 0.10, 13.0),
    "chop": Regime("chop", 0.00000, 0.011, 0.35, 18.0),
    "melt_up": Regime("melt_up", 0.00130, 0.010, -0.30, 15.0),
    "grind_down": Regime("grind_down", -0.00060, 0.013, -0.15, 24.0),
    "crash": Regime("crash", -0.01000, 0.030, -0.10, 46.0),
    "rebound": Regime("rebound", 0.00320, 0.026, 0.25, 30.0),
    "rate_shock": Regime("rate_shock", -0.00090, 0.018, -0.25, 28.0),
}


# A scenario is a regime schedule: (regime name, days). The names are shaped
# after markets that actually happened, and they are NOT those markets — this
# is a generator, not a data feed. What survives them has survived a caricature
# of 2008, not 2008. See README.md, "Where the honesty runs out".
SCENARIOS: dict = {
    "quiet_2017": [("calm_bull", 250)],
    "chop_2015": [("chop", 120), ("calm_bull", 60), ("chop", 70)],
    "gfc_2008": [("grind_down", 70), ("crash", 40), ("rebound", 45), ("chop", 95)],
    "covid_2020": [("calm_bull", 60), ("crash", 25), ("rebound", 70), ("melt_up", 95)],
    "bear_2022": [("rate_shock", 120), ("grind_down", 80), ("rebound", 50)],
    "melt_up_2021": [("melt_up", 160), ("chop", 40), ("melt_up", 50)],
}


class MarketFeed:
    """A seeded tape. Same seed, same bars, on any machine."""

    def __init__(
        self,
        seed: int = 7,
        start_price: float = 500.0,
        plan: Optional[Sequence[tuple]] = None,
    ):
        self.seed = int(seed)
        self.start_price = float(start_price)
        self.plan = list(plan or [("calm_bull", 250)])
        self._bars: Optional[list] = None

    @classmethod
    def scenario(cls, name: str, seed: int = 7, start_price: float = 500.0) -> "MarketFeed":
        if name not in SCENARIOS:
            raise KeyError(f"unknown scenario {name!r}; have {', '.join(sorted(SCENARIOS))}")
        return cls(seed=seed, start_price=start_price, plan=SCENARIOS[name])

    def bars(self) -> list:
        if self._bars is not None:
            return self._bars

        rng = random.Random(self.seed)
        bars: list = []
        close = self.start_price
        previous_return = 0.0
        vix = REGIMES[self.plan[0][0]].vix_base
        sentiment = 0.0
        index = 0

        for regime_name, days in self.plan:
            regime = REGIMES[regime_name]
            for _ in range(days):
                shock = rng.gauss(0.0, 1.0)
                # The load-bearing term: reversion > 0 fades yesterday, < 0
                # follows it. Everything a strategy can be right or wrong
                # about lives in this line.
                pull = -regime.reversion * previous_return
                # Volatility clusters, so a scared tape stays scared.
                vol = regime.vol * (0.75 + 0.5 * max(0.0, vix - 12.0) / 25.0)
                ret = regime.drift + pull + vol * shock

                open_ = close
                close = max(1.0, open_ * (1.0 + ret))
                swing = abs(close - open_) + open_ * vol * 0.6
                high = max(open_, close) + swing * rng.random()
                low = max(0.5, min(open_, close) - swing * rng.random())
                volume = 60_000 * (1.0 + 6.0 * abs(ret) / max(vol, 1e-9)) * (
                    0.7 + 0.6 * rng.random()
                )

                # VIX: pulled toward the regime's base, kicked by down moves.
                target = regime.vix_base + max(0.0, -ret) * 900.0 + abs(ret) * 250.0
                vix = max(9.0, vix + 0.35 * (target - vix) + rng.gauss(0.0, 0.8))
                # Sentiment lags price and saturates — the news is always a
                # little late and always overstates.
                sentiment = max(
                    -2.0,
                    min(2.0, 0.82 * sentiment + 55.0 * ret + rng.gauss(0.0, 0.06)),
                )

                bars.append(
                    Bar(
                        index=index,
                        day=f"Day {index + 1}",
                        open=round(open_, 2),
                        high=round(high, 2),
                        low=round(low, 2),
                        close=round(close, 2),
                        volume=round(volume),
                        vix=round(vix, 2),
                        sentiment=round(sentiment, 3),
                        regime=regime_name,
                    )
                )
                previous_return = ret
                index += 1

        self._bars = bars
        return bars

    def window(self, start: int, end: int) -> "WindowFeed":
        return WindowFeed(self, start, end)

    def __len__(self) -> int:
        return len(self.bars())

    def __iter__(self) -> Iterator:
        return iter(self.bars())


class WindowFeed:
    """A feed with the rest of history physically removed.

    Not a cursor, not a promise to stop at bar 400 — a different object that
    *has* no other bars. The whole walk-forward argument rests on the training
    run being unable to see the holdout, and a rule you have to remember is a
    rule that gets forgotten during a refactor. This one cannot be.
    """

    def __init__(self, feed: MarketFeed, start: int, end: int):
        self.source = feed
        self.start = max(0, int(start))
        self.end = int(end)
        self.name = f"{getattr(feed, 'name', 'feed')}[{self.start}:{self.end}]"

    def bars(self) -> list:
        return self.source.bars()[self.start : self.end]

    def __len__(self) -> int:
        return len(self.bars())

    def __iter__(self) -> Iterator:
        return iter(self.bars())


# =========================================================================
# fills
# =========================================================================


@dataclass
class Fill:
    side: str
    shares: float
    price: float  # what you actually paid, per share
    reference: float  # the close you saw when you decided
    fee: float
    slippage: float  # total dollars lost to spread and impact

    @property
    def notional(self) -> float:
        return self.shares * self.price

    @property
    def cash_delta(self) -> float:
        gross = self.shares * self.price
        return -gross - self.fee if self.side == "buy" else gross - self.fee


@dataclass
class Venue:
    """Fills that cost what fills cost.

    ``half_spread`` is unavoidable: you buy at the offer and sell at the bid.
    ``impact`` is the part every simulator forgets — an order larger than the
    displayed depth does not fill at the touch, it walks the book. Both are
    charged on every share, in both directions, so there is no size at which
    trading becomes free and no direction in which the venue is on your side.
    """

    fee_bps: float = 1.0
    impact_coefficient: float = 0.55
    fills: int = 0
    total_slippage: float = 0.0
    total_fees: float = 0.0

    def execute(self, side: str, shares: float, bar: Bar) -> Optional[Fill]:
        shares = abs(float(shares))
        if shares < 1e-9:
            return None

        half_spread = bar.spread / 2.0
        # Square-root impact: the standard shape, and the direction of the
        # error is the one that matters — bigger hurts more, always.
        participation = shares / max(bar.depth, 1.0)
        impact = bar.close * self.impact_coefficient * math.sqrt(participation) * 0.01

        cost_per_share = half_spread + impact
        price = bar.close + cost_per_share if side == "buy" else bar.close - cost_per_share
        price = max(0.01, price)

        fee = shares * price * self.fee_bps / 10_000.0
        slippage = cost_per_share * shares

        self.fills += 1
        self.total_slippage += slippage
        self.total_fees += fee
        return Fill(
            side=side,
            shares=shares,
            price=price,
            reference=bar.close,
            fee=fee,
            slippage=slippage,
        )


@dataclass
class PerfectVenue(Venue):
    """Fills at the close, for free. **Never use this to decide anything.**

    It exists so ``python -m hive_mind --show-hallucination`` can print the
    same strategy scored both ways, side by side. The gap between the two
    numbers is the money the simulation was inventing, and seeing it once is
    worth more than any warning in a README.
    """

    def execute(self, side: str, shares: float, bar: Bar) -> Optional[Fill]:
        shares = abs(float(shares))
        if shares < 1e-9:
            return None
        self.fills += 1
        return Fill(side, shares, bar.close, bar.close, 0.0, 0.0)


__all__ = [
    "Bar",
    "Fill",
    "MarketFeed",
    "PerfectVenue",
    "REGIMES",
    "Regime",
    "SCENARIOS",
    "Venue",
    "WindowFeed",
]
