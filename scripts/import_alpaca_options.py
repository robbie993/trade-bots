#!/usr/bin/env python3
"""Seed `shadow_trades` with the option trades the Alpaca account actually made.

The village has never had an executed option trade to learn from. The account
has 39 of them across 21 contracts, and they are the only evidence in this
whole project of what an option strategy does when it meets a real book.

**They are almost all short.** The winners are written puts — AMD +$1,315,
INTC +$680, HOOD +$520 — which is the same conclusion the 210,509 bar-day
baseline reached from the other direction: buying premium loses about 13% over
five days, so selling it is where the money has been. Importing them is how the
shadow desk starts from that fact rather than rediscovering it.

**Tagged `source='real'`.** Never blended with the desk's hypotheticals. A
score that mixed executed trades with imagined ones would be part evidence and
part invention, and there would be no way afterwards to say which part.

Idempotent: a contract already imported is skipped, so this can be re-run as
the account trades more without duplicating what it already knows.
"""

import json
import ssl
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import Database          # noqa: E402
from src.money import D, money                  # noqa: E402

CREDS = Path("/Users/robbie/trade/.alpaca_credentials")
BASE = "https://paper-api.alpaca.markets"
DESK = "shadow_options"


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def headers():
    raw = dict(l.strip().split("=", 1) for l in CREDS.open()
               if "=" in l and not l.strip().startswith("#"))
    key = raw.get("ALPACA_API_KEY") or raw.get("ALPACA_API_KEY_ID")
    secret = raw.get("ALPACA_SECRET_KEY") or raw.get("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        sys.exit(f"no usable credentials in {CREDS}")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fills(head):
    """Every FILL activity, paged. Options are the long symbols."""
    out, page = [], None
    for _ in range(40):
        url = (f"{BASE}/v2/account/activities/FILL?page_size=100"
               + (f"&page_token={page}" if page else ""))
        request = urllib.request.Request(url, headers=head)
        with urllib.request.urlopen(request, timeout=30, context=_ctx()) as r:
            batch = json.load(r)
        if not batch:
            break
        out.extend(batch)
        page = batch[-1]["id"]
    return [a for a in out if len(a.get("symbol", "")) > 10]


def underlying(occ):
    """The root of an OCC symbol: everything before the 15-character tail."""
    return occ[:-15] if len(occ) > 15 else occ


def main():
    db = Database.from_url("sqlite:///data/mvv.db")
    head = headers()
    activities = fills(head)

    # One row per contract, netting the legs. A written put that was later
    # bought back is one trade with a realised number, not two.
    legs = defaultdict(list)
    for a in activities:
        legs[a["symbol"]].append(a)

    existing = {r["contract"] for r in (db.query(
        "SELECT contract FROM shadow_trades WHERE source = 'real'") or [])}

    added = 0
    for contract, rows in sorted(legs.items()):
        if contract in existing:
            continue
        rows.sort(key=lambda r: r["transaction_time"])
        opened = rows[0]
        # sell-to-open is the short side; the account's option trades are
        # overwhelmingly written premium and the sign has to survive the import.
        short = opened["side"].startswith("sell")
        cash = sum(
            float(r["qty"]) * float(r["price"]) * 100
            * (1 if r["side"].startswith("sell") else -1)
            for r in rows
        )
        closed = len(rows) > 1
        db.insert("shadow_trades", {
            "desk": DESK,
            "source": "real",
            "contract": contract,
            "underlying": underlying(contract),
            "side": "sell" if short else "buy",
            "quantity": abs(float(opened["qty"])),
            "entry_price": str(D(str(opened["price"]))),
            "entry_mid": str(D(str(opened["price"]))),   # no quote on a fill
            "spread_pct": "0",
            "exit_price": str(D(str(rows[-1]["price"]))) if closed else None,
            "realized": str(money(D(str(cash)))) if closed else None,
            "reason": f"real alpaca fill x{len(rows)}",
            "opened_bar": opened["transaction_time"][:16],
            "closed_bar": rows[-1]["transaction_time"][:16] if closed else None,
            "closed_at": rows[-1]["transaction_time"] if closed else None,
        })
        added += 1

    rows = db.query("SELECT side, realized, closed_at FROM shadow_trades "
                    "WHERE source = 'real'") or []
    done = [r for r in rows if r["closed_at"]]
    net = sum(D(str(r["realized"] or 0)) for r in done)
    shorts = sum(1 for r in rows if r["side"] == "sell")
    print(f"imported {added} contract(s); {len(rows)} on record")
    print(f"  written (short): {shorts}   bought (long): {len(rows) - shorts}")
    print(f"  closed: {len(done)}   net realised: ${net:,.2f}")
    db.close()


if __name__ == "__main__":
    main()
