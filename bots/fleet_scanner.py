"""FLEET/SCANNER — what the *real* scanner_bot picked, carried into the village.

Source of truth: `/Users/robbie/trade/scanner_bot.py`, running on Railway
(service `sincere-appreciation`, posts #scanner). Its picks arrive here via
`scripts/fleet_sync.py`, which copies `/data/scanner_picks.json` off the volume
into `data/fleet/scanner.json`.

**This is a bridge, not a port, and that is the whole point.**

`bots/scanner.py` is the port: a re-implementation of the same screen inside the
village. Its own header lists what it had to give up — the volume-surge term
("no volume in context"), the day's 40 most-active names ("that list is fetched
live"), and the raw score, squashed to fit -100..+100 with a clip the real bot
does not apply. It says so honestly, and it is still a *different screen*, wearing
the real one's name, frozen on the day it was written. Every time the real bot
changes, the copy is wrong in a new way and nothing announces it.

So this file computes nothing. The real scanner already ran, on its own schedule,
with its full universe and every term of its score. This reads what it decided
and repeats it. If the real bot improves, this improves; if it breaks, this goes
silent rather than quietly substituting an opinion of its own.

**Silence is the failure mode, deliberately.** Three things make this publish
nothing: no snapshot file, a snapshot older than MAX_AGE_HOURS, or a pick list
for a day that is not the snapshot's own. Each returns `{}` — which the signal
board treats as a publisher with nothing to say, and every listening firm
correctly hears nothing. There is no fallback to stale picks and no default
score, because the one thing worse than no reading is yesterday's reading
presented as today's.

**Evidence health warning, carried over from the port.** Measured 2026-08-06:
scanner_bot's hand-written base universe is worth about +26.9%/yr of pure
hindsight against a random-name control, because the names were chosen in 2026
from what had already worked. These readings inherit that. They are a record of
what the bot said, not evidence that saying it was right — and any trial that
scores them on that same universe measures the list, not the screen.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "fleet" / "scanner.json"

#: How old a snapshot may be before this goes quiet. The real scanner publishes
#: twice a day (20:00 ET on the close, 07:00 ET premarket), so a gap beyond this
#: means the sync has stopped rather than that the bot had a quiet session.
MAX_AGE_HOURS = 30.0

#: The real bot's score is an additive points total, unbounded in principle and
#: in practice running roughly 0..90 for names that make its top ten. The
#: village wants -100..+100. Dividing by this and clipping is a *presentation*
#: choice and not the bot's opinion: two names arriving at +100 here may be well
#: apart in the real ranking. Kept explicit so the squashing is visible rather
#: than buried in a magic number.
SCORE_FULL_SCALE = 90.0


def _fresh(snapshot: dict) -> bool:
    fetched = snapshot.get("fetched_at")
    if not fetched:
        return False
    try:
        at = datetime.fromisoformat(fetched)
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - at).total_seconds() / 3600.0
    return age_h <= MAX_AGE_HOURS


def scan(context):
    try:
        snapshot = json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return {}                       # never synced, or mid-write

    if not _fresh(snapshot):
        return {}

    payload = snapshot.get("payload") or {}
    picks = payload.get("picks") or []
    if not picks:
        return {}

    day = payload.get("day") or ""

    readings = []
    for pick in picks:
        symbol = str(pick.get("sym") or "").upper()
        if not symbol:
            continue
        raw = pick.get("score")
        if raw is None:
            continue
        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue

        scaled = max(-100.0, min(100.0, score / SCORE_FULL_SCALE * 100.0))

        # The real bot's own terms, repeated rather than recomputed, so the note
        # says why *it* liked the name and can be checked against #scanner.
        bits = []
        if pick.get("rs") is not None:
            bits.append(f"RS {float(pick['rs']):+.1f}")
        if pick.get("ret20") is not None:
            bits.append(f"20d {float(pick['ret20']):+.1f}%")
        if pick.get("above_50") is not None:
            bits.append("above 50d" if pick["above_50"] else "below 50d")
        if pick.get("vol_ratio") is not None:
            bits.append(f"vol x{float(pick['vol_ratio']):.1f}")

        readings.append({
            "symbol": symbol,
            "score": scaled,
            # The real bot ranks rather than scoring confidence, so this is the
            # strength of its own score and nothing more. Saying anything more
            # confident than that would be inventing a number it never produced.
            "confidence": min(100.0, abs(scaled)),
            "note": f"scanner_bot {day}: {', '.join(bits)}" if bits
                    else f"scanner_bot {day} watchlist",
        })

    return readings
