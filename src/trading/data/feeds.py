"""Market data sources.

Three of them, in order of how much they can fail:

``synthetic``  a seeded random walk. No network, no keys, byte-identical on
               every machine. This is the default because a system whose
               tests need a market data subscription does not get tested,
               and because a backtest you cannot reproduce is an anecdote.
``csv``        files under ``data/market/<SYMBOL>.csv``. What you use once
               you have real history and still want determinism.
``yahoo``      live daily bars. Needs a working network and nothing else;
               fails loudly rather than silently returning a short series,
               because a truncated history quietly changes every indicator.

All three hand back ``Bar`` objects with Decimal prices. Nothing downstream
knows or cares which source it is reading.
"""

from __future__ import annotations

import csv
import hashlib
import os
import warnings
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Protocol, Sequence

from ...money import D
from ..config import DataConfig
from ..models import Bar, price


def _get_json(url: str, params: dict, headers: Optional[dict] = None, timeout: int = 20):
    """One HTTP GET returning parsed JSON, on the standard library alone.

    This used to be `requests`, imported inside each feed, raising a tidy
    "pip install requests" when it was missing. Tidy and wrong: `requests` is
    in requirements.txt but **not** in pyproject's dependencies, so anybody who
    installed with `pip install -e .` — which is what the setup instructions
    say — got a village whose every real feed refused on import.

    The symptom was not an error anybody could act on. Each refusal was caught
    per symbol, every symbol became unpriceable, `as_of()` went None, and the
    console said `as of: None (data: alpaca)` with eleven dead firms under it.
    A missing HTTP client presented as a market that had ceased to exist.

    A price feed is core, and the core of this repository runs on the standard
    library by design. `urllib` does exactly this much. Now there is nothing to
    install and nothing to forget.

    Raises FeedNotConfigured with the status and body on anything but a 200,
    because "alpaca returned 403: forbidden" is a sentence you can act on.
    """
    import json
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    request = Request(f"{url}?{query}" if query else url, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:   # noqa: S310 - fixed hosts
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 - the status is the useful part
            pass
        raise FeedHTTPError(exc.code, body) from exc
    except URLError as exc:
        raise FeedNotConfigured(f"could not reach {url}: {exc.reason}") from exc
    except ValueError as exc:                 # not JSON
        raise FeedNotConfigured(f"{url} did not return JSON: {exc}") from exc


class FeedHTTPError(Exception):
    """A non-200 from a feed, carrying the status so callers can explain it."""

    def __init__(self, status: int, body: str = ""):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:120]}")


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

        span = "1y" if self.days <= 365 else "5y"
        try:
            payload = _get_json(
                self.ENDPOINT.format(symbol=symbol),
                {"range": span, "interval": "1d"},
                {"User-Agent": "ai-village-trading/1.0"},
                self.timeout_s,
            )
        except FeedHTTPError as exc:
            raise FeedNotConfigured(
                f"yahoo returned {exc.status} for {symbol}"
                + (" — it does not know that ticker" if exc.status == 404 else "")
            ) from exc
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


