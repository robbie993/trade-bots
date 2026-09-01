"""News sentiment — real, and only ever about today.

The scouts read a sentiment number. Until now it has been a proxy computed
from the price series, labelled as one everywhere it appears. RSS gives you
something better: actual headlines, from actual newsrooms, free and without a
key.

It also gives you a problem that is easy to miss and expensive to find.

**RSS has no history.** A feed hands back the last few dozen headlines. It
cannot tell you how the market felt on 12 March 2009, and no free source can.
So real news sentiment is available for *today, and every day from today
onward* — and phases 1, 2 and 2b of the crucible need years of it. Those
phases will keep running on the proxy no matter what you install.

Which means a genome can be fitted against one sentiment distribution and
deployed reading another. ``fear_threshold = -0.5`` is a specific claim about
a specific scale; move to a different scorer and the same number fires at
different times, on different days, for different reasons — and nothing errors,
nothing logs, the equity curve just quietly becomes someone else's. That is
the failure this module exists to make impossible, and the defence is in
``lock.py``: a certificate records the sentiment source it was earned on, and
the engine refuses to run a genome against a source it was not measured with.

So the useful thing to do with this file is **start collecting now**.
``collect()`` appends today's score to ``data/market/SENTIMENT.csv``. Run it
daily and in six months you have six months of real sentiment history that was
recorded forward, never backfilled — which is the only kind that can honestly
appear in a backtest.

**The scorer is a lexicon, and a lexicon is weak.** Finance words with signs
on them, negation handling, and nothing else. It cannot read sarcasm, it does
not know that "beat expectations" is about the expectations, and it treats a
Bloomberg headline and a Reddit title as the same kind of object. It is here
because it has no dependencies and can be read in a sitting; if you want
better, replace ``score_text`` and re-certify, because changing the scorer
changes the distribution and voids the licence — which is the whole point.
"""

from __future__ import annotations

import csv
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Optional, Sequence

from src.config import REPO_ROOT

MARKET_DIR = REPO_ROOT / "data" / "market"
SENTIMENT_CSV = "SENTIMENT.csv"
USER_AGENT = "hive-mind-crucible/1.0 (research harness)"
TIMEOUT = 20

# Public RSS, no key, no registration. They rotate and go dead; the collector
# treats a silent feed as one missing source rather than a failed run.
FEEDS: dict = {
    "investing": "https://www.investing.com/rss/news_1063.rss",
    "yahoo": "https://finance.yahoo.com/news/rssindex",
    "wsj_markets": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
}

# A finance lexicon. Weights are ordinal, not calibrated — "plunge" is worse
# than "slip" and that is all this claims.
POSITIVE = {
    "surge": 2.0, "soar": 2.0, "rally": 1.8, "jump": 1.5, "gain": 1.2,
    "rise": 1.0, "climb": 1.2, "beat": 1.3, "record": 1.2, "growth": 1.0,
    "upgrade": 1.4, "bullish": 1.8, "optimism": 1.4, "rebound": 1.5,
    "recover": 1.3, "strong": 1.0, "profit": 1.0, "outperform": 1.5,
    "boost": 1.2, "high": 0.8, "up": 0.6, "top": 0.8, "win": 1.0,
}
NEGATIVE = {
    "plunge": 2.2, "crash": 2.5, "slump": 1.8, "tumble": 1.8, "sink": 1.6,
    "fall": 1.0, "drop": 1.2, "slide": 1.2, "slip": 0.8, "loss": 1.2,
    "miss": 1.3, "downgrade": 1.4, "bearish": 1.8, "fear": 1.8, "panic": 2.4,
    "selloff": 2.0, "recession": 2.2, "crisis": 2.2, "default": 2.0,
    "layoff": 1.6, "weak": 1.0, "warn": 1.4, "cut": 0.9, "low": 0.8,
    "down": 0.6, "risk": 0.8, "concern": 1.0, "volatile": 1.0, "tariff": 0.8,
}
NEGATORS = {"not", "no", "never", "without", "avoids", "avoid", "denies", "deny"}
WORD = re.compile(r"[a-z']+")


