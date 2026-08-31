"""The scribe — the firm that only reads the dead.

Bankruptcy already writes a real postmortem for every firm that fails, and
those postmortems are good: they name whether costs or direction did the
damage, which symbol carried the loss, and what win rate the payoff ratio
actually needed. Six of them exist. **Nothing had ever read one.** They were
written to `trade_memory` and stayed there, so each firm died privately and
the village learned nothing it could act on.

This is the reader. It publishes to the same signal board the scanners use, so
a firm with the `signals` seat hears a warning about a name that has already
killed somebody, next to its own technical and macro seats. The relationship is
the scanners' relationship, unchanged and for the same reasons:

    the scribe informs a decision. It never makes one.

**Why it is built in rather than dropped in `bots/`.** A scanner is a stranger's
file: it is parsed, run in a sandbox, and handed a context with the money
stripped out — no store, no cash, no positions. That is a safety property worth
keeping, and it is exactly why a scanner cannot read `trade_memory`. The scribe
needs the ledger, so it lives here, where it can be read, instead of being given
a hole in the sandbox.

**It refuses to speak on one firm's word.** This is the part that matters. With
six bankruptcies the temptation is to publish six confident warnings, and every
one of them would be a single firm's bad fortnight dressed as a pattern. A
symbol earns a reading only when *independent* firms lost money on it, because
one firm losing on WIF is an anecdote and two firms losing on WIF is the
beginning of evidence.

Measured on the village's actual history the answer is that there is no
evidence at all: all six postmortems name a worst symbol, and **no symbol is
named twice**. So the scribe currently publishes nothing, and says so. That is
the correct output, not a broken one — the whole village has produced 28
closed-trade bars, and every postmortem it reads already opens by saying the
sample is too small to diagnose. A scribe that manufactured confident lessons
from that would be the exact failure the postmortems warn about, wearing the
authority of having read them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Sequence

from ..money import D, ZERO
from .signals import Reading

#: How many *different* dead firms must have lost money on a symbol before the
#: scribe will say anything about it. Two, because one is an anecdote. This is
#: the knob that decides whether this module is evidence or superstition.
MIN_CORROBORATION = int(os.environ.get("TRADE_SCRIBE_MIN_FIRMS", "2") or 2)

#: The most confidence a lesson from the dead may ever carry. Deliberately low:
#: this is survivorship-shaped evidence about strategies that failed, not a
#: forecast, and it sits at one seat of a debate that has several.
MAX_CONFIDENCE = D(35)

PUBLISHER = "scribe"


@dataclass
class Verdict:
    """What the scribe found, and what it refused to say."""

    readings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    lessons_read: int = 0
    symbols_seen: int = 0
    withheld: list = field(default_factory=list)


def _lessons(store) -> list:
    try:
        rows = store.db.query(
            "SELECT firm_id, summary, payload FROM trade_memory "
            "WHERE memory_type = 'bankruptcy' ORDER BY id"
        )
    except Exception:  # noqa: BLE001 - a scribe that cannot read is silent
        return []
    out = []
    for row in rows:
        try:
            payload = json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            payload = {}
        out.append({"firm_id": row.get("firm_id"),
                    "summary": row.get("summary") or "",
                    "payload": payload})
    return out


def read_the_dead(store) -> Verdict:
    """Every bankruptcy, reduced to what more than one of them agrees on."""
    lessons = _lessons(store)
    verdict = Verdict(lessons_read=len(lessons))
    if not lessons:
        verdict.notes.append("no bankruptcies on record: nothing to learn from yet")
        return verdict

    # symbol -> {firm_id: net loss}. Keyed by firm so one firm losing on a name
    # across several trades still counts once — the question is how many
    # *independent* strategies the name hurt, not how often it hurt one.
    by_symbol: dict = {}
    for lesson in lessons:
        firm_id = lesson["firm_id"]
        for entry in lesson["payload"].get("worst_symbols", []) or []:
            try:
                net = D(entry.get("net", 0))
            except Exception:  # noqa: BLE001 - a malformed row is not evidence
                continue
            if net >= 0:
                continue                  # it did not lose money here
            symbol = str(entry.get("symbol") or "").upper()
            if not symbol:
                continue
            by_symbol.setdefault(symbol, {})[firm_id] = net

    verdict.symbols_seen = len(by_symbol)
    for symbol, losers in sorted(by_symbol.items()):
        firms = len(losers)
        if firms < MIN_CORROBORATION:
            verdict.withheld.append(symbol)
            continue
        # Confidence grows with corroboration and stops well short of certainty.
        confidence = min(MAX_CONFIDENCE, D(15) * D(firms))
        verdict.readings.append(Reading(
            symbol=symbol,
            score=-confidence,            # bearish, at the weight of the evidence
            confidence=confidence,
            note=(f"{firms} bankrupt firms lost money on {symbol} "
                  f"(total {sum(losers.values())})"),
        ))

    if verdict.withheld:
        verdict.notes.append(
            f"{len(verdict.withheld)} symbol(s) named by only one bankrupt firm — "
            f"withheld, one firm's bad run is not a pattern: "
            f"{', '.join(verdict.withheld[:6])}"
            + (" and others" if len(verdict.withheld) > 6 else "")
        )
    if not verdict.readings:
        verdict.notes.append(
            f"read {len(lessons)} postmortem(s) across {len(by_symbol)} symbol(s) "
            f"and published nothing: no symbol was named by {MIN_CORROBORATION} "
            "independent firms"
        )
    return verdict


class Scribe:
    """Publishes the dead's lessons once per bar, or explains its silence."""

    name = PUBLISHER

    def __init__(self, store, board):
        self.store = store
        self.board = board

    def run(self, market, as_of=None) -> list:
        as_of = as_of if as_of is not None else market.as_of()
        if as_of is None:
            return []
        if self.board.published(PUBLISHER, as_of):
            return []                     # already spoke for this bar

        verdict = read_the_dead(self.store)
        notes = list(verdict.notes)
        if verdict.readings:
            self.board.publish(PUBLISHER, verdict.readings, as_of)
            notes.insert(0, (
                f"scribe: {len(verdict.readings)} warning(s) from "
                f"{verdict.lessons_read} postmortem(s): "
                + ", ".join(f"{r.symbol} {r.score}" for r in verdict.readings[:5])
            ))
        elif verdict.lessons_read:
            # Record the silence, so this does not re-derive it sixty times
            # an hour. `publish` with an empty list writes no row and so
            # leaves `published()` False — which is exactly what happened.
            self.board.mark_silent(PUBLISHER, as_of)
            notes.insert(0, f"scribe: silent — {notes[-1] if notes else 'no evidence'}")
        return notes


__all__ = ["Scribe", "Verdict", "read_the_dead", "MIN_CORROBORATION", "PUBLISHER"]
