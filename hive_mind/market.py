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

The bar itself is the repository's own ``trading.models.Bar`` with three
fields added — VIX, sentiment, and the regime label. Same OHLCV, same Decimal
prices, same quantisers, so a real feed from ``src/trading/data/feeds.py``
already produces the base half of what this reads.

**The fills.** ``Venue`` is deliberately hostile. It defers to the village's
``PaperVenue`` for the arithmetic that is already right there — slippage that
always works against the trader, a fee on the gross — and adds the two things
that model does not have: a spread that widens with fear, and impact that
grows with size.

That second part is not a detail. A paper engine that fills the whole order at
the last printed close reports an edge that does not exist, and — this is the
part that ends badly — an evolver scored on those fills will *learn to trade
more*, because in that world size is free. It then arrives live with a genome
tuned for a market where slippage does not exist, and the slippage eats the
edge it was built to have. Perfect fills do not merely flatter a backtest;
they teach a bad habit. So there are no perfect fills in here at all.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator, Optional, Sequence

from src.money import D, ZERO, money
from src.trading.config import DataConfig
from src.trading.execution.paper import PaperVenue
from src.trading.models import Bar as TradingBar, Fill, Side, price, qty

# =========================================================================
# bars
# =========================================================================


@dataclass(frozen=True)
class Bar(TradingBar):
    """The village's OHLCV bar, plus what the scouts actually read.

    VIX and sentiment are not decoration: they are the two inputs the genome
    holds thresholds for, so they belong on the bar rather than in a side
    table that could fall out of step with it. ``regime`` is a label for the
    report and the tests — **nothing that trades may read it**, or the whole
    exercise becomes a system that is told what kind of market it is in.
    """

    index: int = 0
    day: str = ""
    vix: Decimal = ZERO
    sentiment: Decimal = ZERO
    regime: str = ""

    @property
    def depth(self) -> Decimal:
        """Shares available at the touch. Scales with volume, as it does live."""
        return max(D(200), self.volume * D("0.004"))

    @property
    def spread(self) -> Decimal:
        """Quoted spread. Widens with fear — the moment you most want to trade."""
        base = self.close * D("0.0002")
        return base * (D(1) + max(ZERO, self.vix - D(15)) / D(12))


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
    """A seeded tape. Same seed, same bars, on any machine.

    The walk itself is computed in float — it is a random process, not a
    ledger, and ``random.gauss`` returns floats whatever you do about it. But
    every value that leaves this class has been through the village's
    ``price()`` quantiser, so nothing downstream ever sees a binary float.
    """

    name = "generated"

    def __init__(
        self,
        seed: int = 7,
        start_price: float = 500.0,
        plan: Optional[Sequence[tuple]] = None,
        symbol: str = "SPY",
        start: Optional[datetime] = None,
    ):
        self.seed = int(seed)
        self.start_price = float(start_price)
        self.plan = list(plan or [("calm_bull", 250)])
        self.symbol = symbol.upper()
        self.start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
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
                        symbol=self.symbol,
                        as_of=self.start + timedelta(days=index),
                        open=price(open_),
                        high=price(high),
                        low=price(low),
                        close=price(close),
                        volume=D(round(volume)),
                        index=index,
                        day=f"Day {index + 1}",
                        vix=D(str(round(vix, 2))),
                        sentiment=D(str(round(sentiment, 3))),
                        regime=regime_name,
                    )
                )
                previous_return = ret
                index += 1

        self._bars = bars
        return bars

    def window(self, start: int, end: int) -> "WindowFeed":
        return WindowFeed(self, start, end)

    def profile(self) -> dict:
        """What this feed *is*, in terms a certificate can be checked against.

        A genome's thresholds are claims about the scales it was measured on.
        Swap the scale and the same numbers fire on different days for
        different reasons, with nothing to report — so the scales travel with
        the licence. See `lock.check_profile`.
        """
        return {
            "feed": "generated",
            "vix": "generated:regime-model",
            "sentiment": "generated:regime-model",
        }

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

    def window(self, start: int, end: int) -> "WindowFeed":
        """A narrower window of this one. It can never be a wider one.

        The crucible splits whatever feed it is handed, so a blindfolded feed
        has to be splittable too — and the split must not be a way back out.
        Both ends are resolved against this window and the far end is clamped
        to it, so no arithmetic here, and no caller's arithmetic, can hand back
        a bar this feed was not allowed to see.
        """
        absolute_start = self.start + max(0, int(start))
        absolute_end = min(self.end, self.start + int(end))
        return WindowFeed(self.source, absolute_start, max(absolute_start, absolute_end))

    def profile(self) -> dict:
        """A window is the same tape, so it is the same profile."""
        return self.source.profile()

    def __len__(self) -> int:
        return len(self.bars())

    def __iter__(self) -> Iterator:
        return iter(self.bars())


