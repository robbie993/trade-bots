"""VIDEO/CALLS — explicit calls made on the YouTube channels the village watches.

Source: `scripts/video_watch.py`, run on the operator's PC every 30 minutes. It
reads the captions of new uploads from `config/video_channels.yaml`, keeps only
explicit calls (a ticker with a directional word beside it, via
`crowd.extract_calls`), aggregates the calls from videos younger than the
configured window, and stores the result as the `youtube_calls` snapshot. The
worker unpacks it to `data/fleet/youtube_calls.json`; this repeats it.

It computes nothing, like the fleet bridges: the aggregation already happened,
with the crowd module's rules (one video is one voice, confidence grows with the
margin between agreeing callers, capped at 60).

Silent when the snapshot is missing or older than MAX_AGE_HOURS, which is what
happens if the PC is off: yesterday's television must not vote today.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "fleet" / "youtube_calls.json"

#: The watcher runs every 30 minutes; beyond this it has stopped.
MAX_AGE_HOURS = 2.0


def scan(context):
    try:
        snapshot = json.loads(SNAPSHOT.read_text())
        at = datetime.fromisoformat(str(snapshot["fetched_at"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError):
        return {}
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - at).total_seconds() / 3600.0 > MAX_AGE_HOURS:
        return {}
    return [
        {"symbol": r["symbol"], "score": r["score"], "confidence": r["confidence"],
         "note": r.get("note", "")}
        for r in (snapshot.get("payload") or {}).get("readings") or []
        if r.get("symbol") and r.get("score") is not None
    ]
