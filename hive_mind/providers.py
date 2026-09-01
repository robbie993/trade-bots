"""Where the tape comes from — several sources, one file on disk.

Every provider here does the same job: fetch daily OHLCV and write
``data/market/<SYMBOL>.csv`` in the village's format. Nothing downstream knows
or cares which one ran. That indirection is not architecture for its own sake —
it is the lesson from the first attempt, where the whole harness was wired to
one vendor and one blocked endpoint stopped everything.

    yfinance   no key, full history, the easiest start. Unofficial, so it
               breaks whenever Yahoo changes something.
    stooq      no key, no library, plain CSV over HTTPS. The most robust free
               source there is: one GET, no auth, no SDK to go stale. Daily
               only, and it does not carry ^VIX under that name.
    tiingo     a key, 1,000 requests a day. A real API with a contract.
    alphavantage  a key, 25 requests a day — enough for two symbols, once.
    csv        you already have the data. Kaggle, a broker export, anything.

``auto`` tries them in order and reports which one answered, because a run
whose data source you cannot name later is a run you cannot reproduce.

**Caching is the point, not an optimisation.** Fetch once, read from disk
forever. A harness whose numbers depend on what a vendor returned this morning
cannot be compared to yesterday's run, and comparing runs is the entire
purpose of the crucible. Nothing here re-fetches a file that already exists
unless you ask it to.

None of these are endorsed by anyone. Two of them scrape. Read the terms of
whichever you use, and do not build a business on an endpoint that has no
contract with you.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from src.config import REPO_ROOT

MARKET_DIR = REPO_ROOT / "data" / "market"
COLUMNS = ("date", "open", "high", "low", "close", "volume")
USER_AGENT = "hive-mind-crucible/1.0 (research harness)"
TIMEOUT = 30


class ProviderError(RuntimeError):
    """One provider could not answer. Carries which, and why."""


@dataclass(frozen=True)
class Row:
    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


def _get(url: str, headers: Optional[dict] = None) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}") from exc
    except Exception as exc:  # noqa: BLE001 - DNS, TLS, proxies, timeouts
        raise ProviderError(f"{urllib.parse.urlsplit(url).netloc}: {exc}") from exc


# =========================================================================
# providers
# =========================================================================
def from_yfinance(symbol: str, start: str, end: str, **_) -> list:
    """Yahoo, through the library. No key; unofficial, so it breaks sometimes."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ProviderError("yfinance is not installed (`pip install yfinance`)") from exc

    frame = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
    if frame is None or frame.empty:
        raise ProviderError(f"yfinance returned no rows for {symbol!r}")
    if hasattr(frame.columns, "levels"):  # yfinance hands back a MultiIndex
        frame.columns = [c[0] for c in frame.columns]
    return [
        Row(
            date=stamp.strftime("%Y-%m-%d"),
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=float(row["Volume"]) if row["Volume"] == row["Volume"] else 0.0,
        )
        for stamp, row in frame.iterrows()
    ]


STOOQ_SYMBOLS = {"SPY": "spy.us", "^VIX": "^vix", "VIX": "^vix", "QQQ": "qqq.us"}


def from_stooq(symbol: str, start: str, end: str, **_) -> list:
    """One GET, a CSV back, no key and no SDK. The least breakable of these."""
    ticker = STOOQ_SYMBOLS.get(symbol.upper(), f"{symbol.lower()}.us")
    url = (
        "https://stooq.com/q/d/l/?"
        + urllib.parse.urlencode(
            {"s": ticker, "d1": start.replace("-", ""), "d2": end.replace("-", ""), "i": "d"}
        )
    )
    body = _get(url).decode("utf-8", "replace")
    if not body.lower().startswith("date"):
        raise ProviderError(f"stooq did not return a CSV for {ticker!r} (got {body[:60]!r})")
    return _parse_csv_rows(csv.DictReader(io.StringIO(body)), "stooq")


def from_tiingo(symbol: str, start: str, end: str, api_key: str = "", **_) -> list:
    """A real API with a contract. 1,000 requests a day on the free tier."""
    if not api_key:
        raise ProviderError("tiingo needs a key — set TIINGO_API_KEY")
    url = (
        f"https://api.tiingo.com/tiingo/daily/{urllib.parse.quote(symbol)}/prices?"
        + urllib.parse.urlencode({"startDate": start, "endDate": end, "format": "json"})
    )
    payload = json.loads(_get(url, {"Authorization": f"Token {api_key}"}))
    if not payload:
        raise ProviderError(f"tiingo returned no rows for {symbol!r}")
    return [
        Row(
            date=str(item["date"])[:10],
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            volume=float(item.get("volume") or 0),
        )
        for item in payload
    ]


def from_alphavantage(symbol: str, start: str, end: str, api_key: str = "", **_) -> list:
    """25 requests a day. Enough to populate two symbols, once, carefully."""
    if not api_key:
        raise ProviderError("alphavantage needs a key — set ALPHAVANTAGE_API_KEY")
    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode(
        {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "full",
            "apikey": api_key,
        }
    )
    payload = json.loads(_get(url))
    series = payload.get("Time Series (Daily)")
    if not series:
        # The free tier answers a blown quota with a 200 and a note, which is
        # the worst possible shape: it looks like data until you index it.
        note = payload.get("Note") or payload.get("Information") or payload
        raise ProviderError(f"alphavantage returned no series: {str(note)[:120]}")
    rows = [
        Row(
            date=day,
            open=float(v["1. open"]),
            high=float(v["2. high"]),
            low=float(v["3. low"]),
            close=float(v["4. close"]),
            volume=float(v.get("5. volume") or 0),
        )
        for day, v in series.items()
        if start <= day <= end
    ]
    if not rows:
        raise ProviderError(f"alphavantage had nothing between {start} and {end}")
    return sorted(rows, key=lambda r: r.date)


