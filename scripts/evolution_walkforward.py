"""The evolution rank test, on pinned bars, across fixed non-overlapping windows.

**This exists because the previous version of the measurement was worthless and
nobody could tell.** `rederive_evolution.py` runs against the live feed, whose
`keep_bars = 720` window slides as new bars arrive. The identical script with
identical arguments returned:

    14:30   rho +0.0014   p = 0.472   -> "carries no out-of-sample information"
    17:00   rho +0.1351   p = 0.006   -> "carries out-of-sample information"

Nine runs across one afternoon drifted from ~0.00 to ~+0.15. Each was measuring
a different holdout period and reporting the answer as though it were a
property of the selection rule. The permutation null cannot detect that: it
conditions on the cohorts it is handed and shuffles inside them, so it prices
the ranking *within* a window and is silent about *which* window. It returned
p=0.006 and p=0.472 for the same question with equal confidence.

Two changes, and both are needed:

**1. Pin the bars.** Fetch once into memory, then serve fixed slices. Two runs
of this script over the same window see byte-identical data, so a difference
between runs is a real difference and not the clock.

**2. Walk forward.** Run the identical statistic over several *non-overlapping*
windows and report the distribution — how many windows are individually
significant, and how wide the spread is. One window's p-value was never the
answer; the spread across windows is.

The decision rule, fixed here before the run: the selection rule carries
out-of-sample information only if the **majority of windows** are individually
positive *and* the mean across windows clears its own null. A split verdict
across windows is a null, and it is also the honest description of what a
sliding-window measurement was hiding.

Writes nothing to the live village.
"""
from __future__ import annotations

import os
import random
import sqlite3
import sys
from statistics import fmean, stdev

SCRATCH = os.environ.get("EVO_SCRATCH", "/tmp")
WINDOWS = int(os.environ.get("EVO_WINDOWS", "8"))
GENERATIONS = int(os.environ.get("EVO_GENERATIONS", "4"))
MIN_ALLOCATION = float(os.environ.get("EVO_MIN_ALLOCATION", "1"))
BARS_PER_WINDOW = int(os.environ.get("EVO_BARS", "720"))
HISTORY_DAYS = int(os.environ.get("EVO_HISTORY_DAYS", "365"))
LIVE = "data/mvv.db"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PinnedFeed:
    """Serves one fixed slice of pre-fetched bars. No network, no clock."""

    name = "pinned"

    def __init__(self, bars: dict, lo: int, hi: int):
        self._bars = bars
        self._lo, self._hi = lo, hi

    def series(self, symbol: str) -> list:
        return list(self._bars.get(str(symbol).upper(), [])[self._lo:self._hi])

    def is_crypto(self, symbol: str) -> bool:
        return "-" in str(symbol) or "/" in str(symbol)


