"""Real history, and the two things it does not come with.

The generated tape in ``market.py`` is a demo toy. This is the replacement:
daily bars from a CSV under ``data/market/``, downloaded once with yfinance
and then read from disk forever. Cached deliberately — a harness whose results
depend on what a vendor's API returned this morning is a harness whose results
cannot be compared to yesterday's, and the whole point of the crucible is
comparing runs.

The CSV layout is the village's own — ``data/market/<SYMBOL>.csv`` with a
``date,open,high,low,close,volume`` header — so ``src/trading/data/feeds.py``
reads the same files, and so you can drop in data from any source at all
without touching this module.

**What a price feed does not have.** The scouts read two things that are not
in an OHLCV row:

``vix``        real, when ``VIX.csv`` is present. ``^VIX`` goes back to 1990,
               which covers SPY's whole history, and it is worth the second
               download: without it every VIX branch in the genome is dead
               code and the survival number you get back is measuring a plain
               momentum strategy wearing this one's name.
``sentiment``  **a proxy, always.** There is no free daily news-sentiment
               series going back to 1990. What this computes is a normalised
               blend of recent return and volatility — it correlates with mood
               because mood follows price, and it is not news. The feed says
               so in ``sentiment_source``, the harness prints it on every run,
               and ``describe()`` repeats it, because the one way this becomes
               dishonest is quietly.

If VIX is missing, the feed does **not** silently fill zeros. Zero VIX is a
tape where the market is never frightened, every fear rule is unreachable, and
nothing says anything went wrong. It either falls back to a labelled realised
volatility proxy or refuses, and which one is your choice, not its.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean, pstdev
from typing import Iterator, Optional, Sequence

from src.config import REPO_ROOT
from src.money import D, ZERO
from src.trading.models import price

from .market import Bar, MarketFeed, WindowFeed

MARKET_DIR = REPO_ROOT / "data" / "market"
COLUMNS = ("date", "open", "high", "low", "close", "volume")


class RealDataMissing(RuntimeError):
    """The tape is not on disk and could not be fetched. Says how to get it."""


# =========================================================================
# getting the data onto disk
# =========================================================================
def download_to_csv(
    symbols: Sequence[str] = ("SPY", "^VIX"),
    start: str = "1993-01-29",
    end: Optional[str] = None,
    directory: Path = MARKET_DIR,
) -> dict:
    """Fetch once, write the village's CSV format, never fetch again.

    The default start is SPY's first trading day. Asking for 1990 does not
    give you three more years of SPY; it gives you the same series with a
    misleading label on the request.
    """
    try:
        import yfinance  # noqa: F401  (imported for the error message's sake)
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise RealDataMissing(
            "yfinance is not installed. `pip install yfinance`, or put a "
            f"date,open,high,low,close,volume CSV at {directory}/SPY.csv yourself."
        ) from exc

    import yfinance as yf

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    end = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = {}

    for symbol in symbols:
        frame = yf.download(
            symbol, start=start, end=end, progress=False, auto_adjust=False
        )
        if frame is None or frame.empty:
            raise RealDataMissing(
                f"yfinance returned no rows for {symbol!r} ({start} to {end}). "
                "Check the network, or supply the CSV by hand."
            )
        if hasattr(frame.columns, "levels"):  # yfinance returns a MultiIndex
            frame.columns = [c[0] for c in frame.columns]

        name = symbol.lstrip("^").upper()
        path = directory / f"{name}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for stamp, row in frame.iterrows():
                writer.writerow(
                    [
                        stamp.strftime("%Y-%m-%d"),
                        f"{float(row['Open']):.6f}",
                        f"{float(row['High']):.6f}",
                        f"{float(row['Low']):.6f}",
                        f"{float(row['Close']):.6f}",
                        int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
                    ]
                )
        written[name] = path
        print(f"✓ {name}: {len(frame)} rows -> {path}")
    return written


def _read_csv(path: Path) -> list:
    if not path.exists():
        raise RealDataMissing(
            f"no CSV at {path}. Run:\n"
            "    python -m hive_mind.real_feed --download\n"
            f"or write one yourself with the header {','.join(COLUMNS)}"
        )
    rows = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            raw = row.get("date") or row.get("Date")
            if not raw:
                continue
            try:
                stamp = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            try:
                rows.append(
                    {
                        "date": stamp,
                        "open": D(row.get("open") or row.get("Open")),
                        "high": D(row.get("high") or row.get("High")),
                        "low": D(row.get("low") or row.get("Low")),
                        "close": D(row.get("close") or row.get("Close")),
                        "volume": D(row.get("volume") or row.get("Volume") or 0),
                    }
                )
            except Exception:  # noqa: BLE001 - one unparseable row is not the file
                continue
    if not rows:
        raise RealDataMissing(f"{path} had no usable rows")
    rows.sort(key=lambda r: r["date"])
    return rows


# =========================================================================
# the feed
# =========================================================================
class RealFeed(MarketFeed):
    """Daily bars from disk, with VIX aligned by date and sentiment declared.

    Subclasses ``MarketFeed`` so ``window()`` still returns a ``WindowFeed``
    with the rest of history physically removed — the walk-forward argument
    rests on that object, and a real tape does not get an exemption from it.
    """

    def __init__(
        self,
        symbol: str = "SPY",
        directory: Path = MARKET_DIR,
        vix_symbol: str = "VIX",
        allow_vix_proxy: bool = False,
        sentiment_window: int = 20,
    ):
        self.symbol = symbol.upper()
        self.directory = Path(directory)
        self.vix_symbol = vix_symbol.upper()
        self.allow_vix_proxy = bool(allow_vix_proxy)
        self.sentiment_window = int(sentiment_window)
        self.vix_source = ""
        self.sentiment_source = ""
        self.name = f"real:{self.symbol}"
        self._bars: Optional[list] = None

    # -- the two derived series -------------------------------------------
    def _vix_by_date(self) -> dict:
        path = self.directory / f"{self.vix_symbol}.csv"
        if path.exists():
            self.vix_source = f"real ^VIX ({path.name})"
            return {r["date"].date(): r["close"] for r in _read_csv(path)}
        if not self.allow_vix_proxy:
            raise RealDataMissing(
                f"no {path} — and without it every VIX rule in the genome is dead "
                "code, so the survival number would be measuring a plain momentum "
                "strategy wearing this one's name. Either:\n"
                "    python -m hive_mind.real_feed --download   (fetches ^VIX too)\n"
                "or pass allow_vix_proxy=True to run on a labelled realised-"
                "volatility proxy instead, knowing that is what you are doing."
            )
        self.vix_source = "PROXY: 20-day realised volatility, annualised"
        return {}

    def _proxy_vix(self, closes: list, index: int, window: int = 20) -> Decimal:
        """Annualised realised vol, scaled to sit where VIX sits."""
        if index < window:
            return D(15)
        chunk = [float(c) for c in closes[index - window : index + 1]]
        rets = [(b - a) / a for a, b in zip(chunk, chunk[1:]) if a]
        if len(rets) < 2:
            return D(15)
        return D(str(round(pstdev(rets) * (252 ** 0.5) * 100, 2)))

    def _sentiment(self, closes: list, index: int) -> Decimal:
        """A price-derived mood proxy in roughly [-2, +2]. **Not news.**

        Mood follows price, so a blend of recent return and recent volatility
        tracks it well enough to exercise the genome's thresholds. What it
        cannot do is tell you anything a price series did not already say, and
        anyone reading a result off this feed needs to know that — which is
        why the label travels with the feed rather than living in a comment.
        """
        window = self.sentiment_window
        if index < window:
            return ZERO
        chunk = [float(c) for c in closes[index - window : index + 1]]
        rets = [(b - a) / a for a, b in zip(chunk, chunk[1:]) if a]
        if len(rets) < 2:
            return ZERO
        spread = pstdev(rets) or 1e-9
        score = mean(rets) / spread
        return D(str(round(max(-2.0, min(2.0, score * 1.4)), 3)))

    # -- bars -------------------------------------------------------------
    def bars(self) -> list:
        if self._bars is not None:
            return self._bars

        rows = _read_csv(self.directory / f"{self.symbol}.csv")
        vix_by_date = self._vix_by_date()
        closes = [r["close"] for r in rows]
        self.sentiment_source = (
            f"PROXY: {self.sentiment_window}-day return/volatility blend — not news"
        )

        bars = []
        missing_vix = 0
        for index, row in enumerate(rows):
            vix = vix_by_date.get(row["date"].date())
            if vix is None:
                missing_vix += 1
                vix = self._proxy_vix(closes, index)
            bars.append(
                Bar(
                    symbol=self.symbol,
                    as_of=row["date"],
                    open=price(row["open"]),
                    high=price(row["high"]),
                    low=price(row["low"]),
                    close=price(row["close"]),
                    volume=row["volume"],
                    index=index,
                    day=row["date"].strftime("%Y-%m-%d"),
                    vix=D(vix),
                    sentiment=self._sentiment(closes, index),
                    # The real tape does not come with a regime label, and
                    # inventing one would hand the reports a fact nobody knows.
                    regime="",
                )
            )
        if vix_by_date and missing_vix:
            self.vix_source += f" ({missing_vix} day(s) filled from the proxy)"
        self._bars = bars
        return bars

    def window(self, start: int, end: int) -> WindowFeed:
        return WindowFeed(self, start, end)

    def profile(self) -> dict:
        """The two scales this feed's numbers are on, named stably.

        Stable is the operative word: these strings go into a certificate and
        get compared to a live feed's later, so they name the *method* rather
        than the run. Change the method and the comparison should fail, which
        is the entire mechanism.
        """
        self.bars()
        return {
            "feed": f"real:{self.symbol}",
            "vix": "real:^VIX" if self.vix_source.startswith("real") else "proxy:realised-vol-20",
            "sentiment": f"proxy:returns-vol-{self.sentiment_window}",
        }

    def describe(self) -> str:
        self.bars()
        first, last = self._bars[0], self._bars[-1]
        return (
            f"{self.symbol}: {len(self._bars)} bars, {first.day} to {last.day}\n"
            f"  vix       : {self.vix_source}\n"
            f"  sentiment : {self.sentiment_source}"
        )

    def __len__(self) -> int:
        return len(self.bars())

    def __iter__(self) -> Iterator:
        return iter(self.bars())


# =========================================================================
# stress regimes, on a tape nobody generated
# =========================================================================
@dataclass(frozen=True)
class RealWindow:
    """One slice of real history, named by the years it covers."""

    label: str
    start: int
    end: int


def real_stress_windows(feed: MarketFeed, count: int, span: int = 252) -> list:
    """``count`` windows of the real tape, for phase 2b.

    On generated data "regimes it never saw" means fresh seeds. There is only
    one real tape, so it means disjoint slices of it instead — a year at a
    time, spread evenly across the whole history, so a genome has to survive
    1998 and 2008 and 2022 rather than the average of them.

    Slices, not resamples. A bootstrap would destroy the autocorrelation that
    decides whether a trend-follower lives, which is the one property being
    tested.
    """
    bars = feed.bars()
    total = len(bars)
    count = max(1, int(count))
    if total < span * 2:
        raise RealDataMissing(
            f"{total} bars cannot be cut into {span}-bar stress windows; "
            "load more history"
        )

    usable = total - span
    step = max(1, usable // count)
    out = []
    for i in range(count):
        start = min(usable, i * step)
        end = start + span
        label = f"{bars[start].day[:4]}-{bars[end - 1].day[:4]}"
        out.append((label, feed.window(start, end), 10_000 + i))
    return out


def stress_source(feed: MarketFeed, span: int = 252):
    """A ``stress_markets`` hook for ``WalkForwardLock``."""

    def source(count: int) -> list:
        return real_stress_windows(feed, count, span=span)

    return source


# =========================================================================
# CLI: get the data, then look at it
# =========================================================================
def main(argv=None) -> int:  # pragma: no cover - a thin wrapper over the above
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m hive_mind.real_feed",
        description="Download real history into data/market/, or describe what is there.",
    )
    parser.add_argument("--download", action="store_true", help="fetch SPY and ^VIX")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", default="1993-01-29", help="SPY's first trading day")
    parser.add_argument("--end", default=None)
    parser.add_argument(
        "--allow-vix-proxy",
        action="store_true",
        help="run without VIX.csv, on a labelled realised-volatility proxy",
    )
    args = parser.parse_args(argv)

    if args.download:
        download_to_csv(("SPY", "^VIX"), start=args.start, end=args.end)

    feed = RealFeed(symbol=args.symbol, allow_vix_proxy=args.allow_vix_proxy)
    try:
        print(feed.describe())
    except RealDataMissing as exc:
        print(f"\n{exc}")
        return 1
    bars = feed.bars()
    print(f"\nfirst: {bars[0]}\nlast : {bars[-1]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "MARKET_DIR",
    "RealDataMissing",
    "RealFeed",
    "RealWindow",
    "download_to_csv",
    "real_stress_windows",
    "stress_source",
]
