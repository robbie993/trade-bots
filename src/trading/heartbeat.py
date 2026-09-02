"""Who is driving the village, so that two things never drive it at once.

**This file exists because of 2026-09-01.** A `trade serve` process started the
previous night was still up eighteen hours later, holding the ledger open for
writing, and a browser tab left on "Let it run" was POSTing a full village tick
every 2.5 seconds. The background loop was ticking every 60 seconds at the same
time. Two independent processes ran the village against one SQLite file for
most of a day, and drove **35,637 ticks** through the web console alone.

The damage was not subtle once you knew to look:

* the web process was running the code it had been started with, so every fix
  deployed to the loop that day was half-undone on the next web tick — a Sharpe
  sample gate that worked perfectly in the loop while the web kept writing the
  artifact it was written to stop;
* `firm_d_value`, the best desk in the village at +$1,224, was struck three
  times and wound up on that artifact **two hours after** its strikes had been
  cleared;
* the gulag counted down twice per bar, because two processes served the same
  sentence;
* and it is the best explanation for two torn ledgers that had gone unexplained,
  since a torn write needs a second writer.

The auto-tick already guarded against overlapping itself, with a comment saying
that two processes writing the ledger would defeat the point of the system. It
was right. It just had no way to see the other process.

A heartbeat is a fact rather than an inference: the loop stamps a file every
tick, and anything else that wants to run a tick reads it first. Deliberately
not a pidfile — pidfiles are written by launchers, and this village gets started
by hand, by `village.sh`, and by whatever a debugging session reaches for. The
loop writes this itself, so it is true however it was started.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

#: Where the running loop stamps itself. Beside the pidfiles, which are still
#: written by `village.sh` and still useful for stopping things.
HEARTBEAT = Path(os.environ.get(
    "TRADE_HEARTBEAT", Path(__file__).resolve().parents[2] / "run" / "loop.beat"))

#: How long a heartbeat stays believable. Three tick intervals at the default
#: sixty seconds: long enough that one slow tick is not read as a dead loop,
#: short enough that a crashed loop stops blocking the console within minutes.
STALE_AFTER_S = float(os.environ.get("TRADE_HEARTBEAT_STALE_S", "180") or 180)


def beat(path: Optional[Path] = None) -> None:
    """Record that this process just ran a tick. Never raises.

    A village that cannot write its heartbeat still has to trade — this is a
    coordination hint, not a precondition — so every failure here is swallowed.
    """
    path = path or HEARTBEAT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{os.getpid()}\n"
                        f"{datetime.now(timezone.utc).isoformat()}\n")
    except Exception:  # noqa: BLE001 - never fail a tick over bookkeeping
        pass


def running_elsewhere(path: Optional[Path] = None) -> Optional[dict]:
    """The other process ticking this village, or None.

    Returns `{"pid", "age_s"}` when something *else* is alive and has ticked
    recently. Three things all have to be true, because a false positive here
    stops a human from ticking their own village by hand:

    * the heartbeat is fresh — an old one is a loop that has since stopped;
    * the pid is not us — our own heartbeat is not a conflict;
    * the pid still exists — `signal 0` asks the kernel rather than guessing.
    """
    # Read the module global at call time, not as a default bound at import:
    # a default freezes the path before a test — or an operator's
    # TRADE_HEARTBEAT — can redirect it, and a guard that cannot be pointed
    # somewhere else is a guard that reads the developer's own running loop
    # during their test suite.
    path = path or HEARTBEAT
    try:
        pid_text, stamp_text = path.read_text().split()[:2]
        pid = int(pid_text)
        when = datetime.fromisoformat(stamp_text)
    except Exception:  # noqa: BLE001 - no heartbeat, or an unreadable one
        return None

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - when).total_seconds()
    if age > STALE_AFTER_S or pid == os.getpid():
        return None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return None                     # stamped, then died
    except PermissionError:
        pass                            # alive, just not ours to signal
    return {"pid": pid, "age_s": round(age, 1)}


__all__ = ["HEARTBEAT", "STALE_AFTER_S", "beat", "running_elsewhere"]
