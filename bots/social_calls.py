"""SOCIAL/CALLS — explicit calls made in the subreddits the village reads.

Source: `scripts/social_watch.py`, run on the operator's PC every 30 minutes. It
reads the public RSS of the subreddits in `config/social_sources.yaml` in one
combined feed, keeps only explicit calls (via `crowd.extract_calls`), counts one
Reddit author as one voice, and stores the aggregate as the `reddit_calls`
snapshot. The worker unpacks it to `data/fleet/reddit_calls.json`; this repeats it,
merged with the other social watchers' snapshots (Instagram, and X and TikTok
once they run).

It computes nothing, like the fleet bridges: the aggregation already happened,
with the crowd module's rules (one author is one voice, confidence grows with the
margin between agreeing callers, capped at 60).

Silent when the snapshot is missing or older than MAX_AGE_HOURS, which is what
happens if the PC is off: yesterday's posts must not vote today.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

FLEET = Path(__file__).resolve().parent.parent / "data" / "fleet"

#: One snapshot per social source, each written by its own watcher on the PC.
#: A source whose watcher has stopped drops out alone.
SOURCES = ("reddit_calls", "instagram_calls", "x_calls", "tiktok_calls")

#: The watchers run every 30 minutes; beyond this one has stopped.
MAX_AGE_HOURS = 2.0


def _fresh_readings(name: str) -> list:
    try:
        snapshot = json.loads((FLEET / f"{name}.json").read_text())
        at = datetime.fromisoformat(str(snapshot["fetched_at"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError):
        return []
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - at).total_seconds() / 3600.0 > MAX_AGE_HOURS:
        return []
    return [r for r in (snapshot.get("payload") or {}).get("readings") or []
            if r.get("symbol") and r.get("score") is not None]


def scan(context):
    """One reading per symbol across every live social source.

    Where two platforms call the same symbol, the score is their mean weighted
    by confidence and the confidence is their sum, capped at the crowd module's
    60: agreement across platforms is more voices, not a louder one.
    """
    merged: dict = {}
    for name in SOURCES:
        for r in _fresh_readings(name):
            merged.setdefault(r["symbol"], []).append(r)
    out = []
    for symbol, rs in merged.items():
        conf = sum(float(r["confidence"]) for r in rs)
        if conf <= 0:
            continue
        score = sum(float(r["score"]) * float(r["confidence"]) for r in rs) / conf
        out.append({"symbol": symbol, "score": round(score, 2),
                    "confidence": min(60.0, conf),
                    "note": " | ".join(r.get("note", "") for r in rs)[:240]})
    return out
