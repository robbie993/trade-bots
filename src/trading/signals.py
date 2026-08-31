"""Scanners — the bots that spot things rather than trade them.

Most of what people have sitting in a folder called `bots` is not a strategy.
It is a scanner, a screener, a whale watcher, a ranker: something that reads the
market and *names a symbol*. Run through the strategy adapter one of those is
useless — it never returns an order, so the firm proposes nothing, and a file
that is working exactly as written looks broken.

So it gets its own seat. A scanner publishes a **reading**:

    symbol      what it is talking about
    score       -100 (bearish) to +100 (bullish)
    confidence  0 to 100, where 0 is silence and not neutrality
    note        why, in its own words

and any firm that lists the ``signals`` analyst hears it in the debate, next to
its own technical, fundamental and macro seats. That is the whole relationship:

    a scanner informs a decision. It never makes one.

**Why this is a smaller blast radius than the adapter.** A strategy bot returns
orders, and the village's job is to review them. A scanner returns a number, and
there is no path at all from a row in ``signals`` to a fill. The score enters a
debate, the debate reaches the trader, the trader's proposal meets the risk
manager, the conscience, the venue and the gate — every one of them unchanged
and none of them aware a scanner exists. A scanner that publishes +100 on
everything moves one vote at one seat of one firm and buys nothing.

**A stale reading is silence.** The analyst reads only rows stamped with the bar
it is standing on. A scanner that crashed, that was deleted, that was never
wired up correctly — all of them go quiet on the next bar rather than voting
from the grave. There is no grace period, because a grace period is exactly how
a dead process keeps its seat at the table.

**Running the file is opt-in, same as the adapter.** A scanner runs because
``config/firm_config.yaml`` names it under ``scanners:``. The court and
``recruit-watch`` still only parse; they gained nothing here. And a scanner
that raises, hangs, or returns nonsense costs one tick's readings and nothing
else — the tick carries on and every firm listening simply hears silence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Sequence

from ..money import D, ZERO, percent
from .adapter import TIMEOUT_SECONDS, AdapterError, Context, call, load
from .models import Signal

# The names a scanner may define, in the order they are tried. Deliberately
# disjoint from the strategy adapter's: a file defining `propose` is telling you
# it wants to trade, and should be wired up as a firm's bot instead.
ENTRY_POINTS = ("scan", "signals", "rank", "watch", "publish")

# More than this in one pass is a runaway loop, not a screen.
MAX_READINGS = 200

# A scanner that does not say how sure it is gets the strength of its own score,
# capped below the seats that actually compute a confidence. An unstated
# confidence is not a high one.
IMPLIED_CONFIDENCE_CAP = D("75")

NOTE_LIMIT = 400


class ScannerError(ValueError):
    """The scanner config could not be read. Never raised into a tick."""


# =========================================================================
# what a scanner is, in config
# =========================================================================
@dataclass(frozen=True)
class ScannerSpec:
    """One entry under ``scanners:`` in the firm config.

    ``universe`` empty means "whatever the village is already watching", which
    is the right default: a screener wants the whole board, and listing the
    symbols twice is how the two lists drift apart.
    """

    name: str
    path: Path
    universe: tuple = ()
    enabled: bool = True

    @classmethod
    def from_mapping(cls, name: str, raw) -> "ScannerSpec":
        if isinstance(raw, str):
            raw = {"bot": raw}
        if not isinstance(raw, dict):
            raise ScannerError(
                f"scanner {name!r} must be a path or a mapping, got {type(raw).__name__}"
            )
        path = str(raw.get("bot") or raw.get("path") or raw.get("file") or "").strip()
        if not path:
            raise ScannerError(f"scanner {name!r} has no `bot:` path — nothing to run")

        universe = raw.get("universe") or raw.get("symbols") or ()
        if isinstance(universe, str):
            universe = [s.strip() for s in universe.split(",") if s.strip()]
        enabled = raw.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("0", "false", "no", "off")

        return cls(
            name=str(name),
            path=Path(path),
            universe=tuple(str(s).upper() for s in universe),
            enabled=bool(enabled),
        )


def load_scanner_specs(path=None, config=None) -> list:
    """Read the ``scanners:`` block of the firm config.

    A village with no scanners is the normal case, so a missing file and a
    missing block are both an empty list rather than an error. A *malformed*
    entry still raises — a screener that silently does not run is worse than
    one that refuses to start.
    """
    from .config import TradingConfig
    from .firms.spec import parse_config

    cfg = config or TradingConfig()
    target = Path(path or cfg.firms_config)
    if not target.exists():
        return []
    try:
        raw = parse_config(target)
    except Exception as exc:  # noqa: BLE001 - the firm loader reports this properly
        raise ScannerError(f"{target}: {exc}") from exc

    block = raw.get("scanners") if isinstance(raw, dict) else None
    if isinstance(block, str) and block.strip() in ("", "{}", "[]", "null", "~"):
        # The fallback YAML parser has no flow-mapping syntax, so `scanners: {}`
        # reaches here as a string. An empty block means no scanners, in every
        # dialect, and must not read as a broken one.
        block = None
    if not block:
        return []
    if not isinstance(block, dict):
        raise ScannerError(f"{target}: `scanners:` must be a mapping of name -> bot")
    return [ScannerSpec.from_mapping(name, value) for name, value in block.items()]


# =========================================================================
# what came back
# =========================================================================
@dataclass(frozen=True)
class Reading:
    symbol: str
    score: Decimal
    confidence: Decimal
    note: str = ""


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _reading(symbol, score, confidence, note, universe) -> tuple:
    """One returned item into a Reading, or a complaint saying why not."""
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return None, "a reading with no symbol"
    if universe and symbol not in universe:
        return None, f"{symbol} is not in this scanner's universe"

    if score is None:
        return None, f"{symbol}: no score — a name with no direction is not a vote"
    try:
        score = percent(_clamp(D(score), D("-100"), D("100")))
    except (InvalidOperation, TypeError, ValueError):
        return None, f"{symbol}: score {score!r} is not a number"

    if confidence is None:
        confidence = min(IMPLIED_CONFIDENCE_CAP, abs(score))
    else:
        try:
            confidence = percent(_clamp(D(confidence), ZERO, D("100")))
        except (InvalidOperation, TypeError, ValueError):
            return None, f"{symbol}: confidence {confidence!r} is not a number"

    return Reading(symbol, score, confidence, str(note or "")[:NOTE_LIMIT]), ""


def _fields(raw) -> tuple:
    get = raw.get if isinstance(raw, dict) else (
        lambda key, default=None: getattr(raw, key, default)
    )
    symbol = get("symbol", None) or get("ticker", None) or get("asset", None)
    score = get("score", None)
    if score is None:
        score = get("signal", None)
    if score is None:
        score = get("rating", None)
    confidence = get("confidence", None)
    if confidence is None:
        confidence = get("conviction", None)
    note = get("note", None) or get("reason", None) or get("rationale", None) or ""
    return symbol, score, confidence, note


def to_readings(result, universe: Sequence[str] = ()) -> tuple:
    """Validate whatever the scanner returned. Returns (readings, complaints).

    Accepts the shapes people actually write a screener in:

        {"AAPL": 80, "MSFT": -20}                     symbol -> score
        {"AAPL": {"score": 80, "confidence": 60}}     symbol -> reading
        [{"symbol": "AAPL", "score": 80}, ...]        a list of readings
        {"symbol": "AAPL", "score": 80}               one reading

    A bare list of tickers is refused: a name with no direction cannot vote,
    and inventing a direction for it would be the system guessing about money.
    """
    universe = tuple(str(s).upper() for s in universe)
    if result is None:
        return [], []

    items: list = []
    if isinstance(result, dict):
        if "symbol" in result or "ticker" in result or "asset" in result:
            items = [result]
        else:
            for symbol, value in result.items():
                if isinstance(value, dict):
                    _, score, confidence, note = _fields(value)
                    items.append({"symbol": symbol, "score": score,
                                  "confidence": confidence, "note": note})
                else:
                    items.append({"symbol": symbol, "score": value})
    elif isinstance(result, (list, tuple)):
        items = list(result)
    else:
        return [], [f"expected readings, got {type(result).__name__}"]

    readings, complaints = [], []
    for raw in items[:MAX_READINGS]:
        if isinstance(raw, str):
            complaints.append(
                f"{raw.upper()}: a ticker with no score — say how bullish, "
                "or the village cannot tell a buy from a sell"
            )
            continue
        symbol, score, confidence, note = _fields(raw)
        reading, why = _reading(symbol, score, confidence, note, universe)
        if reading is None:
            complaints.append(why)
        else:
            readings.append(reading)

    if len(items) > MAX_READINGS:
        complaints.append(
            f"returned {len(items)} readings; only the first {MAX_READINGS} were read"
        )
    return readings, complaints


# =========================================================================
# the board
# =========================================================================
def stamp(as_of) -> str:
    """The market bar as a stable string. The clock is never used."""
    return "" if as_of is None else str(as_of)


class SignalBoard:
    """Published readings, and the one consensus a firm actually hears.

    Every read is filtered to a single bar. There is no "most recent reading"
    accessor on purpose: the only question a firm may ask is "what did the
    scanners say about this symbol, on this bar", and anything else would
    reintroduce the stale vote this design exists to prevent.
    """

    def __init__(self, db):
        self.db = db

    # -- writing ----------------------------------------------------------
    def publish(self, publisher: str, readings: Sequence[Reading], as_of) -> int:
        """Store one scanner's readings for one bar. Returns rows written."""
        at = stamp(as_of)
        written = 0
        for reading in readings:
            try:
                self.db.insert(
                    "signals",
                    {
                        "publisher": str(publisher)[:60],
                        "symbol": reading.symbol,
                        "score": reading.score,
                        "confidence": reading.confidence,
                        "note": reading.note[:NOTE_LIMIT],
                        "as_of": at,
                    },
                )
                written += 1
            except Exception:  # noqa: BLE001 - an un-migrated database has no board
                return written
        return written

    def mark_silent(self, publisher: str, as_of) -> None:
        """Record that a publisher ran on this bar and had nothing to say.

        `published()` asks whether a row exists, so publishing an empty list
        wrote nothing and left it False — and a publisher with nothing to say
        re-derived its own silence on every tick, sixty times an hour, logging
        the same three lines each time. The scribe did exactly that from the
        moment it shipped.

        Silence is a real answer and needs somewhere to live. It goes in the
        same table under the empty symbol, which no lookup can collide with:
        `latest()` matches on an upper-cased ticker, and nothing upper-cases to
        the empty string.
        """
        try:
            self.db.insert("signals", {
                "publisher": str(publisher)[:60],
                "symbol": "",
                "score": "0",
                "confidence": "0",
                "note": "ran, nothing to report",
                "as_of": stamp(as_of),
            })
        except Exception:  # noqa: BLE001 - a missed marker costs a repeat, not a tick
            pass

    def published(self, publisher: str, as_of) -> bool:
        """Has this scanner already spoken for this bar?

        A daily bar lasts many ticks, and a deterministic scanner asked twice
        gives the same answer twice. Asking once per bar keeps the table the
        size of the history rather than the size of the uptime.
        """
        try:
            row = self.db.query_one(
                "SELECT id FROM signals WHERE publisher = ? AND as_of = ? LIMIT 1",
                (str(publisher)[:60], stamp(as_of)),
            )
        except Exception:  # noqa: BLE001
            return False
        return row is not None

    # -- reading ----------------------------------------------------------
    def latest(self, symbol: str, as_of) -> list:
        """Every publisher's newest reading on this symbol, on this bar."""
        at = stamp(as_of)
        if not at:
            return []
        try:
            rows = self.db.query(
                "SELECT * FROM signals WHERE symbol = ? AND as_of = ? ORDER BY id",
                (str(symbol).upper(), at),
            ) or []
        except Exception:  # noqa: BLE001
            return []
        newest: dict = {}
        for row in rows:                     # ordered oldest first: last wins
            newest[str(row["publisher"])] = row
        return list(newest.values())

    def reading(self, symbol: str, as_of, publishers=None,
                exclude=None) -> Optional[Signal]:
        """The combined view of the publishers asked for, or None.

        **`publishers` and `exclude` are what keep the tool belt a belt.** Every
        publisher used to be averaged into one number, so a firm could not tell
        a headline from a moving average from a bankruptcy postmortem — and
        because `signal_trust` is a single gene, it could not trust one and
        distrust another. It had to take all of them at the same weight or none
        of them at all.

        That is wrong in both directions. A news feed may be worth hearing on
        memecoins and worthless on Treasuries; a price scanner is the reverse of
        that on nothing in particular. Which of them a desk believes is exactly
        the sort of question the evolver settles on held-out bars, and it cannot
        settle it while they arrive pre-mixed.

        So each seat names the publishers it listens to, and each seat carries
        its own trust gene. Passing neither argument keeps the old behaviour —
        everything, averaged — which is what a firm listing plain `signals`
        with no news desk running still gets.

        Within a seat the scores are still a confidence-weighted average, so a
        publisher that says "80, but I am only 10% sure" moves the number about
        as much as it deserves to. When publishers inside one seat disagree
        about *direction* the confidence is halved rather than the score
        flipped — the same asymmetry the technical analyst uses, and for the
        same reason: contradiction is a reason to size down, not a reason to be
        certain of the opposite.
        """
        rows = self.latest(symbol, as_of)
        if publishers is not None:
            wanted = {str(p) for p in publishers}
            rows = [r for r in rows if str(r["publisher"]) in wanted]
        if exclude:
            unwanted = {str(p) for p in exclude}
            rows = [r for r in rows if str(r["publisher"]) not in unwanted]
        heard = []
        for row in rows:
            try:
                score = D(row["score"])
                confidence = D(row["confidence"])
            except (InvalidOperation, TypeError, ValueError, KeyError):
                continue
            if confidence > 0:
                heard.append((str(row["publisher"]), score, confidence))
        if not heard:
            return None

        weight = sum((c for _, _, c in heard), ZERO)
        score = percent(sum((s * c for _, s, c in heard), ZERO) / weight)
        confidence = percent(weight / D(len(heard)))
        signs = {1 if s > 0 else -1 if s < 0 else 0 for _, s, _ in heard}
        if 1 in signs and -1 in signs:
            confidence = percent(confidence / D(2))

        who = ", ".join(f"{name} {s:+.0f}" for name, s, _ in heard[:4])
        more = f" (+{len(heard) - 4} more)" if len(heard) > 4 else ""
        return Signal(
            analyst="signals",
            symbol=str(symbol).upper(),
            score=score,
            confidence=confidence,
            note=f"{len(heard)} scanner(s): {who}{more}",
        )

    def recent(self, limit: int = 30, publisher: str = "") -> list:
        """Whatever was published lately, for a page or the CLI."""
        try:
            if publisher:
                return self.db.query(
                    "SELECT * FROM signals WHERE publisher = ? ORDER BY id DESC LIMIT ?",
                    (publisher, limit),
                ) or []
            return self.db.query(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
            ) or []
        except Exception:  # noqa: BLE001
            return []


