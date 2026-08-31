"""News — what the village can see beyond its own price series.

Every analyst in this system reads the same thing: bars. `technical` reads
them one way, `macro` another, `fundamental` a third, but a village that only
ever sees price is a village that finds out about a bankruptcy, a merger or a
central bank from the shape of a candle, hours late. This is the layer that
lets something else in.

**It is a publisher, not a scanner, and that distinction is a safety rule.**
A scanner is a stranger's file: parsed, never imported, run in a sandbox, and
handed a context with the money stripped out — no store, no cash, no network.
That is why a scanner cannot fetch anything. Fetching needs the network, so
fetching lives here, in code that can be read and reviewed, and the sandbox
stays intact. The split is: trusted code fetches, and everything downstream
treats what it fetched as untrusted data.

**Nothing here can place an order.** A source produces `Reading`s, which land
on the same board the scanners use, which a firm hears at one seat of a debate
that still has to get past the trader, the risk manager, the conscience, the
venue and the gate. A source screaming BUY moves one vote at one firm.

Four things this module refuses to do, each because the village has already
been hurt by the opposite:

*Fetch per tick.* The loop runs every sixty seconds and the bar changes every
hour. Fetching per tick is the churn that turned 77 decisions into 666 fills
and started the rate-limit failures. Sources run once per bar, full stop.

*Block the tick.* A hang here would stall trading. Every fetch has a hard
timeout, every failure is caught, and a source that dies is silence rather
than an exception — the same rule the feed follows for an unpriceable symbol.

*Score with anything that cannot be replayed.* The scoring below is a crude
deterministic lexicon, and it is deliberately crude: the same headline gives
the same score on every machine and in every replay, which is what makes it
possible to ever measure whether this helped. An LLM reader is the obvious
upgrade and it belongs behind this same interface, scored the same way, once
there is a scorecard to judge it with.

*Sound confident.* Confidence is capped low. A headline is weak evidence about
a price, coverage volume is not conviction, and the `signal_trust` gene lets
each firm decide independently how much of this it wants — the bonds desk and
the memecoin desk should not be forced to agree about Reddit.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Protocol, Sequence

from ..money import D, ZERO
from .signals import Reading

#: Seconds any one source may take before it is abandoned for this bar.
FETCH_TIMEOUT_S = float(os.environ.get("TRADE_NEWS_TIMEOUT_S", "8") or 8)

#: The most confidence a headline may ever carry into a debate. A story is a
#: reason to look, not a forecast.
MAX_CONFIDENCE = D(30)

#: Reddit and most feeds refuse an unidentified client, and rightly.
USER_AGENT = os.environ.get(
    "TRADE_NEWS_USER_AGENT",
    "mvv-village/1.0 (research trading bot; contact via repository)",
)

# A deliberately small, deliberately obvious lexicon. It is not sentiment
# analysis and does not pretend to be — it is a reproducible way to turn a
# headline into a number so that the question "did this help?" can be asked at
# all. Weights are small because headlines are weak evidence.
# **Paired, deliberately.** The first version had `outperform` and no
# `underperform` — the exact opposite word, absent — and the very first live
# fetch returned two headlines using it ("Is XOM Underperforming the Energy
# Sector?", "Is Procter & Gamble Stock Underperforming the Nasdaq?"), both
# scoring zero. An asymmetric lexicon does not merely miss stories, it reads
# the market as more bullish than it is, because the words it happens to know
# lean one way.
#
# So every entry below has its opposite, and adding one without the other is
# a bug even when it looks harmless. The pairs are also the reason to distrust
# this whole approach: it is keyword counting, it cannot read "1 Step Behind
# in AI. In Chipmaking, It's Now 1 Step Ahead", and no amount of vocabulary
# will fix that. A language model behind this same interface is the real
# answer; the scorecard is what will say whether either is worth having.
BULLISH = {
    "beats": 3, "surge": 3, "soars": 3, "rally": 2, "upgrade": 3, "raises": 2,
    "record": 2, "profit": 2, "growth": 2, "wins": 2, "approval": 3,
    "breakthrough": 3, "outperform": 3, "acquisition": 2, "buyback": 3,
    "rise": 2, "rising": 2, "climbs": 2, "gains": 2, "jumps": 3, "soar": 3,
    "higher": 2, "rebound": 2, "boost": 2, "strong": 2, "expands": 2,
    "bullish": 3, "optimis": 2, "beat": 3, "top": 1, "tops": 2,
}
BEARISH = {
    "misses": -3, "plunge": -3, "slump": -3, "crash": -4, "downgrade": -3,
    "cuts": -2, "loss": -2, "lawsuit": -3, "probe": -3, "recall": -3,
    "bankruptcy": -4, "fraud": -4, "layoffs": -2, "halts": -3, "warns": -2,
    "sinks": -3, "tumbles": -3,
    "underperform": -3, "fall": -2, "falling": -2, "falls": -2, "drops": -2,
    "declines": -2, "lower": -2, "slides": -2, "weak": -2, "shrinks": -2,
    "bearish": -3, "pessimis": -2, "miss": -3, "sell-off": -3, "selloff": -3,
    "worst": -2, "risk": -1, "concerns": -2, "delay": -2,
}

_WORD = re.compile(r"[a-z']+")

#: What a headline calls a company, versus what the ticker calls it. Nobody
#: writes "PG missed earnings" — they write "Procter & Gamble". The first live
#: fetch returned "Is Procter & Gamble Stock Underperforming the Nasdaq?" and
#: matched nothing at all, because the village only knew the four-letter name.
#:
#: Deliberately hand-written and deliberately short: it covers what this
#: village actually trades, and a wrong alias is worse than a missing one —
#: mapping a common word onto a ticker floods the board with confident noise.
#: `TRADE_NEWS_ALIASES` extends it as `SYM=name|name,SYM=name`.
ALIASES: dict = {
    "AAPL": ("apple",), "MSFT": ("microsoft",), "NVDA": ("nvidia",),
    "AMZN": ("amazon",), "TSLA": ("tesla",), "AMD": ("amd",),
    "JNJ": ("johnson & johnson", "johnson and johnson"),
    "PG": ("procter & gamble", "procter and gamble"),
    "KO": ("coca-cola", "coca cola"), "XOM": ("exxon", "exxonmobil"),
    "VZ": ("verizon",), "SPY": ("s&p 500", "s&p500"), "QQQ": ("nasdaq 100",),
    "DIA": ("dow jones",), "GLD": ("gold",), "SLV": ("silver",),
    "USO": ("crude oil", "oil"), "TLT": ("treasury", "treasuries"),
    "BTC-USD": ("bitcoin",), "ETH-USD": ("ethereum",), "SOL-USD": ("solana",),
    "DOGE-USD": ("dogecoin",), "SHIB-USD": ("shiba inu",),
    "PEPE-USD": ("pepe coin",), "WIF-USD": ("dogwifhat",),
}


def _aliases_for(symbol: str) -> tuple:
    extra = os.environ.get("TRADE_NEWS_ALIASES", "")
    for part in (p for p in extra.split(",") if "=" in p):
        key, _, names = part.partition("=")
        if key.strip().upper() == symbol.upper():
            return tuple(n.strip() for n in names.split("|") if n.strip())
    return ALIASES.get(symbol.upper(), ())


#: Why the last fetch failed, per source. Silence and failure look identical
#: from outside, and this village has lost days to that confusion — the feed
#: froze twice for hours while reporting nothing wrong. A source that cannot
#: reach the internet must say so, not shrug.
LAST_ERROR: dict = {}


def _get(url: str, timeout: float = FETCH_TIMEOUT_S,
         label: str = "") -> Optional[bytes]:
    """One GET, or None. Never raises — but always records why.

    TLS verification comes from `data/feeds.py`, which already solved this for
    the price feed and wrote down why: Python from python.org does not use the
    macOS keychain, so `urllib` cannot verify anything until certifi is found.
    The first version of this function did its own plain `urlopen` and every
    source returned nothing, silently, which read exactly like "there is no
    news today". Reusing the shared context also means verification is never
    accidentally disabled in one place and not the other.
    """
    from .data.feeds import _ssl_context

    key = label or url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(  # noqa: S310 - fixed, declared hosts
            req, timeout=timeout, context=_ssl_context()
        ) as response:
            LAST_ERROR.pop(key, None)
            return response.read()
    except urllib.error.HTTPError as exc:
        LAST_ERROR[key] = f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - a dead source is news, not a crash
        LAST_ERROR[key] = f"{type(exc).__name__}: {str(exc)[:80]}"
    return None


def score_text(text: str) -> tuple[Decimal, int]:
    """A headline's tone, and how many words carried it.

    Returns (score, hits). Deterministic on purpose: the same words give the
    same number on every machine and in every replay, which is the only way
    the scorecard can ever ask whether reading the news was worth it.
    """
    words = _WORD.findall((text or "").lower())
    total, hits = 0, 0
    for word in words:
        weight = _weight(word)
        if weight:
            total += weight
            hits += 1
    return D(total), hits


def _weight(word: str) -> int:
    """The lexicon's opinion of one word, matching on its stem.

    Exact matching looked fine and was not. The first live fetch pulled the
    headline "Johnson & Johnson Stock: Is JNJ Outperforming the Consumer
    Staples Sector?" — the symbol matched, and the story scored zero, because
    the lexicon holds `outperform` and the headline says `outperforming`.
    Financial headlines are written in participles: beats/beating, plunge/
    plunging, cut/cuts/cutting. An exact match reads almost none of them.

    Prefix matching is crude and occasionally wrong — `recalls` a memory as
    well as a product — but a lexicon this small is already crude, and the
    honest fix for both is a reader that understands language, behind this
    same interface, scored by the same scorecard.
    """
    for table in (BULLISH, BEARISH):
        for stem, weight in table.items():
            if word.startswith(stem):
                return weight
            # English drops a trailing `e` before `-ing`: plunge -> plunging,
            # surge -> surging, probe -> probing. Prefix matching alone reads
            # "plunges" and "plunged" and misses "plunging", which is the one
            # a headline is most likely to use.
            if len(stem) > 4 and stem.endswith("e") and word.startswith(stem[:-1]):
                return weight
    return 0


@dataclass
class Story:
    """One headline, and where it came from."""

    title: str
    source: str
    url: str = ""

    def mentions(self, symbol: str, aliases: Sequence[str] = ()) -> bool:
        """Does this story name the symbol?

        Word-boundary matched, because substring matching makes `KO` fire on
        "Tokyo" and `ALL` on almost everything — a bug that would fill the
        board with confident noise about names nobody wrote about.
        """
        haystack = f" {(self.title or '').lower()} "
        for needle in (symbol, *aliases):
            token = str(needle or "").strip().lower()
            if token and re.search(rf"\b{re.escape(token)}\b", haystack):
                return True
        return False


class NewsSource(Protocol):
    name: str

    def fetch(self, symbols: Sequence[str]) -> list:
        """Stories for this bar. Never raises; returns [] when it cannot."""


class RssSource:
    """Headlines from any RSS or Atom feed. Free, no key, no rate limit worth
    worrying about, and the format has not changed in twenty years.

    Parsed with the standard library rather than an XML library that resolves
    external entities, because a feed is untrusted input and XXE is a real
    thing to hand a trading system.
    """

    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url

    def fetch(self, symbols: Sequence[str]) -> list:
        raw = _get(self.url, label=self.name)
        if not raw:
            return []
        try:
            import xml.etree.ElementTree as ET

            parser = ET.XMLParser()
            root = ET.fromstring(raw, parser=parser)
        except Exception:  # noqa: BLE001 - a malformed feed is no news
            return []
        out = []
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1].lower()
            if tag != "item" and tag != "entry":
                continue
            title = ""
            link = ""
            for child in node:
                child_tag = child.tag.rsplit("}", 1)[-1].lower()
                if child_tag == "title":
                    title = (child.text or "").strip()
                elif child_tag == "link":
                    link = (child.get("href") or child.text or "").strip()
            if title:
                out.append(Story(title=title, source=self.name, url=link))
        return out


class RedditSource:
    """Titles from a subreddit's public JSON. No key, no OAuth, no signup.

    `https://reddit.com/r/<sub>/hot.json` is a documented public endpoint. It
    wants a real User-Agent and it will rate-limit an anonymous client that
    hammers it — which is why this runs once per bar like everything else.
    """

    def __init__(self, subreddit: str, limit: int = 50, listing: str = "hot"):
        self.subreddit = subreddit.strip().lstrip("r/")
        self.limit = int(limit)
        self.listing = listing
        self.name = f"reddit:{self.subreddit}"

    def fetch(self, symbols: Sequence[str]) -> list:
        url = (f"https://www.reddit.com/r/{self.subreddit}/{self.listing}.json"
               f"?limit={self.limit}")
        raw = _get(url, label=self.name)
        if not raw:
            return []
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
            children = payload.get("data", {}).get("children", []) or []
        except Exception:  # noqa: BLE001
            return []
        out = []
        for child in children:
            data = child.get("data") or {}
            title = (data.get("title") or "").strip()
            if title:
                out.append(Story(
                    title=title,
                    source=self.name,
                    url=f"https://reddit.com{data.get('permalink', '')}",
                ))
        return out


@dataclass
class Digest:
    """What one bar's reading of the news produced."""

    readings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    stories: int = 0
    sources_ok: int = 0
    sources_failed: list = field(default_factory=list)


