"""The market as the firms see it: a cursor over a series, never the future.

Every analyst reads through ``MarketData``, and ``MarketData`` will not hand
out a bar past its cursor. That single rule is what makes the backtest and
the live loop the same code path — in a backtest the cursor is advanced by
the runner, live it sits at the end of the series — and it is what stops an
indicator from accidentally reading tomorrow's close.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from ...money import D, ZERO
from ..config import DataConfig
from ..models import Bar
from .feeds import FeedNotConfigured, MarketFeed, build_feed


class MarketData:
    def __init__(self, feed: MarketFeed, symbols: Iterable[str] = (), cursor: int = -1):
        self.feed = feed
        self.symbols = list(dict.fromkeys(s.upper() for s in symbols))
        self._cursor = cursor  # -1 means "the end of every series"
        # symbol -> why the feed would not price it. See `_series`.
        self.unpriceable: dict = {}

    @classmethod
    def from_config(cls, config: DataConfig, symbols: Iterable[str] = ()) -> "MarketData":
        return cls(build_feed(config), symbols)

    # -- cursor -----------------------------------------------------------
    @property
    def cursor(self) -> int:
        return self._cursor

    def seek(self, index: int) -> "MarketData":
        self._cursor = index
        return self

    def advance(self, steps: int = 1) -> "MarketData":
        if self._cursor < 0:
            self._cursor = self.length() - 1
        self._cursor = min(self.length() - 1, self._cursor + steps)
        return self

    def at_end(self) -> bool:
        return self._cursor < 0 or self._cursor >= self.length() - 1

    def length(self) -> int:
        """Bars available for the shortest symbol — the honest common history."""
        lengths = [len(self._series(s)) for s in self.symbols] or [0]
        return min(lengths)

    def register(self, symbols: Iterable[str]) -> None:
        for symbol in symbols:
            upper = symbol.upper()
            if upper not in self.symbols:
                self.symbols.append(upper)

    # -- reads ------------------------------------------------------------
    def _series(self, symbol: str) -> list[Bar]:
        """The feed's bars, or none — remembering which symbols it could not price.

        A feed refuses rather than degrades, which is right about *a symbol* and
        was catastrophic about *the market*: one meme coin that no exchange
        lists raised out of `as_of()`, and every firm in the village stopped,
        including the ten with no opinion about it. Eleven firms went quiet for
        three hours over PEPE-USD.

        So the refusal is caught and recorded here. A symbol nothing can price
        becomes a symbol with no bars, which the rest of the system already
        knows how to be unexcited about: no price, no proposal. What it must
        not do is disappear quietly — `unpriceable` is read by the tick, which
        reports it, and reports it as an error for any firm that actually
        *holds* the thing, because a position you cannot value is a book you
        cannot trust.
        """
        upper = symbol.upper()
        try:
            return self.feed.series(upper)
        except Exception as exc:  # noqa: BLE001 - every feed has its own errors
            self.unpriceable[upper] = str(exc)
            return []

    def _visible(self, symbol: str) -> list[Bar]:
        series = self._series(symbol)
        if self._cursor < 0:
            return series
        return series[: self._cursor + 1]

    def history(self, symbol: str, lookback: Optional[int] = None) -> list[Bar]:
        """Bars up to and including the cursor, oldest first."""
        visible = self._visible(symbol)
        if lookback is None or lookback >= len(visible):
            return visible
        return visible[-lookback:]

    def bar(self, symbol: str) -> Optional[Bar]:
        visible = self._visible(symbol)
        return visible[-1] if visible else None

    def bar_span_days(self) -> Optional[Decimal]:
        """Calendar days one bar of *this data* actually covers. `None` if unsure.

        **Measured, not configured.** Anything charged per unit of market time
        — borrow, financing, carry — has to be prorated by how much time a bar
        covers, and every previous version of that calculation read the number
        off `config.data.resolution`. That constant describes the live loop. It
        does not have to describe the data in front of it, and in the backtests
        it did not: the synthetic feed steps one day per bar while the config
        said fifteen minutes, so borrow was billed at **1/96th** of what was
        actually borrowed. On a $4,900 short that is $0.004 a bar, `money()`
        rounds it to zero, `if fee <= 0` drops it, and a short book runs for
        free — with no error, because nothing ever compared the two numbers.

        This is the same unit confusion as the Sharpe kill, the staleness
        licence and the bar count. The lesson those taught is that a config
        constant is a claim about the data and the data is right there to ask,
        so ask it: take the median gap between the visible bars. Median rather
        than mean because a weekend or a halt is a real gap in calendar time
        but not a change of resolution, and one three-day hole should not
        triple the charge on every bar around it.
        """
        stamps = sorted({
            bar.as_of for symbol in self.symbols
            for bar in self.history(symbol, 40) if bar.as_of is not None
        })
        gaps = sorted((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
        gaps = [g for g in gaps if g > 0]
        if len(gaps) < 3:
            return None
        return D(str(gaps[len(gaps) // 2])) / D(86400)

    def mark(self, symbol: str) -> Decimal:
        """The price a position is valued at. Zero when nothing is known."""
        bar = self.bar(symbol)
        return bar.close if bar else ZERO

    def marks(self, symbols: Iterable[str]) -> dict:
        return {s.upper(): self.mark(s) for s in symbols}

    def as_of(self) -> Optional[datetime]:
        stamps = [b.as_of for b in (self.bar(s) for s in self.symbols) if b is not None]
        return max(stamps) if stamps else None

    #: How far behind its peers a symbol may fall before it is treated as
    #: stale. **In seconds, not bars.** Three bars was fine on an hourly feed
    #: and became forty-five minutes on a fifteen-minute one — and a thinly
    #: traded ETF crosses forty-five minutes routinely without anything being
    #: wrong. Measured after the switch to 15m: SPY had printed to 23:45 with
    #: no gaps while EFA's newest bar was 20:45, because SPY trades all through
    #: extended hours and EFA barely trades at all. Nothing was broken; one is
    #: liquid and one is not.
    #:
    #: The cost of getting it wrong is not cosmetic. `firm_h_global`'s entire
    #: universe is EEM, EFA, EWJ, FXI and VGK, so a threshold that flags thin
    #: ETFs marks every symbol it owns unpriceable and stops it trading.
    #:
    #: Three hours is the same tolerance the old three-bar rule gave at hourly
    #: resolution, now stated in the unit it always meant.
    LAG_TOLERANCE_S = 3 * 60 * 60

    #: How old a bar may be before the price behind it is not tradeable, in
    #: hours. **This is the check `lagging` structurally cannot make.**
    #:
    #: `lagging` asks "is this symbol behind its peers?", which catches one
    #: thin ETF falling behind a busy market and is exactly right for that. It
    #: is blind to the case where *every* equity is equally stale, because then
    #: nothing is behind anything — and that case is the US market being shut,
    #: which happens every single night.
    #:
    #: Measured at 06:22 UTC on 2026-09-04, with `lagging` flagging only JNJ:
    #: EFA 9.69h old, EEM 7.69h, SPY 6.69h, SOL-USD 0.19h. `firm_a_etf_ii_v`
    #: was selling EFA at 107.256345 — one price, unchanged since the close,
    #: stamped with a bar the *crypto* feed had advanced to. A trade at a
    #: moment the symbol could not trade.
    #:
    #: Deliberately generous. A thin ETF can go three hours without a print
    #: inside a normal session, and a threshold tight enough to catch that
    #: muted `firm_h_global`'s entire universe once already. Six hours clears
    #: any real intraday gap and still catches an overnight by a wide margin.
    #: It is a freshness rule, not a clock: a firm may trade the pre-market and
    #: the after-hours session whenever there are real prints to trade on.
    MAX_BAR_AGE_S = float(os.environ.get("TRADE_MAX_BAR_AGE_H", "6") or 6) * 3600

    def stale(self, now: Optional[datetime] = None) -> dict:
        """Symbols whose newest bar is too old to trade on. `{symbol: hours}`.

        Absolute, so it works when the whole market is shut and the relative
        check has nothing to compare against.

        **"Now" is the data's clock, not the wall clock.** Defaulting to
        `datetime.now()` looks obviously right and is wrong for every backtest:
        a replay of 2024 would find every symbol years stale and refuse to
        price anything. It is the same mistake as annualising per-tick
        observations or billing borrow per loop pass — judging market data by
        how long the operator has been awake. `as_of()` is the latest bar the
        village has seen, which in a live loop *is* approximately now, and in a
        replay is the moment being replayed.
        """
        now = now or self.as_of()
        if now is None:
            return {}
        out: dict = {}
        for symbol in self.symbols:
            bar = self.bar(symbol)
            if bar is None or bar.as_of is None:
                continue            # no bars at all is `unpriceable`, not stale
            age = (now - bar.as_of).total_seconds()
            if age > self.MAX_BAR_AGE_S:
                out[symbol] = round(age / 3600, 2)
        return out

    def lagging(self, bar_seconds: float, max_bars_behind: float = 3.0) -> dict:
        """Symbols left behind by their own peers. `{symbol: how far behind}`.

        A per-symbol age limit cannot catch a stale equity, because it cannot
        tell "this feed is broken" from "the exchange is shut" without a market
        calendar nobody here has. This can, and needs no calendar: **the other
        symbols are the calendar.** If forty names are on the 18:00 bar and GLD
        is on yesterday's 03:00, the exchange is plainly open, and GLD is stale.

        That is not hypothetical. On 27 August the commodities desk traded GLD
        and SLV against a bar forty hours old for the whole session while the
        value desk bought JNJ and KO on the current one, in the same ticks. The
        village was reading three different bars at once and `as_of()` reported
        the freshest of them, so every proposal looked current.

        **Grouped by whether the market closes.** Crypto runs through the night
        and an equity does not, so comparing the two at 3am flags every stock in
        the village for the crime of the exchange being shut. Symbols are only
        ever measured against others that keep the same hours.
        """
        if bar_seconds <= 0 or max_bars_behind <= 0:
            return {}
        is_crypto = getattr(self.feed, "is_crypto", None)
        groups: dict = {}
        for symbol in self.symbols:
            bar = self.bar(symbol)
            if bar is None or bar.as_of is None:
                continue        # no bars at all is `unpriceable`, not lateness
            key = bool(is_crypto(symbol)) if callable(is_crypto) else False
            groups.setdefault(key, []).append((symbol, bar.as_of))

        out: dict = {}
        for members in groups.values():
            if len(members) < 2:
                continue        # one symbol cannot be out of step with itself
            freshest = max(stamp for _, stamp in members)
            for symbol, stamp in members:
                seconds = (freshest - stamp).total_seconds()
                # Whichever is more generous: the caller's bar count, or the
                # fixed time tolerance. On an hourly feed they agree; on a
                # fifteen-minute one the time floor is what stops a thinly
                # traded ETF being called broken for trading thinly.
                if seconds <= max(bar_seconds * max_bars_behind,
                                  self.LAG_TOLERANCE_S):
                    continue
                out[symbol] = seconds / bar_seconds
        return out

    def closes(self, symbol: str, lookback: Optional[int] = None) -> list[Decimal]:
        return [b.close for b in self.history(symbol, lookback)]

    def returns(self, symbol: str, lookback: Optional[int] = None) -> list[Decimal]:
        """Simple period returns, as fractions. Skips zero-price gaps."""
        closes = self.closes(symbol, lookback)
        out: list[Decimal] = []
        for previous, current in zip(closes, closes[1:]):
            if previous == 0:
                continue
            out.append((current - previous) / previous)
        return out

    def is_ready(self, symbol: str, minimum_bars: int) -> bool:
        """Below the minimum, an analyst must stay silent rather than guess."""
        return len(self._visible(symbol)) >= minimum_bars


__all__ = ["MarketData", "FeedNotConfigured", "build_feed", "D"]