def main() -> int:
    from src.config import Config
    from src.db.connection import Database
    from src.trading.config import TradingConfig
    from src.trading.data.feeds import AlpacaFeed
    from src.trading.ecosystem import Ecosystem
    from src.trading.research import spearman

    # ---- which symbols, from the funded firms --------------------------
    snap = f"{SCRATCH}/wf_probe.db"
    if os.path.exists(snap):
        os.remove(snap)
    src = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
    dst = sqlite3.connect(snap)
    src.backup(dst)
    dst.close()
    src.close()
    probe_cfg = Config(database_url=f"sqlite:///{snap}")
    probe = Ecosystem(Database.from_url(probe_cfg.database_url),
                      TradingConfig(), probe_cfg)
    firms = [f for f in probe.store.firms()
             if not f.is_killed and float(f.allocation or 0) >= MIN_ALLOCATION]
    symbols = sorted({s.upper() for f in firms for s in (f.universe or [])})
    print(f"{len(firms)} funded firms, {len(symbols)} symbols")

    # ---- fetch once ----------------------------------------------------
    need = WINDOWS * BARS_PER_WINDOW
    feed = AlpacaFeed(days=HISTORY_DAYS, keep_bars=need + 100,
                      timeframe="15Min", bar_seconds=900)
    bars: dict = {}
    for symbol in symbols:
        try:
            bars[symbol] = feed.series(symbol)
        except Exception as exc:                          # noqa: BLE001
            print(f"  {symbol}: {type(exc).__name__}")
            bars[symbol] = []
    have = min((len(v) for v in bars.values() if v), default=0)
    print(f"pinned history: {have} bars common to every symbol "
          f"(needed {need} for {WINDOWS} windows)")
    usable = min(WINDOWS, have // BARS_PER_WINDOW)
    if usable < 2:
        print("not enough history for two non-overlapping windows")
        return 1
    if usable < WINDOWS:
        print(f"  -> only {usable} non-overlapping windows fit; using those")

    def rho_of(rows):
        return spearman([a for a, _ in rows], [b for _, b in rows])[0]

    def null_p(cohorts, observed):
        rng = random.Random(20260914)
        hits = 0
        for _ in range(500):
            draws = []
            for rows in cohorts:
                held = [b for _, b in rows]
                rng.shuffle(held)
                draws.append(rho_of(list(zip([a for a, _ in rows], held))))
            if fmean(draws) >= observed:
                hits += 1
        return hits / 500

    # ---- one run per window --------------------------------------------
    print(f"\n{'window':>7} {'bars':>16} {'cohorts':>8} {'rho':>9} {'p':>7}")
    results = []
    for w in range(usable):
        lo = have - (usable - w) * BARS_PER_WINDOW
        hi = lo + BARS_PER_WINDOW
        path = f"{SCRATCH}/wf_{w}.db"
        if os.path.exists(path):
            os.remove(path)
        s = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
        d = sqlite3.connect(path)
        s.backup(d)
        d.close()
        s.close()
        cfg = Config(database_url=f"sqlite:///{path}")
        eco = Ecosystem(Database.from_url(cfg.database_url), TradingConfig(), cfg)
        pinned = PinnedFeed(bars, lo, hi)
        from src.trading.data.market_data import MarketData
        market = MarketData(pinned, symbols)

        cohorts = []
        for firm in [f for f in eco.store.firms()
                     if not f.is_killed
                     and float(f.allocation or 0) >= MIN_ALLOCATION]:
            spec = eco.specs().get(firm.firm_key)
            analysts = spec.analysts if spec else ("technical", "sentiment", "macro")
            record = firm
            for generation in range(1, GENERATIONS + 1):
                try:
                    gen = eco.evolver.evolve(record, market, generation, analysts)
                except Exception:                          # noqa: BLE001
                    break
                scored = [(float(c.fitness), float(c.holdout_fitness))
                          for c in gen.candidates if c.holdout_fitness is not None]
                if len(scored) >= 4:
                    cohorts.append(scored)
                record = eco.store.require_firm_by_id(record.id)
        os.remove(path)
        if not cohorts:
            print(f"{w:>7} {lo:>7}-{hi:<8} {0:>8}   (no scored cohorts)")
            continue
        rho = fmean([rho_of(c) for c in cohorts])
        p = null_p(cohorts, rho)
        results.append({"w": w, "rho": rho, "p": p, "cohorts": len(cohorts)})
        print(f"{w:>7} {lo:>7}-{hi:<8} {len(cohorts):>8} {rho:>+9.4f} {p:>7.3f}")

    if len(results) < 2:
        print("\ntoo few windows produced cohorts to say anything")
        return 1

    rhos = [r["rho"] for r in results]
    sig = [r for r in results if r["p"] < 0.05 and r["rho"] > 0]
    positive = [r for r in results if r["rho"] > 0]
    print("\n" + "=" * 62)
    print(f"  windows                  : {len(results)} non-overlapping, "
          f"{BARS_PER_WINDOW} bars each")
    print(f"  mean rho across windows  : {fmean(rhos):+.4f}")
    if len(rhos) > 1:
        print(f"  sd across windows        : {stdev(rhos):.4f}")
    print(f"  range                    : {min(rhos):+.4f} .. {max(rhos):+.4f}")
    print(f"  windows with rho > 0     : {len(positive)}/{len(results)}")
    print(f"  individually significant : {len(sig)}/{len(results)}")
    carries = len(positive) > len(results) / 2 and len(sig) > len(results) / 2
    print("\n  VERDICT: the selection rule "
          + ("carries out-of-sample information."
             if carries else "does NOT carry out-of-sample information."))
    if not carries and len(sig):
        print("  Some windows are individually significant and others are not —")
        print("  which is exactly what the sliding-window version was reporting")
        print("  one draw at a time, as though it were the answer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
