"""FLEET/BOOK — what the live fleet is holding, as a vote.

Source of truth: the shared Alpaca paper account every fleet bot trades
(`PA3VG5AP6TO5`). The worker reads it once per bar (`src/trading/fleet.py`)
and unpacks it to `data/fleet/alpaca_account.json`; this reads that file.

A long position is the fleet saying "up" about a name, and a short one "down".
That is all this repeats. It does **not** score a position by how well it is
doing: an open gain is a price, not a result (FLEET_PNL_2026-09-22.md found
nearly the whole $10.4k gain unrealised, and three quarters of it resting on a
crypto cost basis that is not a price), and a vote that grew with the open gain
would be the fleet's luck arriving here as conviction.

**Which bot holds it is unknown**, because every order on the account carries an
anonymous UUID. So the note says "the fleet", never a bot's name.

Options are skipped. An OCC contract symbol is not a village symbol, and turning
a call into a vote on the underlying would be guessing at the bot's intent —
a covered call is a mildly bearish view on a stock the wheel also holds long.

Silent when the snapshot is missing or older than MAX_AGE_HOURS.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "fleet" / "alpaca_account.json"

#: The worker reads the account every bar; beyond this the reading has stopped.
MAX_AGE_HOURS = 6.0

#: A held position is a direction and nothing more, so every vote is the same
#: modest size. It moves one seat of a debate; it never decides one.
SCORE = 40.0
CONFIDENCE = 30.0


def _fresh(snapshot: dict) -> bool:
    try:
        at = datetime.fromisoformat(str(snapshot.get("fetched_at")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).total_seconds() / 3600.0 <= MAX_AGE_HOURS


def village_symbol(symbol: str, asset_class: str) -> str:
    """Alpaca spells crypto BTCUSD (or BTC/USD); the village spells it BTC-USD."""
    s = str(symbol or "").upper().replace("/", "")
    if asset_class == "crypto" and s.endswith("USD") and len(s) > 3:
        return f"{s[:-3]}-USD"
    return s


def scan(context):
    try:
        snapshot = json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return {}
    if not _fresh(snapshot):
        return {}

    readings = []
    for p in (snapshot.get("payload") or {}).get("positions") or []:
        asset_class = str(p.get("asset_class") or "")
        if asset_class == "us_option":
            continue
        symbol = village_symbol(p.get("symbol"), asset_class)
        if not symbol:
            continue
        short = str(p.get("side") or "").lower() == "short"
        try:
            plpc = float(p.get("unrealized_plpc") or 0) * 100
            note = f"the fleet holds it {'short' if short else 'long'} ({plpc:+.1f}% open)"
        except (TypeError, ValueError):
            note = f"the fleet holds it {'short' if short else 'long'}"
        readings.append({
            "symbol": symbol,
            "score": -SCORE if short else SCORE,
            "confidence": CONFIDENCE,
            "note": note,
        })
    return readings
