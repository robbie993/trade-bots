"""The market as the firms see it: a cursor over a series, never the future.

Every analyst reads through ``MarketData``, and ``MarketData`` will not hand
out a bar past its cursor. That single rule is what makes the backtest and
the live loop the same code path — in a backtest the cursor is advanced by
the runner, live it sits at the end of the series — and it is what stops an
indicator from accidentally reading tomorrow's close.
"""

from __future__ import annotations

from datetime import datetime
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

    def mark(self, symbol: str) -> Decimal:
        """The price a position is valued at. Zero when nothing is known."""
        bar = self.bar(symbol)
        return bar.close if bar else ZERO

    def marks(self, symbols: Iterable[str]) -> dict:
        return {s.upper(): self.mark(s) for s in symbols}

    def as_of(self) -> Optional[datetime]:
        stamps = [b.as_of for b in (self.bar(s) for s in self.symbols) if b is not None]
        return max(stamps) if stamps else None

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