def from_csv(symbol: str, start: str, end: str, source: Optional[Path] = None, **_) -> list:
    """You already have the data — a Kaggle dump, a broker export, anything."""
    if source is None:
        raise ProviderError("the csv provider needs --source pointing at a file")
    path = Path(source)
    if not path.exists():
        raise ProviderError(f"no file at {path}")
    with path.open(newline="") as handle:
        rows = _parse_csv_rows(csv.DictReader(handle), str(path))
    return [r for r in rows if start <= r.date <= end]


def _parse_csv_rows(reader, where: str) -> list:
    """Tolerant about case and column order; strict about there being rows."""
    out = []
    for raw in reader:
        row = {(k or "").strip().lower(): v for k, v in raw.items()}
        date = (row.get("date") or row.get("timestamp") or "")[:10]
        if not date or not date[0].isdigit():
            continue
        try:
            out.append(
                Row(
                    date=date,
                    open=float(row.get("open") or 0),
                    high=float(row.get("high") or 0),
                    low=float(row.get("low") or 0),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # one unreadable row is not a reason to lose the file
    if not out:
        raise ProviderError(f"{where} had no usable rows")
    return sorted(out, key=lambda r: r.date)


PROVIDERS: dict = {
    "yfinance": from_yfinance,
    "stooq": from_stooq,
    "tiingo": from_tiingo,
    "alphavantage": from_alphavantage,
    "csv": from_csv,
}

# Tried in this order by `auto`: no key first, then the keyed ones, because a
# provider that needs no configuration is a provider that cannot be
# misconfigured.
AUTO_ORDER = ("stooq", "yfinance", "tiingo", "alphavantage")


# =========================================================================
# fetching to disk
# =========================================================================
def write_csv(rows: Sequence[Row], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row.date,
                    f"{row.open:.6f}",
                    f"{row.high:.6f}",
                    f"{row.low:.6f}",
                    f"{row.close:.6f}",
                    int(row.volume),
                ]
            )
    return path


def fetch(
    symbol: str,
    provider: str = "auto",
    start: str = "1993-01-29",
    end: Optional[str] = None,
    directory: Path = MARKET_DIR,
    force: bool = False,
    api_key: str = "",
    source: Optional[Path] = None,
    on_note: Callable[[str], None] = print,
) -> dict:
    """Get one symbol onto disk. Returns what was written and by whom."""
    end = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = symbol.lstrip("^").upper()
    path = Path(directory) / f"{name}.csv"

    if path.exists() and not force:
        on_note(f"· {name}: already at {path} (use --force to refetch)")
        return {"symbol": name, "path": path, "provider": "cache", "rows": None}

    order = AUTO_ORDER if provider == "auto" else (provider,)
    failures = []
    for candidate in order:
        if candidate not in PROVIDERS:
            raise ProviderError(
                f"unknown provider {candidate!r}; have {', '.join(sorted(PROVIDERS))}"
            )
        try:
            rows = PROVIDERS[candidate](
                symbol, start, end, api_key=api_key, source=source
            )
        except ProviderError as exc:
            failures.append(f"{candidate}: {exc}")
            continue
        write_csv(rows, path)
        on_note(f"✓ {name}: {len(rows)} rows from {candidate} -> {path}")
        return {"symbol": name, "path": path, "provider": candidate, "rows": len(rows)}

    raise ProviderError(
        f"no provider could fetch {symbol!r}:\n  " + "\n  ".join(failures)
        + "\n\nEvery free source is someone else's server having a good day. If they "
        "are all\nrefusing, download a CSV by hand — Kaggle has S&P 500 dailies — and "
        "load it with:\n  python -m hive_mind.providers --symbol SPY --provider csv "
        "--source path/to.csv"
    )


def main(argv=None) -> int:  # pragma: no cover - a wrapper over fetch()
    import argparse
    import os

    parser = argparse.ArgumentParser(
        prog="python -m hive_mind.providers",
        description="Fetch daily bars into data/market/<SYMBOL>.csv.",
    )
    parser.add_argument("--symbol", action="append", default=None)
    parser.add_argument("--provider", default="auto", choices=["auto", *sorted(PROVIDERS)])
    parser.add_argument("--start", default="1993-01-29", help="SPY's first trading day")
    parser.add_argument("--end", default=None)
    parser.add_argument("--dir", default=str(MARKET_DIR))
    parser.add_argument("--source", default=None, help="for --provider csv")
    parser.add_argument("--force", action="store_true", help="refetch even if cached")
    args = parser.parse_args(argv)

    symbols = args.symbol or ["SPY", "^VIX"]
    key = os.environ.get("TIINGO_API_KEY") or os.environ.get("ALPHAVANTAGE_API_KEY") or ""
    failed = 0
    for symbol in symbols:
        try:
            fetch(
                symbol,
                provider=args.provider,
                start=args.start,
                end=args.end,
                directory=Path(args.dir),
                force=args.force,
                api_key=key,
                source=Path(args.source) if args.source else None,
            )
        except ProviderError as exc:
            failed += 1
            print(f"✗ {symbol}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "AUTO_ORDER",
    "COLUMNS",
    "MARKET_DIR",
    "PROVIDERS",
    "ProviderError",
    "Row",
    "fetch",
    "from_alphavantage",
    "from_csv",
    "from_stooq",
    "from_tiingo",
    "from_yfinance",
    "write_csv",
]