# =========================================================================
# running them
# =========================================================================
class Scanners:
    """Runs the configured scanners once per bar and publishes what they say.

    Nothing here can fail a tick. A scanner that will not load, will not
    return, or returns rubbish produces a note in the tick report and no
    readings, and every firm listening for it hears the silence correctly.
    """

    def __init__(self, board: SignalBoard, specs: Sequence[ScannerSpec] = (),
                 timeout: float = TIMEOUT_SECONDS, error: str = ""):
        self.board = board
        self.specs = list(specs)
        self.timeout = timeout
        # A `scanners:` block that would not parse. Reported on every pass
        # rather than once: a screener nobody can see is not running is the
        # failure this whole module exists to stop happening quietly.
        self.error = error

    def run(self, market, as_of=None) -> list:
        """Publish this bar's readings. Returns human-readable notes."""
        as_of = as_of if as_of is not None else market.as_of()
        notes: list = [self.error] if self.error else []
        if as_of is None:
            if self.specs:
                notes.append("no bars yet: scanners have nothing to read")
            return notes

        for spec in self.specs:
            if not spec.enabled:
                continue
            if self.board.published(spec.name, as_of):
                continue                      # already spoke for this bar
            notes.extend(self._one(spec, market, as_of))
        return notes

    def _one(self, spec: ScannerSpec, market, as_of) -> list:
        universe = spec.universe or tuple(market.symbols)
        try:
            func = load(spec.path, entry_points=ENTRY_POINTS)
        except AdapterError as exc:
            return [f"{spec.name}: {exc}"]

        context = build_context(universe, market)
        result, error = call(func, context, self.timeout)
        if error:
            return [f"{spec.name}: {error}"]

        readings, complaints = to_readings(result, universe)
        notes = [f"{spec.name}: {why}" for why in complaints]
        if readings:
            written = self.board.publish(spec.name, readings, as_of)
            notes.append(
                f"{spec.name} published {written} reading(s): "
                + ", ".join(f"{r.symbol} {r.score:+.0f}" for r in readings[:5])
                + (f" (+{len(readings) - 5} more)" if len(readings) > 5 else "")
            )
        elif not complaints:
            notes.append(f"{spec.name}: nothing caught its eye")
        return notes


