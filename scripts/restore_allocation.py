"""Put back capital the village took by mistake.

    python scripts/restore_allocation.py                        # show, change nothing
    python scripts/restore_allocation.py --firm firm_a_etf --to 90000 \
        --by robbie --reason "12 of 13 allocation cuts were the per-tick bug"

**Why this exists rather than a SQL update.** ``allocation`` is one side of the
reconciler's identity — ``cash = allocation + Σ fill.cash_delta`` — so moving it
without moving cash breaks every figure downstream of it, silently, and the
next tick refuses to run. This routes through ``apply_approved_increase``,
which moves both together.

**Why it is a script and not a council power.** Handing capital back is an
*increase*, and the village's one asymmetry is that cutting happens by itself
while raising never does. There is no score, no ruling and no verdict that can
reach this. A person types it, and their name goes in the ledger next to it.

**What it will not do.** Guess. Without ``--firm``, ``--to``, ``--by`` and
``--reason`` it prints the current allocations and exits, because the whole
reason the money moved wrongly in the first place was a process that ran
without anybody deciding anything.
"""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config                     # noqa: E402
from src.db import Database                       # noqa: E402
from src.money import D, fmt_money                # noqa: E402
from src.trading.config import TradingConfig      # noqa: E402
from src.trading.ecosystem import Ecosystem       # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--firm", help="firm_key to restore")
    ap.add_argument("--to", help="the allocation it should hold, in dollars")
    ap.add_argument("--by", help="who is authorising this — goes in the ledger")
    ap.add_argument("--reason", help="why the original withdrawal was wrong")
    args = ap.parse_args()

    config = Config()
    eco = Ecosystem(Database.from_url(config.database_url), TradingConfig(), config)
    firms = eco.store.firms()

    print("=== allocations now")
    for f in sorted(firms, key=lambda f: f.firm_key):
        moved = D(f.initial_allocation) - D(f.allocation)
        note = f"   ({fmt_money(moved)} taken back)" if moved > 0 else ""
        print(f"  {f.firm_key:20} {f.status:8} "
              f"handed {fmt_money(f.initial_allocation):>13}  "
              f"holds {fmt_money(f.allocation):>13}{note}")

    if not all([args.firm, args.to, args.by, args.reason]):
        print("\nNothing changed. All four of --firm --to --by --reason are "
              "required to move capital.")
        return 0

    firm = eco.store.get_firm(args.firm)
    if firm is None:
        print(f"\nNo firm called {args.firm!r}.")
        return 1

    target, current = Decimal(args.to), D(firm.allocation)
    if target <= current:
        print(f"\n{args.firm} already holds {fmt_money(current)}, which is not "
              f"less than {fmt_money(target)}. This script only puts capital "
              "back; taking it away is the allocator's job.")
        return 1

    # The cap is the whole village's, not this firm's, and it is the one limit
    # a human authorisation does not override.
    deployed = sum((D(f.allocation) for f in firms if not f.is_killed), D(0))
    headroom = D(eco.config.brokerage.total_capital) - deployed
    if target - current > headroom:
        print(f"\nRefusing: that needs {fmt_money(target - current)} and the "
              f"village has {fmt_money(headroom)} of undeployed capital left.")
        return 1

    change = eco.brokerage.allocator.apply_approved_increase(
        args.firm, target, reason=args.reason, by=args.by)
    print(f"\n{change}")
    print(f"  cash moved with it: {fmt_money(change.delta)}")

    after = eco.store.get_firm(args.firm)
    print(f"  {args.firm}: allocation {fmt_money(after.allocation)}  "
          f"cash {fmt_money(after.cash)}")
    print("\nRun `python -m src.cli trade reconcile` to confirm the books "
          "still add up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