# =========================================================================
# fills
# =========================================================================
@dataclass
class Venue:
    """Fills that cost what fills cost.

    Two layers, and only the second is new here:

    ``PaperVenue``  the village's own fill arithmetic — slippage that always
                    works against the trader, a fee on the gross, everything
                    quantised the way the ledger quantises it. Reused rather
                    than re-derived: a second implementation of "what a fill
                    costs" is a second set of numbers to reconcile, and the
                    first thing to go wrong would be that the two disagree.
    *impact*        what that model does not have — a spread that widens with
                    VIX, and square-root impact on size. An order larger than
                    the displayed depth does not fill at the touch, it walks
                    the book, and a simulator without that term is one that
                    tells an evolver size is free.

    Both are charged on every share in both directions, so there is no size at
    which trading becomes free and no direction in which the venue is on your
    side.
    """

    config: Optional[DataConfig] = None
    impact_coefficient: Decimal = D("0.55")
    fills: int = 0
    total_slippage: Decimal = ZERO
    total_fees: Decimal = ZERO

    def __post_init__(self) -> None:
        self.paper = PaperVenue(self.config or DataConfig())
        self.impact_coefficient = D(self.impact_coefficient)

    def impact(self, shares: Decimal, bar: Bar) -> Decimal:
        """Cost per share of taking ``shares`` out of this bar's book."""
        half_spread = bar.spread / D(2)
        participation = D(shares) / max(bar.depth, D(1))
        # Square-root impact: the standard shape. The direction of the error
        # is what matters — bigger always hurts more.
        walk = bar.close * self.impact_coefficient * participation.sqrt() * D("0.01")
        return half_spread + walk

    def execute(self, side: str, shares, bar: Bar) -> Optional[Fill]:
        shares = qty(abs(D(shares)))
        if shares <= 0:
            return None

        side_enum = Side(side)
        per_share = self.impact(shares, bar)
        adjusted = bar.close + per_share if side_enum is Side.BUY else bar.close - per_share
        adjusted = max(D("0.01"), adjusted)

        # The village prices the fill from there: its slippage, its fee.
        quote = self.paper.quote(side_enum, adjusted, shares)
        impact_cost = money(per_share * shares)

        self.fills += 1
        self.total_slippage += quote.slippage + impact_cost
        self.total_fees += quote.fee
        return Fill(
            firm_id=None,
            symbol=bar.symbol,
            side=side_enum.value,
            quantity=shares,
            price=quote.fill_price,
            fee=quote.fee,
            slippage=money(quote.slippage + impact_cost),
            cash_delta=money(
                -quote.gross - quote.fee if side_enum is Side.BUY else quote.gross - quote.fee
            ),
            venue="hostile-paper",
            as_of=bar.as_of,
        )


@dataclass
class PerfectVenue(Venue):
    """Fills at the close, for free. **Never use this to decide anything.**

    It exists so ``python -m hive_mind --show-hallucination`` can print the
    same strategy scored both ways, side by side. The gap between the two
    numbers is the money the simulation was inventing, and seeing it once is
    worth more than any warning in a README.
    """

    def execute(self, side: str, shares, bar: Bar) -> Optional[Fill]:
        shares = qty(abs(D(shares)))
        if shares <= 0:
            return None
        side_enum = Side(side)
        gross = money(shares * bar.close)
        self.fills += 1
        return Fill(
            firm_id=None,
            symbol=bar.symbol,
            side=side_enum.value,
            quantity=shares,
            price=bar.close,
            fee=ZERO,
            slippage=ZERO,
            cash_delta=-gross if side_enum is Side.BUY else gross,
            venue="perfect",
            as_of=bar.as_of,
        )


__all__ = [
    "Bar",
    "Fill",
    "MarketFeed",
    "PerfectVenue",
    "REGIMES",
    "Regime",
    "SCENARIOS",
    "Side",
    "Venue",
    "WindowFeed",
]