def score_text(text: str) -> float:
    """One headline, roughly in [-3, +3]. Not calibrated to anything."""
    words = WORD.findall((text or "").lower())
    total = 0.0
    for index, word in enumerate(words):
        weight = POSITIVE.get(word, 0.0) - NEGATIVE.get(word, 0.0)
        if not weight:
            continue
        if index and words[index - 1] in NEGATORS:
            weight = -weight
        total += weight
    return max(-3.0, min(3.0, total))


@dataclass
class Headline:
    source: str
    title: str
    score: float


@dataclass
class DailySentiment:
    """One day's reading, and the evidence for it."""

    date: str
    score: float
    headlines: int
    sources: list = field(default_factory=list)
    sample: list = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.date}: {self.score:+.3f} from {self.headlines} headline(s) "
            f"across {len(self.sources)} source(s)"
        )


# =========================================================================
# fetching
# =========================================================================
def _parse_rss(body: str, source: str) -> list:
    """Titles out of RSS or Atom, without a dependency.

    ``feedparser`` is better and this is deliberately not it: the harness
    should keep working when an optional package is missing, and a regex over
    <title> is enough to get the words a lexicon needs.
    """
    titles = re.findall(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    out = []
    for raw in titles[1:]:  # the first <title> is the channel's own name
        text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", raw, flags=re.S)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 8:
            out.append(Headline(source=source, title=text, score=score_text(text)))
    return out


def fetch_headlines(feeds: Optional[dict] = None, on_note=None) -> list:
    """Every headline the feeds will give up right now. Silence is not failure."""
    feeds = feeds if feeds is not None else FEEDS
    note = on_note or (lambda *_: None)
    out = []
    for name, url in feeds.items():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - every feed dies its own way
            note(f"  · {name}: unreachable ({str(exc)[:60]})")
            continue
        headlines = _parse_rss(body, name)
        note(f"  · {name}: {len(headlines)} headline(s)")
        out.extend(headlines)
    return out


def today_sentiment(feeds: Optional[dict] = None, on_note=None) -> DailySentiment:
    """Today's reading. Empty when nothing answered — never a cheerful zero."""
    headlines = fetch_headlines(feeds, on_note=on_note)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not headlines:
        return DailySentiment(date=date, score=0.0, headlines=0, sources=[])
    scores = [h.score for h in headlines]
    return DailySentiment(
        date=date,
        score=round(mean(scores), 4),
        headlines=len(headlines),
        sources=sorted({h.source for h in headlines}),
        sample=[h.title for h in sorted(headlines, key=lambda h: -abs(h.score))[:5]],
    )


# =========================================================================
# collecting it forward
# =========================================================================
def collect(directory: Path = MARKET_DIR, feeds: Optional[dict] = None, on_note=print) -> Optional[DailySentiment]:
    """Append today to ``SENTIMENT.csv``. Run it daily; that is the whole idea.

    Refuses to write a day twice, and refuses to write a day with no
    headlines behind it. A zero that means "the feeds were down" is
    indistinguishable, once written, from a zero that means "the news was
    balanced" — and one of those is data.
    """
    directory = Path(directory)
    path = directory / SENTIMENT_CSV
    reading = today_sentiment(feeds, on_note=on_note if on_note else None)

    if not reading.headlines:
        if on_note:
            on_note(
                "✗ no headlines from any feed — writing nothing. A zero here would be "
                "indistinguishable\n  from a genuinely balanced news day, and only one "
                "of those is data."
            )
        return None

    existing = {r["date"] for r in read_series(directory)}
    if reading.date in existing:
        if on_note:
            on_note(f"· {reading.date} already recorded — nothing to do")
        return reading

    directory.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["date", "score", "headlines", "sources"])
        writer.writerow(
            [reading.date, f"{reading.score:.4f}", reading.headlines, "|".join(reading.sources)]
        )
    if on_note:
        on_note(f"✓ {reading.summary()} -> {path}")
        for title in reading.sample:
            on_note(f"    {title[:88]}")
    return reading


