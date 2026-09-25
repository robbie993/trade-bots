"""The live fleet, as the village sees it: what each bot said, and what they hold.

Two things arrive here, by two different routes.

**What each bot decided.** `scripts/fleet_sync.py` reads each bot's state file
off its Railway volume and writes it to `fleet_snapshots`. It has to run where
`railway ssh` is logged in (the operator's PC), because the bots live in
another Railway project the worker cannot reach. The worker then calls
`materialize`, which writes the newest snapshot per source into its own
`data/fleet/`, where the fleet scanners have always looked. The scanners do not
change and do not gain a database handle; they still read a file and still go
silent on a stale one.

**What the fleet holds.** Every fleet bot trades one shared Alpaca paper
account, and the village's feed credentials are that account's. So once per
bar the worker reads the account and its positions itself — no sync, no
second machine — and stores it as the `alpaca_account` source. That is what
`bots/fleet_book.py` hears, and what Mission Control compares the village with.

Nothing here can place an order. It reads the account; it never writes to it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..db.connection import to_datetime

REPO = Path(__file__).resolve().parents[2]
FLEET_DIR = REPO / "data" / "fleet"

#: Snapshots kept per source. The scanners read only the newest; the rest is
#: enough history to see when a bot went quiet without growing without bound.
KEEP_PER_SOURCE = 48

ACCOUNT_SOURCE = "alpaca_account"
PAPER_API = "https://paper-api.alpaca.markets"


def record(db, source: str, payload, fetched_at: Optional[str] = None,
           service: str = "", remote_path: str = "") -> int:
    """Store one snapshot, and trim that source to its last KEEP_PER_SOURCE."""
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    new_id = db.insert("fleet_snapshots", {
        "source": source,
        "service": service,
        "remote_path": remote_path,
        "fetched_at": fetched_at,
        "payload": json.dumps(payload),
    })
    db.execute(
        "DELETE FROM fleet_snapshots WHERE source = ? AND id <= ?",
        (source, new_id - KEEP_PER_SOURCE),
    )
    return new_id


def latest(db) -> dict:
    """source -> the newest snapshot, in the shape fleet_sync always wrote."""
    try:
        rows = db.query(
            "SELECT s.* FROM fleet_snapshots s JOIN ("
            " SELECT source, MAX(id) AS top FROM fleet_snapshots GROUP BY source"
            ") t ON s.id = t.top"
        )
    except Exception:  # noqa: BLE001 - an unmigrated ledger has no fleet
        return {}
    out = {}
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        out[row["source"]] = {
            "source": row["source"],
            "service": row.get("service") or "",
            "remote_path": row.get("remote_path") or "",
            "fetched_at": row["fetched_at"],
            "payload": payload,
        }
    return out


def materialize(db, out_dir: Path = FLEET_DIR) -> list:
    """Write each source's newest snapshot to `out_dir`, if it is newer.

    Returns the sources written. Never raises: a fleet that cannot be read is a
    fleet the scanners will not hear, which is exactly what a missing file
    already means to them.
    """
    written = []
    try:
        snaps = latest(db)
        if not snaps:
            return written
        out_dir.mkdir(parents=True, exist_ok=True)
        for source, snap in snaps.items():
            target = out_dir / f"{source}.json"
            if _fetched(target) >= (to_datetime(snap["fetched_at"]) or _EPOCH):
                continue
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(snap, indent=2))
            tmp.replace(target)
            written.append(source)
    except Exception:  # noqa: BLE001 - never fail a tick over the fleet
        pass
    return written


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _fetched(path: Path) -> datetime:
    try:
        return to_datetime(json.loads(path.read_text()).get("fetched_at")) or _EPOCH
    except (OSError, ValueError, AttributeError):
        return _EPOCH


def _credentials() -> Optional[dict]:
    key = (os.environ.get("ALPACA_API_KEY_ID") or os.environ.get("APCA_API_KEY_ID") or "").strip()
    secret = (os.environ.get("ALPACA_API_SECRET_KEY")
              or os.environ.get("APCA_API_SECRET_KEY") or "").strip()
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def read_account(get_json=None) -> Optional[dict]:
    """The fleet's account and positions, trimmed to what the village uses.

    None when there are no credentials. Raises on an HTTP failure, so the caller
    decides how loud to be.
    """
    headers = _credentials()
    if headers is None:
        return None
    if get_json is None:
        from .data.feeds import _get_json as get_json
    account = get_json(f"{PAPER_API}/v2/account", {}, headers=headers)
    positions = get_json(f"{PAPER_API}/v2/positions", {}, headers=headers)
    return {
        "account": account.get("account_number"),
        "equity": account.get("equity"),
        "last_equity": account.get("last_equity"),
        "cash": account.get("cash"),
        "positions": [
            {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "qty": p.get("qty"),
                "asset_class": p.get("asset_class"),
                "market_value": p.get("market_value"),
                "cost_basis": p.get("cost_basis"),
                "unrealized_pl": p.get("unrealized_pl"),
                "unrealized_plpc": p.get("unrealized_plpc"),
            }
            for p in (positions or [])
        ],
    }


def snapshot_account(db, get_json=None) -> Optional[str]:
    """Record the fleet account once. Returns a one-line note, or None if skipped."""
    payload = read_account(get_json)
    if payload is None:
        return None
    record(db, ACCOUNT_SOURCE, payload, service="alpaca", remote_path="/v2/account")
    return (f"fleet account {payload['account']}: equity ${payload['equity']}, "
            f"{len(payload['positions'])} position(s)")


#: The shared account's opening stake, as FLEET_PNL_2026-09-22.md measured it.
FLEET_BASE = 100_000


def comparison(db, village_capital, village_equity) -> Optional[dict]:
    """The fleet beside the village, on each one's own capital.

    Deliberately two returns side by side and not a race: they did not start on
    the same day, with the same money, or on the same instruments, and a single
    "who is winning" number would hide all three.
    """
    snap = latest(db).get(ACCOUNT_SOURCE)
    if not snap:
        return None
    p = snap["payload"]
    try:
        equity = float(p["equity"])
        last = float(p.get("last_equity") or equity)
        unrealized = sum(float(x.get("unrealized_pl") or 0) for x in p["positions"])
    except (TypeError, ValueError, KeyError):
        return None
    cap = float(village_capital or 0)
    return {
        "fetched_at": snap["fetched_at"],
        "fleet_equity": equity,
        "fleet_return_pct": (equity / FLEET_BASE - 1) * 100,
        "fleet_day_pct": (equity / last - 1) * 100 if last else 0.0,
        "fleet_positions": len(p["positions"]),
        "fleet_unrealized": unrealized,
        "village_capital": cap,
        "village_equity": float(village_equity or 0),
        "village_return_pct": (float(village_equity) / cap - 1) * 100 if cap else 0.0,
    }


__all__ = [
    "ACCOUNT_SOURCE",
    "FLEET_DIR",
    "comparison",
    "latest",
    "materialize",
    "read_account",
    "record",
    "snapshot_account",
]