def build_context(symbols: Sequence[str], market, lookback: int = 250) -> Context:
    """What a scanner sees: prices, and no book at all.

    Deliberately the same object the strategy adapter hands a bot, with the
    money zeroed out. A scanner has no cash, no equity and no positions
    because it does not have a firm — it reads the market and says what it
    thinks, and giving it a balance to look at would only invite it to size
    something it cannot trade.
    """
    universe = tuple(str(s).upper() for s in symbols)
    closes, marks = {}, {}
    for symbol in universe:
        try:
            marks[symbol] = D(market.mark(symbol))
        except Exception:  # noqa: BLE001 - a symbol the feed lacks is not fatal
            marks[symbol] = None
        try:
            closes[symbol] = [D(c) for c in market.closes(symbol, lookback)]
        except Exception:  # noqa: BLE001
            closes[symbol] = []
    return Context(
        universe=universe,
        cash=ZERO,
        equity=ZERO,
        as_of=market.as_of(),
        _closes=closes,
        _marks=marks,
        _positions={},
    )


__all__ = [
    "ENTRY_POINTS", "IMPLIED_CONFIDENCE_CAP", "MAX_READINGS", "Reading",
    "ScannerError", "ScannerSpec", "Scanners", "SignalBoard", "build_context",
    "load_scanner_specs", "stamp", "to_readings",
]