def read_series(directory: Path = MARKET_DIR) -> list:
    path = Path(directory) / SENTIMENT_CSV
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return [r for r in csv.DictReader(handle) if r.get("date")]


# =========================================================================
# the part that stops it being used wrongly
# =========================================================================
@dataclass(frozen=True)
class Calibration:
    """How two sentiment scales compare, and whether they are interchangeable."""

    proxy_mean: float
    proxy_sd: float
    news_mean: float
    news_sd: float
    days: int

    @property
    def scale_ratio(self) -> float:
        return self.news_sd / self.proxy_sd if self.proxy_sd else float("inf")

    @property
    def interchangeable(self) -> bool:
        """Only when the spreads are within a quarter of each other."""
        return self.days >= 60 and 0.75 <= self.scale_ratio <= 1.33

    def summary(self) -> str:
        verdict = (
            "comparable" if self.interchangeable
            else ("too few days to say" if self.days < 60 else "DIFFERENT SCALES")
        )
        return (
            f"proxy  mean {self.proxy_mean:+.3f} sd {self.proxy_sd:.3f}\n"
            f"news   mean {self.news_mean:+.3f} sd {self.news_sd:.3f} "
            f"over {self.days} day(s)\n"
            f"ratio  {self.scale_ratio:.2f}x — {verdict}"
        )


def calibrate(proxy_values: Sequence, news_values: Sequence) -> Calibration:
    """Compare the two distributions a genome's thresholds could be read on.

    A threshold is a claim about a scale. ``fear_threshold = -0.5`` on a series
    with a standard deviation of 0.4 is a rare event; on one with a standard
    deviation of 1.2 it is a Tuesday. Swapping the source without checking this
    changes when every fear rule in the genome fires, and reports nothing.
    """
    proxy = [float(v) for v in proxy_values]
    news = [float(v) for v in news_values]
    return Calibration(
        proxy_mean=mean(proxy) if proxy else 0.0,
        proxy_sd=pstdev(proxy) if len(proxy) > 1 else 0.0,
        news_mean=mean(news) if news else 0.0,
        news_sd=pstdev(news) if len(news) > 1 else 0.0,
        days=min(len(proxy), len(news)),
    )


def main(argv=None) -> int:  # pragma: no cover - a wrapper over collect()
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m hive_mind.news",
        description="Score today's financial headlines and append them to SENTIMENT.csv.",
    )
    parser.add_argument("--dir", default=str(MARKET_DIR))
    parser.add_argument("--show", action="store_true", help="print what has been collected")
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    if args.show:
        series = read_series(directory)
        if not series:
            print(f"nothing collected yet at {directory / SENTIMENT_CSV}")
            return 1
        print(f"{len(series)} day(s) collected, {series[0]['date']} to {series[-1]['date']}")
        for row in series[-10:]:
            print(f"  {row['date']}  {float(row['score']):+.3f}  ({row['headlines']} headlines)")
        print(
            "\nThis series was recorded forward, never backfilled — which is the only\n"
            "kind that can honestly appear in a backtest. It becomes usable for the\n"
            "crucible at a few hundred days, and until then the proxy is what the\n"
            "phases run on."
        )
        return 0

    print("Fetching headlines...")
    reading = collect(directory)
    return 0 if reading and reading.headlines else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "Calibration",
    "DailySentiment",
    "FEEDS",
    "Headline",
    "MARKET_DIR",
    "SENTIMENT_CSV",
    "calibrate",
    "collect",
    "fetch_headlines",
    "read_series",
    "score_text",
    "today_sentiment",
]
