"""FLEET/POSITIONS — what each fleet bot is holding, by name, as a vote.

`fleet_book.py` hears the shared Alpaca account, but that account cannot say
which bot holds what (every order is an anonymous UUID), and the crypto bots do
not trade there at all: btcc trades perps, coinbase trades on Coinbase, atlas
and supercrypto run long/short crypto books of their own. Their state files
reach `data/fleet/` through `scripts/fleet_sync.py`; this reads them.

One bot holding a name is one vote in that direction: long +1, short -1. The
reading is the mean of the bots' votes times 40, so every bot long is +40 and a
split fleet is 0; confidence is 20 per bot holding it, capped at 60. Nothing
here scales with a bot's open gain or position size — the same rule as
fleet_book, for the same reason.

A snapshot older than MAX_AGE_HOURS is ignored bot by bot: a bot whose sync
stopped goes silent without silencing the others.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

FLEET = Path(__file__).resolve().parent.parent / "data" / "fleet"
MAX_AGE_HOURS = 3.0
#: A cross-sectional weight smaller than this is a rounding remainder, not a view.
MIN_WEIGHT = 0.001


def _fresh(snap: dict) -> bool:
    try:
        at = datetime.fromisoformat(str(snap["fetched_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).total_seconds() / 3600.0 <= MAX_AGE_HOURS


def _usd(sym: str) -> str:
    s = str(sym).upper()
    return s if "-" in s else f"{s}-USD"


def holdings(bot: str, payload) -> dict:
    """symbol -> +1/-1 for one bot's state file."""
    if not isinstance(payload, dict):
        return {}
    if bot == "btcc":
        return {_usd(k): (-1 if str(v.get("side", "")).upper() == "SHORT" else 1)
                for k, v in payload.items() if isinstance(v, dict)}
    if bot == "coinbase":
        return {_usd(k): 1 for k, v in payload.items() if isinstance(v, dict)}
    if bot in ("atlas", "supercrypto"):
        return {_usd(k): (1 if w > 0 else -1)
                for k, w in (payload.get("weights") or {}).items()
                if isinstance(w, (int, float)) and abs(w) >= MIN_WEIGHT}
    if bot == "picks_trader":
        return {str(k).upper(): 1 for k in (payload.get("positions") or {})}
    return {}


BOTS = ("btcc", "coinbase", "atlas", "supercrypto", "picks_trader")


def scan(context):
    votes: dict = {}
    for bot in BOTS:
        try:
            snap = json.loads((FLEET / f"{bot}.json").read_text())
        except (OSError, ValueError):
            continue
        if not _fresh(snap):
            continue
        for symbol, direction in holdings(bot, snap.get("payload")).items():
            votes.setdefault(symbol, []).append((bot, direction))
    out = []
    for symbol, vs in votes.items():
        mean = sum(d for _, d in vs) / len(vs)
        if mean == 0:
            continue
        out.append({
            "symbol": symbol,
            "score": round(40 * mean, 2),
            "confidence": min(60, 20 * len(vs)),
            "note": ", ".join(f"{b} {'long' if d > 0 else 'short'}" for b, d in vs),
        })
    return out