def read_the_news(sources: Sequence, symbols: Sequence[str],
                  aliases: Optional[dict] = None) -> Digest:
    """Fetch every source once, and turn what they said into readings."""
    aliases = aliases or {}
    digest = Digest()
    stories: list = []
    for source in sources:
        try:
            got = source.fetch(symbols)
        except Exception as exc:  # noqa: BLE001 - a broken source is not a broken tick
            digest.sources_failed.append(f"{getattr(source, 'name', '?')}: {exc}")
            continue
        if got:
            digest.sources_ok += 1
            stories.extend(got)
        else:
            name = getattr(source, "name", "?")
            digest.sources_failed.append(
                f"{name}: {LAST_ERROR.get(name, 'returned no stories')}")
    digest.stories = len(stories)

    for symbol in symbols:
        upper = str(symbol).upper()
        known = aliases.get(upper) or _aliases_for(upper)
        named = [s for s in stories if s.mentions(upper, known)]
        if not named:
            continue
        total, hits = ZERO, 0
        for story in named:
            score, hit = score_text(story.title)
            total += score
            hits += hit
        if hits == 0:
            continue        # written about, but in words this cannot read

        # Confidence rises with how much was written and how loaded it was,
        # and stops well short of certainty. A story is a reason to look.
        confidence = min(MAX_CONFIDENCE, D(6) * D(len(named)))
        # Score is the tone, scaled to the -100..100 the board expects, and
        # clamped so one hysterical headline cannot dominate a debate.
        score = max(D(-100), min(D(100), total * D(8)))
        digest.readings.append(Reading(
            symbol=upper,
            score=score,
            confidence=confidence,
            note=(f"{len(named)} story(s): "
                  + "; ".join(s.title[:60] for s in named[:2])),
        ))
    return digest


