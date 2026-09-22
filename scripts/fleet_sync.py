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
OUT_DIR = REPO / "data" / "fleet"

PROJECT = "e7a232dc-f65e-421c-bd22-0106116ea09a"
ENVIRONMENT = "production"
RAILWAY = Path.home() / ".local" / "bin" / "railway"

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
}

TIMEOUT_S = 120


def fetch(service: str, remote_path: str) -> str:
    """Read one file off a Railway volume. Raises on any failure."""
    result = subprocess.run(
        [str(RAILWAY), "ssh", "-p", PROJECT, "-s", service, "-e", ENVIRONMENT,
         f"cat {remote_path}"],
        capture_output=True, text=True, timeout=TIMEOUT_S,
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
    return out[start:]


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", choices=sorted(SOURCES),
                        help="just this one; repeatable. Default: all of them.")
    parser.add_argument("--list", action="store_true", help="show sources and exit")
    args = parser.parse_args()

    if args.list:
        for name, (service, path) in sorted(SOURCES.items()):
            print(f"  {name:<14} {service:<24} {path}")
        return 0

    wanted = args.source or sorted(SOURCES)
    failures = 0
    for name in wanted:
        try:
            snap = sync_one(name)
        except Exception as exc:  # noqa: BLE001 - one dead source is not a dead sync
            # Loud, and keeps going. A bridge that stops at the first failure
            # syncs nothing the day one bot is redeploying.
            print(f"  {name:<14} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
            continue
        payload = snap["payload"]
        day = payload.get("day") if isinstance(payload, dict) else None
        size = len(payload.get("picks", [])) if isinstance(payload, dict) else 0
        print(f"  {name:<14} ok  day={day}  picks={size}  -> data/fleet/{name}.json")

    if failures:
        print(f"\n{failures} of {len(wanted)} source(s) failed.", file=sys.stderr)
    return 1 if failures == len(wanted) else 0


if __name__ == "__main__":
    raise SystemExit(main())
