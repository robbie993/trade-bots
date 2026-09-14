"""How much does the evolution rank statistic move between identical runs?

Asked because two runs of the *same* measurement disagreed:

    rederive_evolution.py, 14:30, 36 cohorts : rho +0.0014  (p = 0.472)
    evolution_two_arm.py arm A, 15:48, 36 cohorts : rho +0.1203  (p = 0.024)

Same firms, same generations, same cohort count, same statistic — and one says
"no information" while the other clears a 95th-percentile permutation null.
Both cannot be right about the same world, and the permutation null says
nothing about which is, because it conditions on the cohorts it was handed and
only shuffles inside them. Variation from a sliding data window and fresh
mutation draws is invisible to it.

So: run the identical arm N times, each on its own fresh snapshot, and look at
the spread of the statistic itself. If the run-to-run sd is comparable to the
effect being claimed, then a single run's p-value is not evidence and neither
number above means anything.

Writes nothing outside the scratch directory.
"""
from __future__ import annotations

import os
import random
import sqlite3
import sys
from statistics import fmean, stdev

SCRATCH = os.environ.get("EVO_SCRATCH", "/tmp")
RUNS = int(os.environ.get("EVO_RUNS", "4"))
GENERATIONS = int(os.environ.get("EVO_GENERATIONS", "6"))
MIN_ALLOCATION = float(os.environ.get("EVO_MIN_ALLOCATION", "1"))
LIVE = "data/mvv.db"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fresh_snapshot(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
    src = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
    dst = sqlite3.connect(path)
    src.backup(dst)
    dst.close()
    src.close()


def one_run(index: int) -> dict:
    path = f"{SCRATCH}/repeat_{index}.db"
    fresh_snapshot(path)
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    os.environ["TRADE_PVALUE_LEDGER"] = f"{SCRATCH}/pvalue_offline.json"

    # Imported inside so each run builds its own ecosystem against its own file.
    from src.config import Config
    from src.db.connection import Database
    from src.trading.config import TradingConfig
    from src.trading.ecosystem import Ecosystem
    from src.trading.research import spearman

    app_config = Config(database_url=os.environ["DATABASE_URL"])
    eco = Ecosystem(Database.from_url(app_config.database_url),
                    TradingConfig(), app_config)
    market = eco.market()
    firms = [f for f in eco.store.firms()
             if not f.is_killed and float(f.allocation or 0) >= MIN_ALLOCATION]

    cohorts = []
    for firm in firms:
        spec = eco.specs().get(firm.firm_key)
        analysts = spec.analysts if spec else ("technical", "sentiment", "macro")
        record = firm
        for generation in range(1, GENERATIONS + 1):
            try:
                gen = eco.evolver.evolve(record, market, generation, analysts)
            except Exception:                      # noqa: BLE001
                break
            scored = [(float(c.fitness), float(c.holdout_fitness))
                      for c in gen.candidates if c.holdout_fitness is not None]
            if len(scored) >= 4:
                cohorts.append(scored)
            record = eco.store.require_firm_by_id(record.id)

    def rho_of(rows):
        return spearman([a for a, _ in rows], [b for _, b in rows])[0]

    rhos = [rho_of(c) for c in cohorts]
    mean = fmean(rhos) if rhos else float("nan")
    rng = random.Random(20260914)
    null = []
    for _ in range(500):
        draws = []
        for rows in cohorts:
            held = [b for _, b in rows]
            rng.shuffle(held)
            draws.append(rho_of(list(zip([a for a, _ in rows], held))))
        null.append(fmean(draws))
    above = sum(1 for x in null if x >= mean)
    os.remove(path)
    return {"cohorts": len(cohorts), "mean": mean, "p": above / 500}


def main() -> int:
    print(f"running the identical arm {RUNS} times, fresh snapshot each\n")
    out = []
    for i in range(RUNS):
        r = one_run(i)
        out.append(r)
        print(f"  run {i+1}: {r['cohorts']:>3} cohorts   rho {r['mean']:+.4f}   "
              f"permutation p {r['p']:.3f}")
    means = [r["mean"] for r in out]
    print("\n" + "=" * 68)
    print(f"  mean of the means : {fmean(means):+.4f}")
    if len(means) > 1:
        sd = stdev(means)
        print(f"  run-to-run sd     : {sd:.4f}")
        print(f"  range             : {min(means):+.4f} .. {max(means):+.4f}")
        print(f"  runs calling it significant (p<0.05): "
              f"{sum(1 for r in out if r['p'] < 0.05)}/{len(out)}")
        print()
        print("  The permutation null reports a p-value conditional on one run's")
        print("  cohorts. If the spread above is comparable to the effect, that")
        print("  p-value is not evidence about the world and a single run cannot")
        print("  settle whether the selection rule works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
