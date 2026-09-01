#!/usr/bin/env python3
"""What buying an option costs before any signal — the hurdle, measured.

Every option strategy is a bet that a signal beats the structural cost of
holding premium. Nobody in this project has ever measured that cost, so every
option idea has been argued about in the abstract. This measures it, using the
210,509 bar-days of real 2024 option history sitting in the sibling repo's
`whale_stack_optbars.json`.

**What this is not.** It is not the insider-options study. That was run on
2026-07-31 and returned a clean null against random dates; re-running it would
reproduce a known result. This asks the prior question: if you bought a random
option on a random day and held it, what happened?

**What it measures.** Close-to-close returns over several holding periods,
across every contract in the file, excluding bar-days with fewer than five
trades — those are prices nobody could have filled, and including them would
measure the tape rather than a trade.

**The one thing it cannot see.** These bars carry open, high, low, close and
volume, and no bid or ask. So this is the return *before* the spread. The live
feed measures the spread separately — 2.6% round trip on an at-the-money SPY
call, 28.6% median near the money after hours — and the two must be subtracted,
never multiplied or conflated. Conflating them is exactly how the insider study
reported an edge that was already dead.

So read the output as: this is the best case, before costs. A strategy has to
beat this *and* pay the spread.
"""

import json
import statistics
import sys
from pathlib import Path

TRADE_DIR = Path("/Users/robbie/trade")
BARS = TRADE_DIR / "whale_stack_optbars.json"

#: A bar-day with fewer trades than this was not fillable. 31% of the file
#: falls below it, and counting those would measure quotes nobody hit.
MIN_TRADES = 5

HORIZONS = (1, 3, 5, 10)


def load():
    if not BARS.exists():
        sys.exit(f"no option bars at {BARS}")
    return json.load(BARS.open())


def returns(data, horizon):
    """Close-to-close over `horizon` fillable days, per contract."""
    out = []
    for symbol, rows in data.items():
        usable = [r for r in rows if (r.get("n") or 0) >= MIN_TRADES
                  and (r.get("c") or 0) > 0]
        for i in range(len(usable) - horizon):
            entry = usable[i]["c"]
            exit_ = usable[i + horizon]["c"]
            if entry > 0:
                out.append((exit_ - entry) / entry)
    return out


def describe(rets, label):
    if not rets:
        print(f"  {label:<12} no fillable pairs")
        return
    rets_sorted = sorted(rets)
    n = len(rets)
    mean = statistics.mean(rets)
    median = statistics.median(rets)
    wins = sum(1 for r in rets if r > 0) / n
    p10 = rets_sorted[int(0.10 * n)]
    p90 = rets_sorted[int(0.90 * n)]
    print(f"  {label:<12}{n:>9,}{mean:>11.2%}{median:>11.2%}{wins:>10.1%}"
          f"{p10:>11.1%}{p90:>11.1%}")


def main():
    data = load()
    print(f"{len(data):,} contracts, min {MIN_TRADES} trades/day to count\n")
    print(f"  {'hold':<12}{'n':>9}{'mean':>11}{'median':>11}{'win%':>10}"
          f"{'p10':>11}{'p90':>11}")
    for horizon in HORIZONS:
        describe(returns(data, horizon), f"{horizon} day(s)")
    print("\n  Before spread. The live feed measures 2.6% round trip on an ATM")
    print("  SPY call and 28.6% median near the money after hours — subtract,")
    print("  never conflate.")


if __name__ == "__main__":
    main()
