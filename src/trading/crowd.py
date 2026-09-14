"""Explicit crowd calls — who *said to buy it*, not who mentioned it.

The distinction this module exists for
--------------------------------------

Counting mentions measures attention. A name trending because it crashed is
mentioned constantly, and a mention counter reads that as bullish. What the
crowd-signal literature finds informative is narrower: an **explicit
directional call** — somebody saying they are buying, or selling, a named
asset. Attention and recommendation are different signals and mixing them is
how a sentiment feed ends up buying every disaster.

So this reads text and returns only calls it can point at: a symbol, a
direction, and the span of words that justified it. Everything else is
silence, which is the same answer every analyst in this village gives when it
has nothing.

What this module is not
-----------------------

**It is not wired to anything.** Nothing imports it into a firm, no analyst
seat reads it, and `config/firm_config.yaml` does not name it. It produces
readings for a scanner that a human may choose to enable, and a scanner
"informs a decision, it never makes one" — see `signals.py`. That is
deliberate: the sibling repo's verdict file records *"No scanner shipped on
purpose"*, and the one hand-written scanner universe that did look skilful
measured **+26.9%/yr of pure hindsight**.

**It is not evidence of an edge.** `PREREG_crowd_calls.md` is fixed in advance
and says what would have to be true. Until that has been run, this is
apparatus.

The null control is part of the module, not an afterthought
-----------------------------------------------------------

`shuffle_symbols` is here rather than in a test because the control is the
measurement. A crowd-call signal has two components that a naive backtest
cannot separate:

* **timing** — the crowd is loud on days when everything moves
* **selection** — the crowd names the *right* one

Only the second is an edge. `shuffle_symbols` reassigns each call to a
different symbol drawn from the same universe, holding the count and the
timestamp fixed, so the shuffled arm keeps all of the timing and none of the
selection. If the real arm does not beat it, what was measured was volatility
clustering with a Reddit-shaped mask over it.

This is the same construction as arm 4 in `PREREG_universe_discovery.md`, and
for the same reason stated there: the control has to be drawn from the same
pool as the treatment, or it measures the pool instead of the effect.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from ..money import D, ZERO, percent

#: How sure a single call makes us, before agreement between callers is
#: considered. Deliberately low: one stranger on the internet is one stranger
#: on the internet, and the confidence that reaches a debate should say so.
#: Confidence grows with *agreeing* callers in `readings_from`, not with the
#: vehemence of any one of them.
ONE_CALL_CONFIDENCE = D(12)

#: The ceiling a crowd seat may ever reach, however many people agree. Capped
#: below the seats that compute a number from prices, because a hundred people
#: agreeing on a memecoin is a well-documented way to be wrong together.
MAX_CONFIDENCE = D(60)

#: One text is one person, so it casts at most one vote per symbol per
#: direction. "buying DOGE, buying DOGE, buying DOGE" in a single title is one
#: caller saying one thing three times, and counting it as three is how a
#: single excited post outvotes a subreddit.
#:
#: **A known limit, stated rather than implied.** Each *mention* of a ticker
#: takes only its nearest directional word, so a genuinely two-sided post —
#: "bought DOGE yesterday, selling it today" — is recorded as the nearer call
#: alone (a buy) rather than as both. Capturing both would need the mention to
#: appear twice. The bias is toward whichever verb the writer put closest to
#: the ticker, and it is not obviously in either direction; it is recorded here
#: because a reader comparing this to the `ONE_VOTE_PER` name would otherwise
#: reasonably assume the two-sided case is handled.
ONE_VOTE_PER = ("symbol", "direction")

BULLISH = (
    "buying", "bought", "buy", "longing", "longed", "long", "accumulating",
    "accumulated", "aping", "aped", "loading", "loaded", "scooping", "scooped",
    "adding", "added", "bullish", "moon", "mooning", "sending", "gem",
)

BEARISH = (
    "selling", "sold", "sell", "shorting", "shorted", "short", "dumping",
    "dumped", "exiting", "exited", "bearish", "rug", "rugged", "rugging",
    "avoid", "avoiding", "exit", "trimming", "trimmed",
)

#: Words that flip the call they precede. "not buying DOGE" is not a buy, and a
#: matcher without this reads every warning as an endorsement — which on this
#: asset class is most of the corpus.
NEGATORS = ("not", "never", "dont", "don't", "doesnt", "doesn't", "no",
            "stop", "stopped", "avoid", "wouldn't", "wouldnt", "isn't", "isnt")

#: How many words either side of a ticker a directional word may sit and still
#: be about it. Five is wide enough for "I am finally buying some DOGE today"
#: and narrow enough that two tickers in one sentence do not both collect the
#: same verb.
WINDOW_WORDS = 5

_WORD = re.compile(r"[A-Za-z']+|\$[A-Za-z]+")


@dataclass(frozen=True)
class Call:
    """One explicit directional statement about one symbol."""

    symbol: str
    direction: int          # +1 bullish, -1 bearish
    trigger: str            # the word that made it a call
    phrase: str             # the span that justified it, for auditing
    negated: bool = False

    @property
    def is_bullish(self) -> bool:
        return self.direction > 0


@dataclass
class CrowdReading:
    """What the crowd said about one symbol over one bar."""

    symbol: str
    bullish: int = 0
    bearish: int = 0
    calls: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.bullish + self.bearish

    @property
    def score(self) -> Decimal:
        """Net direction, -100 to +100. Zero when the crowd is split."""
        if not self.total:
            return ZERO
        return percent(D(self.bullish - self.bearish) / D(self.total) * D(100))

    @property
    def confidence(self) -> Decimal:
        """Rises with the number of *agreeing* callers, never with volume alone.

        A symbol with nine buys and nine sells has a score of zero and should
        not also carry the confidence of eighteen people: the crowd is loud and
        says nothing. So this counts the margin, not the turnout.
        """
        margin = abs(self.bullish - self.bearish)
        if not margin:
            return ZERO
        return min(MAX_CONFIDENCE, ONE_CALL_CONFIDENCE * D(margin))

    @property
    def note(self) -> str:
        sample = "; ".join(c.phrase for c in self.calls[:2])
        return (f"{self.bullish} buy / {self.bearish} sell call(s) "
                f"[{sample}]" if sample else "no explicit calls")


def _aliases(symbol: str) -> tuple:
    """The spellings of a symbol a person might actually type.

    `DOGE-USD` is never typed by a human; `DOGE` and `$DOGE` are. The pair
    suffix is stripped and both forms are matched. Returned rather than
    guessed at call sites so one place decides what counts as naming a symbol.
    """
    head = str(symbol).upper().split("-")[0].split("/")[0].strip()
    if not head:
        return ()
    return (head, f"${head}")


def extract_calls(text: str, universe: Sequence[str]) -> list:
    """Explicit directional calls in one piece of text.

    Returns a list of `Call`. A ticker with no directional word near it is not
    a call and does not appear — that is the whole point of the module.

    Matching is word-based rather than regex-over-the-raw-string so that
    `WINDOW_WORDS` means words, and so a ticker inside a longer word does not
    match: "SHIBA" is not a mention of SHIB, and a substring matcher says it
    is.
    """
    if not text or not universe:
        return []
    words = [w.lower().strip("'") for w in _WORD.findall(str(text))]
    if not words:
        return []

    lookup: dict = {}
    for symbol in universe:
        for alias in _aliases(symbol):
            lookup.setdefault(alias.lower(), symbol)

    out: list = []
    seen: set = set()
    for i, word in enumerate(words):
        symbol = lookup.get(word)
        if symbol is None:
            continue
        lo, hi = max(0, i - WINDOW_WORDS), min(len(words), i + WINDOW_WORDS + 1)

        # **The nearest directional word wins, not the first one.** Scanning
        # left to right gave "buying DOGE and selling PEPE" two *buy* calls:
        # "buying" sits inside PEPE's window and came first by index, so PEPE
        # collected DOGE's verb. Distance is what decides which ticker a verb
        # is about.
        best = None
        for j in range(lo, hi):
            if j == i:
                continue
            trigger = words[j]
            direction = 1 if trigger in BULLISH else -1 if trigger in BEARISH else 0
            if not direction:
                continue
            distance = abs(j - i)
            if best is None or distance < best[0]:
                best = (distance, j, trigger, direction)
        if best is None:
            continue

        _, j, trigger, direction = best
        # A negator in the two words before the trigger flips it.
        negated = any(words[k] in NEGATORS for k in range(max(0, j - 2), j))
        if negated:
            direction = -direction

        key = (symbol, direction)
        if key in seen:
            continue
        seen.add(key)
        out.append(Call(
            symbol=symbol,
            direction=direction,
            trigger=trigger,
            phrase=" ".join(words[max(0, min(i, j) - 1):max(i, j) + 2]),
            negated=negated,
        ))
    return out


def readings_from(texts: Iterable[str], universe: Sequence[str]) -> dict:
    """Aggregate many texts into one `CrowdReading` per symbol that was called.

    Symbols nobody called explicitly are **absent**, not zero. A symbol with no
    calls is silence, and the debate already knows how to hear that; handing it
    back a zero-score, zero-confidence row would make "nobody mentioned it"
    indistinguishable from "the crowd is evenly split", which are opposite
    states.
    """
    out: dict = {}
    for text in texts or ():
        for call in extract_calls(text, universe):
            reading = out.setdefault(call.symbol, CrowdReading(symbol=call.symbol))
            if call.is_bullish:
                reading.bullish += 1
            else:
                reading.bearish += 1
            reading.calls.append(call)
    return out


def shuffle_symbols(calls: Sequence[Call], universe: Sequence[str],
                    seed: Optional[int] = None) -> list:
    """The null control: same calls, same count, reassigned to other symbols.

    **This is not a test helper, it is half the measurement.** A crowd-call
    strategy has a timing component and a selection component, and only
    selection is an edge. The crowd is loudest when everything is moving, so an
    arm that trades when the crowd is loud inherits volatility clustering it did
    nothing to earn.

    Holding the count and the ordering fixed while moving each call to a
    *different* symbol from the same universe keeps all of the timing and
    destroys all of the selection. A real arm that cannot beat this one has not
    found anything, however good its absolute return looks.

    Reassignment is to a different symbol wherever the universe allows one, so
    the control is genuinely a control rather than partly the treatment. With a
    single-symbol universe no reassignment exists and the calls are returned
    unchanged — a fact the caller must handle, because in that case the control
    is meaningless rather than merely weak.
    """
    pool = [str(s) for s in dict.fromkeys(universe or ())]
    if len(pool) < 2:
        return list(calls)
    rng = random.Random(seed)
    out = []
    for call in calls:
        alternatives = [s for s in pool if s != call.symbol] or pool
        out.append(Call(
            symbol=rng.choice(alternatives),
            direction=call.direction,
            trigger=call.trigger,
            phrase=call.phrase,
            negated=call.negated,
        ))
    return out


__all__ = [
    "BEARISH", "BULLISH", "Call", "CrowdReading", "MAX_CONFIDENCE",
    "NEGATORS", "ONE_CALL_CONFIDENCE", "ONE_VOTE_PER", "extract_calls", "readings_from",
    "shuffle_symbols",
]
