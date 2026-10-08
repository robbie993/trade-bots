"""Phone notifications for new paper trades in the daily village.

Run after each tick. Reads fills newer than the last one it reported and sends
one push per firm through ntfy (https://ntfy.sh, free app, no account): the
time, each buy or sell, and the dollar amount. The topic name comes from
NTFY_TOPIC or data/ntfy_topic.txt (git-ignored); anyone who knows it can read
the messages, so it is a long random name. Nothing is sent on the very first
run, so old trades are not replayed; it only remembers where it is.

    python scripts/notify_fills.py [--db data/mvv_daily.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "notify_state.json"


def topic() -> str:
    t = os.environ.get("NTFY_TOPIC", "").strip()
    f = ROOT / "data" / "ntfy_topic.txt"
    if not t and f.exists():
        t = f.read_text().strip()
    return t


def local_time(stamp: str) -> str:
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return str(stamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def messages(rows) -> list[tuple[str, str]]:
    by_firm = defaultdict(list)
    for r in rows:
        by_firm[r["name"] or r["firm_key"]].append(r)
    out = []
    for firm, fills in by_firm.items():
        lines = []
        for f in fills:
            amount = abs(float(f["price"]) * float(f["quantity"]))
            verb = "Bought" if f["side"] == "buy" else "Sold"
            pnl = float(f["realized_pnl"] or 0)
            tail = f" ({'+' if pnl >= 0 else '-'}${abs(pnl):,.2f})" if f["side"] != "buy" and pnl else ""
            lines.append(f"{verb} {f['symbol']} ${amount:,.0f}{tail}")
        when = local_time(fills[-1]["created_at"])
        title = f"{firm}: {len(fills)} paper trade{'s' if len(fills) > 1 else ''}"
        out.append((title, f"{when}\n" + "\n".join(lines[:12]) + (f"\n+{len(lines) - 12} more" if len(lines) > 12 else "")))
    return out


def send(t: str, title: str, body: str) -> None:
    req = urllib.request.Request(f"https://ntfy.sh/{t}", data=body.encode(), method="POST",
                                 headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": "chart_with_upwards_trend"})
    urllib.request.urlopen(req, timeout=15).read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "mvv_daily.db"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    top = con.execute("SELECT COALESCE(MAX(id), 0) FROM fills").fetchone()[0]
    if not STATE.exists():
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"last_fill_id": top}))
        print(f"notify: first run, starting after fill {top}")
        return
    last = json.loads(STATE.read_text()).get("last_fill_id", 0)
    rows = con.execute(
        "SELECT f.*, firms.name, firms.firm_key FROM fills f LEFT JOIN firms ON firms.id = f.firm_id "
        "WHERE f.id > ? ORDER BY f.id", (last,)).fetchall()
    t = topic()
    for title, body in messages(rows):
        print(f"notify: {title}\n{body}")
        if t and not a.dry_run:
            send(t, title, body)
    if not t:
        print("notify: no NTFY_TOPIC or data/ntfy_topic.txt, printed only")
    if not a.dry_run:
        STATE.write_text(json.dumps({"last_fill_id": top}))


if __name__ == "__main__":
    main()