def build_sources(spec: str = "") -> list:
    """Sources from `TRADE_NEWS_SOURCES`, or a sensible free default.

    Format is comma-separated: `reddit:wallstreetbets`, `reddit:stocks`, or a
    bare URL for an RSS feed. Everything here is free and keyless on purpose —
    a paid source should have to prove it beats these before it costs anything.
    """
    spec = (spec or os.environ.get("TRADE_NEWS_SOURCES", "")).strip()
    if not spec:
        return [
            RedditSource("wallstreetbets"),
            RedditSource("stocks"),
            RssSource("yahoo-finance",
                      "https://finance.yahoo.com/news/rssindex"),
        ]
    out: list = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        if part.lower().startswith("reddit:"):
            out.append(RedditSource(part.split(":", 1)[1]))
        elif part.startswith("http"):
            name = part.split("//", 1)[-1].split("/", 1)[0]
            out.append(RssSource(name, part))
    return out


class NewsDesk:
    """Runs the sources once per bar and publishes what they said."""

    name = "news"

    def __init__(self, board, sources: Optional[Sequence] = None):
        self.board = board
        self.sources = list(sources) if sources is not None else build_sources()

    def run(self, market, as_of=None) -> list:
        as_of = as_of if as_of is not None else market.as_of()
        if as_of is None or not self.sources:
            return []
        if self.board.published(self.name, as_of):
            return []           # already read the news on this bar

        symbols = [str(s).upper() for s in getattr(market, "symbols", [])]
        if not symbols:
            return []
        digest = read_the_news(self.sources, symbols)

        # Publish even when empty, so `published()` is true and the sources are
        # not re-fetched sixty times inside one bar.
        self.board.publish(self.name, digest.readings, as_of)

        notes = []
        if digest.readings:
            notes.append(
                f"news: {len(digest.readings)} reading(s) from {digest.stories} "
                f"story(s) across {digest.sources_ok} source(s): "
                + ", ".join(f"{r.symbol} {r.score}" for r in digest.readings[:5])
            )
        else:
            notes.append(
                f"news: {digest.stories} story(s) from {digest.sources_ok} "
                "source(s), none naming a symbol this village trades"
            )
        for failure in digest.sources_failed[:3]:
            notes.append(f"news source quiet — {failure}")
        return notes


__all__ = [
    "Digest", "NewsDesk", "NewsSource", "RedditSource", "RssSource", "Story",
    "build_sources", "read_the_news", "score_text",
]
