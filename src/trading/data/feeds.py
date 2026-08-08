"""Market data sources.

Three of them, in order of how much they can fail:

``synthetic``  a seeded random walk. No network, no keys, byte-identical on
               every machine. This is the default because a system whose
               tests need a market data subscription does not get tested,
               and because a backtest you cannot reproduce is an anecdote.
``csv``        files under ``data/market/<SYMBOL>.csv``. What you use once
               you have real history and still want determinism.
``yahoo``      live daily bars. Needs ``requests`` and a working network;
               fails loudly rather than silently returning a short series,
               because a truncated history quietly changes every indicator.

All three hand back ``Bar`` objects with Decimal prices. Nothing downstream
knows or cares which source it is reading.
"""

from __future__ import annotations

import csv
import hashlib
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Protocol

from ...money import D
from ..config import DataConfig
from ..models import Bar, price


class FeedNotConfigured(RuntimeError):
    """The requested source cannot run here — missing dependency, key or file."""


class MarketFeed(Protocol):
    name: str

    def series(self, symbol: str) -> list[Bar]:
        """Every bar this feed has for the symbol, oldest first."""


# =========================================================================
# synthetic
# =========================================================================
class SyntheticFeed:
    """A seeded random walk with per-symbol character.

    Drift and volatility are derived from a hash of the symbol, so ``BTC-USD``
    is reliably wilder than ``SPY`` without anyone hand-tuning a fixture, and
    the same symbol behaves the same way in every run and on every machine.
    """

    name = "synthetic"

    def __init__(self, seed: int = 0, days: int = 180, start: Optional[datetime] = None):
        self.seed = int(seed)
        self.days = int(days)
        self.start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._cache: dict[str, list[Bar]] = {}

    def _params(self, symbol: str) -> tuple[int, Decimal, Decimal, Decimal]:
        digest = hashlib.sha256(f"{self.seed}:{symbol}".encode()).digest()
        stream = int.from_bytes(digest[:8], "big")
        # Base price 20-520, daily drift -0.05%..+0.10%, daily vol 0.5%-4.5%.
        base = D(20 + digest[8] % 500)
        drift = (D(digest[9] % 150) - D(50)) / D(100000)
        vol = (D(50) + D(digest[10] % 400)) / D(10000)
        return stream, base, drift, vol

    def series(self, symbol: str) -> list[Bar]:
        if symbol in self._cache:
            return self._cache[symbol]

        stream, base, drift, vol = self._params(symbol)
        rng = random.Random(stream)
        bars: list[Bar] = []
        close = base
        for i in range(self.days):
            # Decimal end to end: two runs of the same seed must produce the
            # same cents, not the same float that rounds differently.
            shock = D(str(round(rng.gauss(0, 1), 6)))
            change = drift + vol * shock
            open_ = close
            close = max(D("0.01"), price(open_ * (D(1) + change)))
            spread = abs(close - open_) + open_ * vol / D(2)
            high = price(max(open_, close) + spread * D(str(round(rng.random(), 6))))
            low = price(max(D("0.01"), min(open_, close) - spread * D(str(round(rng.random(), 6)))))
            volume = D(10_000 + rng.randrange(0, 990_000))
            bars.append(
                Bar(
                    symbol=symbol,
                    as_of=self.start + timedelta(days=i),
                    open=price(open_),
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
        self._cache[symbol] = bars
        return bars


# =========================================================================
# csv
# =========================================================================
class CsvFeed:
    """``data/market/<SYMBOL>.csv`` with a date,open,high,low,close,volume header."""

    name = "csv"

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._cache: dict[str, list[Bar]] = {}

    def path_for(self, symbol: str) -> Path:
        return self.directory / f"{symbol.upper()}.csv"

    def series(self, symbol: str) -> list[Bar]:
        if symbol in self._cache:
            return self._cache[symbol]
        path = self.path_for(symbol)
        if not path.exists():
            raise FeedNotConfigured(
                f"no CSV for {symbol} at {path}. Expected columns: "
                "date,open,high,low,close,volume"
            )
        bars: list[Bar] = []
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                as_of = _parse_date(row.get("date") or row.get("Date") or "")
                if as_of is None:
                    continue
                bars.append(
                    Bar(
                        symbol=symbol,
                        as_of=as_of,
                        open=_col(row, "open"),
                        high=_col(row, "high"),
                        low=_col(row, "low"),
                        close=_col(row, "close"),
                        volume=_col(row, "volume"),
                    )
                )
        if not bars:
            raise FeedNotConfigured(f"{path} contained no usable rows")
        bars.sort(key=lambda b: b.as_of)
        self._cache[symbol] = bars
        return bars


def _col(row: dict, name: str) -> Decimal:
    raw = row.get(name, row.get(name.capitalize(), "0"))
    try:
        return D(raw or 0)
    except Exception:
        return D(0)


def _parse_date(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# =========================================================================
# yahoo
# =========================================================================
class YahooFeed:
    """Daily bars from Yahoo's public chart endpoint.

    Deliberately not the default. It is here so the ecosystem can be pointed
    at real prices with one environment variable, and it raises rather than
    degrades: a partial history silently changes every moving average in the
    system, which is a far worse failure than not starting.
    """

    name = "yahoo"
    ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, days: int = 180, timeout_s: int = 20):
        self.days = int(days)
        self.timeout_s = timeout_s
        self._cache: dict[str, list[Bar]] = {}

    def series(self, symbol: str) -> list[Bar]:
        if symbol in self._cache:
            return self._cache[symbol]
        try:
            import requests  # type: ignore
        except ImportError as exc:  # pragma: no cover - env dependent
            raise FeedNotConfigured(
                "TRADE_DATA_SOURCE=yahoo needs `pip install requests`"
            ) from exc

        span = "1y" if self.days <= 365 else "5y"
        response = requests.get(
            self.ENDPOINT.format(symbol=symbol),
            params={"range": span, "interval": "1d"},
            timeout=self.timeout_s,
            headers={"User-Agent": "ai-village-trading/1.0"},
        )
        if response.status_code != 200:
            raise FeedNotConfigured(f"yahoo returned {response.status_code} for {symbol}")
        payload = response.json()
        try:
            result = payload["chart"]["result"][0]
            stamps = result["timestamp"]
            quote = result["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise FeedNotConfigured(f"unreadable yahoo payload for {symbol}") from exc

        bars: list[Bar] = []
        for i, stamp in enumerate(stamps):
            close = quote["close"][i]
            if close is None:
                continue  # holiday / halted session
            bars.append(
                Bar(
                    symbol=symbol,
                    as_of=datetime.fromtimestamp(stamp, tz=timezone.utc),
                    open=D(quote["open"][i] if quote["open"][i] is not None else close),
                    high=D(quote["high"][i] if quote["high"][i] is not None else close),
                    low=D(quote["low"][i] if quote["low"][i] is not None else close),
                    close=D(close),
                    volume=D(quote["volume"][i] or 0),
                )
            )
        if len(bars) < 30:
            raise FeedNotConfigured(
                f"yahoo returned only {len(bars)} usable bars for {symbol}; refusing to "
                "run indicators on a truncated history"
            )
        bars = bars[-self.days :]
        self._cache[symbol] = bars
        return bars


def build_feed(config: DataConfig) -> MarketFeed:
    source = (config.source or "synthetic").strip().lower()
    if source == "synthetic":
        return SyntheticFeed(seed=config.seed, days=config.history_days)
    if source == "csv":
        return CsvFeed(config.csv_dir)
    if source == "yahoo":
        return YahooFeed(days=config.history_days)
    raise FeedNotConfigured(
        f"unknown TRADE_DATA_SOURCE={source!r}; expected synthetic, csv or yahoo"
    )
