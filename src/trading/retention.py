"""How much of a signal's edge survives the delay before it is acted on.

**The one number that decides whether a village is worth building.** A strategy
has an edge measured at the moment its signal fires, and a smaller edge by the
time an order actually reaches a venue. The ratio between them is the retention
ratio:

    R(T) = E_a(T) / E_b

where ``E_b`` is the edge at zero delay and ``E_a(T)`` is what is left after a
round trip of ``T``. Somewhere there is a threshold ``T*`` at which the
surviving edge equals the cost of trading. **If T* is longer than the delay you
actually run at, the design is viable. If it is shorter, no amount of
infrastructure fixes it** — the signal is gone before the order lands, and
faster hardware only means arriving earlier at the same nothing.

This matters here specifically because a village is a *throughput* machine, not
a *latency* machine. It can evaluate many genomes in parallel; it cannot make
the path to the exchange shorter. A race has one winner and more bots do not
shorten the path. So the village is only viable at timescales where latency is
not what decides the outcome — and this module is how you find out whether the
timescale it actually runs at is one of those.

**Read the curve, not the single number.** A flat R(T) across many bars means
the edge is slow-moving and the village's 15-minute cadence is irrelevant to
it — good news, and licence to go looking for signals. A curve that collapses
within a bar or two means the village is playing a game it structurally cannot
win, and the honest response is to stop rather than to optimise.

The costs are charged from the village's own config, not assumed, so ``T*`` is
answered in the same units the ledger is kept in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Sequence

from ..money import D, ZERO


@dataclass
class Point:
    """One delay, and what the edge was worth at it."""

    bars: int
    #: Mean per-trade return at this delay, in basis points of notional.
    edge_bps: Decimal
    #: How many trades this was measured over. A point with n=3 is noise.
    n: int
    #: Median per-trade return. Carried because the mean is not enough: the
    #: first village run measured -14.4 bps mean against a -1.9 bps median,
    #: and the gap was one overnight bar where 22 firms bought DOGE and WIF
    #: into a 5% slide. Dropping that single bar moved the mean to -2.1.
    median_bps: Decimal = ZERO
    #: Mean with every *bar* weighted equally instead of every entry. Firms
    #: act on the same bar constantly — 1,017 entries landed on 239 bars — so
    #: the entry-weighted mean is really a vote on the few bars the village
    #: crowded into. Weighting bars equally flipped the first run's headline
    #: from -14.4 bps to +1.4, and it is the honest summary of the two.
    bar_weighted_bps: Optional[Decimal] = None
    #: How many distinct bars the entries fell on. The real sample size.
    bars_seen: Optional[int] = None

    @property
    def label(self) -> str:
        return "no delay" if self.bars == 0 else f"+{self.bars} bar"

    @property
    def fragile(self) -> bool:
        """True when mean and median disagree, so one cluster is driving it.

        Not a verdict — a flag that says the mean is the wrong summary here
        and the sample needs a leave-one-out before anything is concluded.
        """
        if self.edge_bps == 0 or self.median_bps == 0:
            return self.edge_bps != self.median_bps
        return ((self.edge_bps > 0) != (self.median_bps > 0)
                or abs(self.edge_bps - self.median_bps) > abs(self.median_bps) * D(2))


@dataclass
class Retention:
    """The whole curve, plus the threshold that matters."""

    points: list = field(default_factory=list)
    #: Round-trip cost the edge has to clear, in basis points.
    cost_bps: Decimal = ZERO

    @property
    def baseline(self) -> Optional[Decimal]:
        """Edge at zero delay. `None` when there is nothing to divide by."""
        for p in self.points:
            if p.bars == 0:
                return p.edge_bps
        return None

    def ratio_at(self, bars: int) -> Optional[Decimal]:
        """R(T). `None` unless there is a positive baseline edge to retain.

        A zero baseline is not a retention of zero — it is a strategy with no
        measurable edge to retain, and dividing by it would manufacture a
        number out of nothing.

        A *negative* baseline is worse than useless, and this returned a number
        for it until a village run walked straight into the trap: the edge went
        from -14.4 bps to -15.8 bps and R(T) printed **1.10**, which reads as
        "110% of the edge survived the delay" when what actually happened is
        that a loss got deeper. The ratio is only meaningful when there is an
        edge, so when there is not, say so instead of dividing.
        """
        base = self.baseline
        if base is None or base <= 0:
            return None
        for p in self.points:
            if p.bars == bars:
                return p.edge_bps / base
        return None

    @property
    def viable_to(self) -> Optional[int]:
        """`T*` in bars: the largest delay whose edge still clears costs.

        `None` means the edge never clears costs at any delay, including none —
        which is a finding, not a failure of the measurement.
        """
        clearing = [p.bars for p in self.points if p.edge_bps > self.cost_bps]
        return max(clearing) if clearing else None


def _returns_at(closes: Sequence[Decimal], index: int, delay: int,
                horizon: int, sign: int) -> Optional[Decimal]:
    """Return over `horizon` bars, entered `delay` bars after the signal.

    `sign` is +1 for a long and -1 for a short, so a short that falls is a
    gain. Getting that backwards inverts the whole curve, which is why it is a
    parameter rather than an assumption.
    """
    entry, exit_ = index + delay, index + delay + horizon
    if entry >= len(closes) or exit_ >= len(closes):
        return None
    a, b = closes[entry], closes[exit_]
    if a <= 0:
        return None
    return (b - a) / a * D(sign) * D(10000)      # basis points


def measure(signals: Sequence[tuple], closes_by_symbol: dict,
            delays: Sequence[int] = (0, 1, 2, 4, 8, 16),
            horizon: int = 4, cost_bps: Decimal = ZERO) -> Retention:
    """Build the curve from `(symbol, bar_index, sign)` signals.

    `horizon` is held fixed across delays on purpose. Letting it grow with the
    delay would measure a longer trade rather than a later one, and a longer
    trade in a drifting market looks like retained edge when it is only
    retained exposure.

    A signal is ``(symbol, bar_index, sign)``, or ``(symbol, bar_index, sign,
    bar_key)`` to also get the bar-weighted mean. Pass the bar key whenever you
    have it: without it there is no way to tell 1,000 independent decisions
    from 1,000 firms agreeing with each other on 200 bars.
    """
    out = Retention(cost_bps=D(cost_bps))
    for delay in delays:
        got, by_bar = [], {}
        for signal in signals:
            symbol, index, sign = signal[0], signal[1], signal[2]
            key = signal[3] if len(signal) > 3 else None
            closes = closes_by_symbol.get(symbol) or []
            r = _returns_at(closes, index, delay, horizon, sign)
            if r is not None:
                got.append(r)
                if key is not None:
                    by_bar.setdefault(key, []).append(r)
        if got:
            ordered = sorted(got)
            mid = len(ordered) // 2
            median = (ordered[mid] if len(ordered) % 2
                      else (ordered[mid - 1] + ordered[mid]) / D(2))
            bar_mean = None
            if by_bar:
                means = [sum(v, ZERO) / D(len(v)) for v in by_bar.values()]
                bar_mean = sum(means, ZERO) / D(len(means))
            out.points.append(Point(bars=delay,
                                    edge_bps=sum(got, ZERO) / D(len(got)),
                                    n=len(got), median_bps=median,
                                    bar_weighted_bps=bar_mean,
                                    bars_seen=len(by_bar) or None))
    return out


def from_village(store, feed, delays: Sequence[int] = (0, 1, 2, 4, 8, 16),
                 horizon: int = 4, cost_bps: Decimal = ZERO,
                 min_bars: int = 100, only: str = "all") -> Retention:
    """Measure the curve on the village's own entries.

    A *buy* fill is the signal. Sells are deliberately excluded: the village is
    long-only in practice, so a sell is an exit, and counting an exit as a
    short entry inverts its sign and quietly corrupts the whole curve.

    `only` takes "all", "equities" or "crypto". They deserve separate curves —
    crypto trades through the night against a spread several times wider, and
    pooling the two hides both.
    """
    rows = store.db.query(
        "SELECT symbol, as_of FROM fills WHERE side = 'buy' AND quantity > ?",
        (str(DUST),))
    wanted = [r for r in rows if _in(r["symbol"], only)]
    if not wanted:
        return Retention(cost_bps=D(cost_bps))

    closes_by_symbol, index = {}, {}
    for symbol in sorted({r["symbol"] for r in wanted}):
        try:
            bars = feed.series(symbol)
        except Exception:
            continue                    # a feed gap is not a reason to stop
        if len(bars) < min_bars:
            continue
        closes_by_symbol[symbol] = [b.close for b in bars]
        index[symbol] = {b.as_of.strftime(_BAR_KEY): i
                         for i, b in enumerate(bars)}

    signals = []
    for r in wanted:
        symbol = r["symbol"]
        if symbol not in index:
            continue
        key = str(r["as_of"])[:16].replace(" ", "T")
        i = index[symbol].get(key)
        if i is not None:
            signals.append((symbol, i, 1, key))
    return measure(signals, closes_by_symbol, delays=delays, horizon=horizon,
                   cost_bps=cost_bps)


def _in(symbol: str, only: str) -> bool:
    if only == "equities":
        return "-USD" not in symbol
    if only == "crypto":
        return "-USD" in symbol
    return True


#: Fills below this are dust, not trades.
DUST = D("0.000001")
_BAR_KEY = "%Y-%m-%dT%H:%M"

__all__ = ["Point", "Retention", "measure", "from_village"]
