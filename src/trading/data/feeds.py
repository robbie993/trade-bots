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
import os
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


class CcxtFeed:
    """Daily bars from a real exchange, through ccxt.

    Same contract as every other feed and the same refusal: a truncated
    history silently changes every moving average in the system, so it raises
    rather than degrades. There is no partial-credit mode here.

    Symbols are spelled the village's way — ``BTC-USD`` — and translated on
    the way out, because exchanges disagree about this and the rest of the
    system should not have to care. A `-USD` pair is tried as `/USDT` too,
    since most crypto exchanges quote in the stablecoin rather than dollars
    and refusing on that technicality would be pedantry rather than safety.

    **Not the default, and it never becomes the default by accident.** The
    deterministic synthetic feed is what the tests and the evolution loop run
    on; a seeded replay that quietly started depending on an exchange being up
    would stop being a replay.
    """

    name = "ccxt"

    def __init__(self, exchange: str = "binance", days: int = 180, timeframe: str = "1d"):
        self.exchange_id = (exchange or "binance").strip().lower()
        self.days = int(days)
        self.timeframe = timeframe
        self._client = None
        self._cache: dict[str, list[Bar]] = {}

    # -- symbols ----------------------------------------------------------
    @staticmethod
    def candidates(symbol: str) -> list[str]:
        """How this symbol might be spelled on an exchange, best guess first."""
        plain = str(symbol).upper().replace("-", "/")
        out = [plain]
        if plain.endswith("/USD"):
            out.append(plain[: -len("/USD")] + "/USDT")
            out.append(plain[: -len("/USD")] + "/USDC")
        return out

    def _connect(self):
        if self._client is not None:
            return self._client
        try:
            import ccxt  # type: ignore
        except ImportError as exc:  # pragma: no cover - env dependent
            raise FeedNotConfigured(
                "TRADE_DATA_SOURCE=ccxt needs `pip install ccxt`"
            ) from exc
        if not hasattr(ccxt, self.exchange_id):
            raise FeedNotConfigured(f"ccxt has no exchange called {self.exchange_id!r}")
        # rateLimit on: a feed that gets the village banned from an exchange
        # is worse than a slow one.
        self._client = getattr(ccxt, self.exchange_id)({"enableRateLimit": True})
        return self._client

    def series(self, symbol: str) -> list[Bar]:
        if symbol in self._cache:
            return self._cache[symbol]
        client = self._connect()

        rows, used = None, None
        errors = []
        for candidate in self.candidates(symbol):
            try:
                rows = client.fetch_ohlcv(candidate, self.timeframe, limit=self.days)
            except Exception as exc:  # noqa: BLE001 - ccxt raises its own tree
                errors.append(f"{candidate}: {type(exc).__name__}")
                continue
            if rows:
                used = candidate
                break
        if not rows:
            raise FeedNotConfigured(
                f"{self.exchange_id} returned nothing for {symbol} "
                f"(tried {', '.join(self.candidates(symbol))}"
                + (f"; {'; '.join(errors)}" if errors else "")
                + ")"
            )

        bars: list[Bar] = []
        for row in rows:
            stamp, opened, high, low, close, volume = row[:6]
            if close is None:
                continue
            bars.append(
                Bar(
                    symbol=symbol,          # the village's spelling, not the venue's
                    as_of=datetime.fromtimestamp(stamp / 1000, tz=timezone.utc),
                    open=D(opened if opened is not None else close),
                    high=D(high if high is not None else close),
                    low=D(low if low is not None else close),
                    close=D(close),
                    volume=D(volume or 0),
                )
            )
        if len(bars) < 30:
            raise FeedNotConfigured(
                f"{self.exchange_id} returned only {len(bars)} usable bars for "
                f"{symbol} (as {used}); refusing to run indicators on a truncated "
                "history"
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
    if source == "ccxt":
        return CcxtFeed(
            exchange=os.environ.get("TRADE_CCXT_EXCHANGE", "binance"),
            days=config.history_days,
            timeframe=os.environ.get("TRADE_CCXT_TIMEFRAME", "1d"),
        )
    raise FeedNotConfigured(
        f"unknown TRADE_DATA_SOURCE={source!r}; expected synthetic, csv, yahoo or ccxt"
    )