class AlpacaFeed:
    """Stocks and crypto from one place.

    Every other real feed here covers half a universe. Yahoo knows SPY and not
    DOGE; a crypto exchange knows DOGE and not SPY; and Binance, it turns out,
    does not know a US address at all. A village holding both needs a chain, or
    it needs one source that does both — which is what this is.

    It is also a broker, which matters later: the same credentials that price
    the book are the ones that would eventually trade it. Nothing here trades.
    This is the data API and nothing else.

    **Credentials.** Free, from alpaca.markets, and read from the environment:

        ALPACA_API_KEY_ID       or APCA_API_KEY_ID
        ALPACA_API_SECRET_KEY   or APCA_API_SECRET_KEY

    They are never logged, never written anywhere, and the failure when they
    are absent says what to set rather than returning an unexplained 403.

    **Which endpoint.** Decided by the village's own spelling, which already
    distinguishes them: `BTC-USD` has a quote currency and goes to the crypto
    endpoint as `BTC/USD`; `SPY` is a bare ticker and goes to the stock one.
    No list of coins to maintain and nothing to keep in sync.

    The free stock tier is IEX rather than the full consolidated tape. For
    daily bars that is a rounding difference; for anything intraday it is not,
    and `TRADE_ALPACA_FEED=sip` switches it if you have the subscription.
    """

    name = "alpaca"
    STOCKS = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    CRYPTO = "https://data.alpaca.markets/v1beta3/crypto/us/bars"

    def __init__(self, days: int = 180, timeout_s: int = 20,
                 timeframe: str = "1Day", stock_feed: str = ""):
        self.days = int(days)
        self.timeout_s = timeout_s
        self.timeframe = timeframe
        self.stock_feed = stock_feed or os.environ.get("TRADE_ALPACA_FEED", "iex")
        self._cache: dict[str, list[Bar]] = {}

    # -- credentials ------------------------------------------------------
    @staticmethod
    def credentials() -> tuple:
        key = (os.environ.get("ALPACA_API_KEY_ID")
               or os.environ.get("APCA_API_KEY_ID") or "").strip()
        secret = (os.environ.get("ALPACA_API_SECRET_KEY")
                  or os.environ.get("APCA_API_SECRET_KEY") or "").strip()
        return key, secret

    def _headers(self) -> dict:
        key, secret = self.credentials()
        if not key or not secret:
            raise FeedNotConfigured(
                "TRADE_DATA_SOURCE=alpaca needs credentials. They are free from "
                "alpaca.markets — set ALPACA_API_KEY_ID and "
                "ALPACA_API_SECRET_KEY in your shell. Do not put them in a file "
                "in this repository."
            )
        return {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "User-Agent": "ai-village-trading/1.0",
        }

    # -- symbols ----------------------------------------------------------
    @staticmethod
    def is_crypto(symbol: str) -> bool:
        """The village spells crypto with a quote currency: BTC-USD, not BTC."""
        return "-" in str(symbol)

    @staticmethod
    def pair(symbol: str) -> str:
        return str(symbol).upper().replace("-", "/")

    # -- fetching ---------------------------------------------------------
    def series(self, symbol: str) -> list[Bar]:
        if symbol in self._cache:
            return self._cache[symbol]
        upper = symbol.upper()
        crypto = self.is_crypto(upper)
        if crypto:
            url = self.CRYPTO
            params = {"symbols": self.pair(upper), "timeframe": self.timeframe,
                      "limit": self.days}
        else:
            url = self.STOCKS.format(symbol=upper)
            params = {"timeframe": self.timeframe, "limit": self.days,
                      "feed": self.stock_feed}

        try:
            payload = _get_json(url, params, self._headers(), self.timeout_s) or {}
        except FeedHTTPError as exc:
            if exc.status in (401, 403):
                raise FeedNotConfigured(
                    f"alpaca refused the credentials for {symbol} ({exc.status}). "
                    "Check ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY"
                    + ("; the free stock tier is 'iex', and 'sip' needs a "
                       "subscription" if not crypto else "")
                ) from exc
            if exc.status == 429:
                raise FeedNotConfigured(
                    f"alpaca rate-limited the request for {symbol}. The free tier "
                    "allows 200 requests a minute; a village with a large universe "
                    "on a short tick interval will hit it."
                ) from exc
            raise FeedNotConfigured(
                f"alpaca returned {exc.status} for {symbol}: {exc.body}"
            ) from exc

        rows = payload.get("bars")
        if isinstance(rows, dict):          # the crypto endpoint keys by pair
            rows = rows.get(self.pair(upper)) or []
        rows = rows or []

        bars: list[Bar] = []
        for row in rows:
            close = row.get("c")
            if close is None:
                continue
            bars.append(
                Bar(
                    symbol=upper,       # the village's spelling, not the venue's
                    as_of=_parse_stamp(row.get("t")),
                    open=D(row.get("o") if row.get("o") is not None else close),
                    high=D(row.get("h") if row.get("h") is not None else close),
                    low=D(row.get("l") if row.get("l") is not None else close),
                    close=D(close),
                    volume=D(row.get("v") or 0),
                )
            )
        if len(bars) < 30:
            raise FeedNotConfigured(
                f"alpaca returned only {len(bars)} usable bars for {symbol} "
                f"({'crypto' if crypto else self.stock_feed}); refusing to run "
                "indicators on a truncated history"
            )
        bars = bars[-self.days :]
        self._cache[symbol] = bars
        return bars


