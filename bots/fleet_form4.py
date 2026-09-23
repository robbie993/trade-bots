"""FLEET/FORM4 — what the *real* form4_bot flagged, carried into the village.

Source of truth: `/Users/robbie/trade/form4_bot.py`, running on Railway (service
`sincere-appreciation`). Its picks arrive via `scripts/fleet_sync.py`, which
copies `/data/form4_picks.json` into `data/fleet/form4.json`.

A bridge, not a port — same reasoning as `fleet_scanner.py`. The real bot reads
SEC Form 4 filings, which the village has no feed for and no business growing
one. Re-implementing it here would mean re-implementing an SEC parser and then
maintaining two of them. This repeats what the bot already decided.

**One filer is one row, so the same symbol arrives more than once.** Five BBD
executives bought on 2026-09-21 and the file has five BBD rows. A signal board
takes one reading per symbol per bar, so they are combined: the score is the
best of them, and the note says how many filers there were. Summing would let a
company with many small buyers outrank one with a large conviction buy, which is
a different rule than the bot's.

**The evidence on this signal is weak and it is recorded here on purpose.**
Measured 2026-07-31: the Form 4 signal was weak, unstable and median-negative,
and a real options backtest against it was a clean null versus random dates. No
scanner was shipped from it, deliberately. Later, the whale stack built on the
same idea failed all six pre-registered criteria and did not survive a
mega-cap-only restriction. These readings are a record of what the bot flagged.
They are not evidence that flagging it was worth anything, and a firm that
listens to this seat should have its `signal_trust` gene turned down until
something says otherwise.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "fleet" / "form4.json"

#: Form 4 filings are daily and the bot scans once a day. Beyond this the sync
#: has stopped rather than the filers having gone quiet.
MAX_AGE_HOURS = 30.0

#: The bot's score is an additive points total; observed 45..110 on a live file.
#: Dividing by this and clipping maps it to the board's -100..+100. A
#: presentation choice, not the bot's opinion — kept explicit rather than buried.
SCORE_FULL_SCALE = 110.0


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
    return (datetime.now(timezone.utc) - at).total_seconds() / 3600.0 <= MAX_AGE_HOURS


def scan(context):
    try:
        snapshot = json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return {}                        # never synced, or mid-write

    if not _fresh(snapshot):
        return {}

    payload = snapshot.get("payload") or {}
    picks = payload.get("picks") or []
    if not picks:
        return {}

    day = payload.get("day") or ""

    best: dict = {}
    for pick in picks:
        symbol = str(pick.get("sym") or "").upper()
        if not symbol:
            continue
        try:
            score = float(pick.get("score"))
        except (TypeError, ValueError):
            continue
        entry = best.setdefault(symbol, {"score": score, "filers": 0, "value": 0.0,
                                         "role": pick.get("role") or ""})
        entry["filers"] += 1
        entry["score"] = max(entry["score"], score)
        try:
            entry["value"] += float(pick.get("value") or 0)
        except (TypeError, ValueError):
            pass

    readings = []
    for symbol, e in best.items():
        scaled = max(-100.0, min(100.0, e["score"] / SCORE_FULL_SCALE * 100.0))
        filers = e["filers"]
        who = f"{filers} filers" if filers > 1 else (e["role"] or "1 filer")
        readings.append({
            "symbol": symbol,
            "score": scaled,
            # The bot ranks; it does not publish a confidence. This is the
            # strength of its own score and nothing more — inventing anything
            # firmer would be reporting a number it never produced.
            "confidence": min(100.0, abs(scaled)),
            "note": f"form4_bot {day}: {who}, ${e['value']:,.0f} bought",
        })

    return readings
