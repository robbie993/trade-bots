"""Pull what the live fleet actually said, into a local snapshot the village can read.

    python scripts/fleet_sync.py            # fetch every source
    python scripts/fleet_sync.py --list     # show what would be fetched
    python scripts/fleet_sync.py --source scanner

**Why a sync step and not a call from inside the village.** A scanner gets ten
seconds (`adapter.TIMEOUT_SECONDS`) and a `railway ssh` round trip regularly
takes longer than that and sometimes fails outright. A scanner that reaches
across the internet inside a tick turns every network hiccup into a silent
village. So the network call lives out here, on its own schedule, and the
scanner reads a file — which it can do in microseconds and which cannot hang.

**Why a bridge and not a port.** `bots/` already holds re-implementations of
these bots, each honestly declaring what it had to drop to fit: the scanner
port lost its volume-surge term and its most-actives universe, keystone lost
the IBS leg that is half its rule, sentinel runs daily instead of 2h with a
close-to-close ATR proxy. Those are approximations of bots that are already
running correctly on Railway, frozen at the day they were written, and they
drift from the real thing every time the real thing changes. This carries what
the real bot actually decided instead, so the village hears the bot rather than
an impression of it.

**What this is allowed to do.** Read. It copies JSON the fleet has already
written to its own volumes and stores it under `data/fleet/`. It places no
order, writes nothing to Railway, and touches no village database — the
publishing step is a scanner, and a scanner can only ever produce a reading.

Each snapshot is wrapped with `fetched_at` and the source it came from, because
a bridge that loses track of *when* is how a Tuesday alert votes on Thursday.
The scanner that reads these checks the stamp and stays silent when it is old.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Task Scheduler hands this process a cp1252 console, and model answers are full
# of characters it cannot encode ("−", em dashes): the first run died on
# the third answer's print. Replace what cannot be shown rather than crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
OUT_DIR = REPO / "data" / "fleet"

PROJECT = "e7a232dc-f65e-421c-bd22-0106116ea09a"          # where the fleet runs
VILLAGE_PROJECT = "ai-village"                            # where its ledger is
ENVIRONMENT = "production"
RAILWAY = Path.home() / ".local" / "bin" / ("railway.exe" if sys.platform == "win32" else "railway")

#: What to pull, and from which service's volume. The service names are
#: Railway-generated and say nothing about what runs there — see
#: `railway variables -s <service> | grep BOT`. One service runs several bots,
#: so the mapping is per *file*, not per service.
SOURCES = {
    "scanner": ("sincere-appreciation", "/data/scanner_picks.json"),
    "form4": ("sincere-appreciation", "/data/form4_picks.json"),
    "picks_trader": ("sincere-appreciation", "/data/picks_trader_state.json"),
    "btcc": ("supportive-benevolence", "/data/btcc_positions.json"),
    "atlas": ("supportive-benevolence", "/data/atlas_xsec_state.json"),
    "coinbase": ("honest-hope", "/data/cb_positions.json"),
    "supercrypto": ("supercrypto", "/app/data/supercrypto_state.json"),
}

TIMEOUT_S = 120


def fetch(service: str, remote_path: str) -> str:
    """Read one file off a Railway volume. Raises on any failure."""
    result = subprocess.run(
        [str(RAILWAY), "ssh", "-p", PROJECT, "-s", service, "-e", ENVIRONMENT,
         f"cat {remote_path}"],
        capture_output=True, text=True, timeout=TIMEOUT_S,
        # No stdin: a first connection asks whether to trust the host, and a
        # prompt nobody can answer is a sync that hangs until the timeout.
        stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:200] or "railway ssh failed")
    # `railway ssh` prefixes a line about the key it used. The payload is JSON,
    # so take everything from the first brace rather than trying to count lines
    # — the banner has changed shape before and a line count would not notice.
    out = result.stdout
    start = out.find("{")
    if start < 0:
        raise RuntimeError(f"no JSON in the response ({out.strip()[:120]!r})")
    # One JSON document and nothing after it: the CLI has appended notices
    # after the payload before (a deprecation warning), and `json.loads` of the
    # whole tail then fails on text that is not the bot's.
    doc, end = json.JSONDecoder().raw_decode(out[start:])
    return json.dumps(doc)


def railway_database_url() -> str:
    """The shared village Postgres, asked of Railway rather than written down.

    Built from the Postgres service's own variables and its public TCP proxy,
    so no connection string (which is a password) ever sits in a file on this
    machine. Needs the same `railway login` the ssh step already needs.
    """
    result = subprocess.run(
        [str(RAILWAY), "variables", "-p", VILLAGE_PROJECT, "-s", "Postgres",
         "-e", ENVIRONMENT, "--json"],
        capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:200] or "railway variables failed")
    v = json.loads(result.stdout[result.stdout.find("{"):])
    host, port = v.get("RAILWAY_TCP_PROXY_DOMAIN"), v.get("RAILWAY_TCP_PROXY_PORT")
    if not host or not port:
        raise RuntimeError("the village Postgres has no public TCP proxy")
    return (f"postgresql://{v['PGUSER']}:{v['PGPASSWORD']}@{host}:{port}/"
            f"{v['PGDATABASE']}")


def sync_one(name: str) -> dict:
    service, remote_path = SOURCES[name]
    raw = fetch(service, remote_path)
    payload = json.loads(raw)          # fail here rather than write junk
    snapshot = {
        "source": name,
        "service": service,
        "remote_path": remote_path,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"{name}.json"
    # Write beside and rename: a reader that catches this file half-written
    # would see truncated JSON and, if it were lenient, a truncated watchlist.
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, indent=2))
    tmp.replace(target)
    return snapshot


def to_ledger(db, snapshot: dict) -> None:
    """Also put it where the Railway worker can read it. See src/trading/fleet.py."""
    from src.trading import fleet

    fleet.record(db, snapshot["source"], snapshot["payload"],
                 fetched_at=snapshot["fetched_at"], service=snapshot["service"],
                 remote_path=snapshot["remote_path"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", choices=sorted(SOURCES),
                        help="just this one; repeatable. Default: all of them.")
    parser.add_argument("--list", action="store_true", help="show sources and exit")
    parser.add_argument("--to-railway", action="store_true",
                        help="also write each snapshot to the village's Railway Postgres")
    parser.add_argument("--database-url", default="",
                        help="also write to this database instead (overrides --to-railway)")
    args = parser.parse_args()

    if args.list:
        for name, (service, path) in sorted(SOURCES.items()):
            print(f"  {name:<14} {service:<24} {path}")
        return 0

    db = None
    if args.database_url or args.to_railway:
        sys.path.insert(0, str(REPO))
        from src.db.connection import Database

        db = Database.from_url(args.database_url or railway_database_url())
        db.init_schema()

    wanted = args.source or sorted(SOURCES)
    failures = 0
    for name in wanted:
        try:
            snap = sync_one(name)
            if db is not None:
                to_ledger(db, snap)
        except Exception as exc:  # noqa: BLE001 - one dead source is not a dead sync
            # Loud, and keeps going. A bridge that stops at the first failure
            # syncs nothing the day one bot is redeploying.
            print(f"  {name:<14} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
            continue
        payload = snap["payload"]
        day = payload.get("day") if isinstance(payload, dict) else None
        size = len(payload.get("picks", [])) if isinstance(payload, dict) else 0
        where = " + ledger" if db is not None else ""
        print(f"  {name:<14} ok  day={day}  picks={size}  -> data/fleet/{name}.json{where}")

    if failures:
        print(f"\n{failures} of {len(wanted)} source(s) failed.", file=sys.stderr)
    return 1 if failures == len(wanted) else 0


if __name__ == "__main__":
    raise SystemExit(main())