def _parse_stamp(value) -> datetime:
    """RFC3339 out of an API, into an aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(tz=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ChainFeed:
    """Several feeds, tried in order, per symbol.

    A real universe is rarely all one thing. Point the village at a crypto
    exchange and it prices BTC-USD perfectly and cannot price SPY at all —
    and because a feed refuses rather than degrades, one unlistable symbol
    stopped every firm, including the ones it had nothing to do with.

    So: `TRADE_DATA_SOURCE=ccxt,yahoo` asks the exchange first and falls back
    to Yahoo for what the exchange has never heard of.

    **It remembers which feed answered.** `origin(symbol)` says where a bar
    came from, because the dangerous version of this is the silent one: a
    village pricing crypto off a real exchange and equities off a random
    number generator, with nothing on the page to tell them apart. Mixing a
    synthetic feed into a chain with real ones logs a warning for exactly that
    reason — it is legitimate for a demo and a lie in production.
    """

    def __init__(self, feeds: Sequence[MarketFeed]):
        self.feeds = list(feeds)
        if not self.feeds:
            raise FeedNotConfigured("a chain needs at least one feed")
        self._origin: dict[str, str] = {}

        kinds = {getattr(f, "name", "?") for f in self.feeds}
        if "synthetic" in kinds and len(kinds) > 1:
            warnings.warn(
                "this feed chain mixes synthetic prices with real ones "
                f"({', '.join(sorted(kinds))}). Some firms will be trading "
                "invented data and the leaderboard will compare them to firms "
                "that are not. Check `trade status` for which feed served what.",
                stacklevel=2,
            )

    @property
    def name(self) -> str:
        return "+".join(getattr(f, "name", "?") for f in self.feeds)

    def origin(self, symbol: str) -> Optional[str]:
        """Which feed actually served this symbol, once it has been asked."""
        return self._origin.get(symbol)

    @property
    def origins(self) -> dict:
        return dict(self._origin)

    def series(self, symbol: str) -> list[Bar]:
        refusals = []
        for feed in self.feeds:
            try:
                bars = feed.series(symbol)
            except FeedNotConfigured as exc:
                refusals.append(f"{getattr(feed, 'name', '?')}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - a feed's own error tree
                refusals.append(f"{getattr(feed, 'name', '?')}: {type(exc).__name__}: {exc}")
                continue
            if bars:
                self._origin[symbol] = getattr(feed, "name", "?")
                return bars
            refusals.append(f"{getattr(feed, 'name', '?')}: returned nothing")

        raise FeedNotConfigured(
            f"no feed in the chain could price {symbol} — "
            + "; ".join(refusals)
        )


def _one_feed(source: str, config: DataConfig) -> MarketFeed:
    source = (source or "synthetic").strip().lower()
    if source == "synthetic":
        return SyntheticFeed(seed=config.seed, days=config.history_days)
    if source == "csv":
        return CsvFeed(config.csv_dir)
    if source == "yahoo":
        return YahooFeed(days=config.history_days)
    if source == "alpaca":
        return AlpacaFeed(days=config.history_days)
    if source == "ccxt":
        return CcxtFeed(
            exchange=os.environ.get("TRADE_CCXT_EXCHANGE", "binance"),
            days=config.history_days,
            timeframe=os.environ.get("TRADE_CCXT_TIMEFRAME", "1d"),
        )
    raise FeedNotConfigured(
        f"unknown TRADE_DATA_SOURCE={source!r}; expected synthetic, csv, yahoo, "
        "alpaca or ccxt, or several separated by commas"
    )


def build_feed(config: DataConfig) -> MarketFeed:
    parts = [p for p in (config.source or "synthetic").split(",") if p.strip()]
    feeds = [_one_feed(part, config) for part in parts] or [
        _one_feed("synthetic", config)
    ]
    return feeds[0] if len(feeds) == 1 else ChainFeed(feeds)
