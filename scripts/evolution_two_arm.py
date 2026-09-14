"""Is the selection rule worthless, or is the space it searches mostly inert?

**Decision rule fixed before running, and this file is committed before the
run.** `PREREG_kronos.md` runs two arms because "a null here is ambiguous — it
may mean the model is useless, or that our few-position shape cannot express a
cross-sectional ranking edge". The village's evolution null has exactly that
shape and cannot currently be read:

  - `scripts/rederive_evolution.py` found mean within-cohort rho = +0.017
    (p=0.356): the in-sample ranking does not predict the held-out ranking.
  - `scripts/gene_ablation.py` found that 68 of 86 gene-firm pairs move fitness
    by **exactly zero**. Only `fast_window`, `slow_window`, `rsi_window` and
    `trend_bias` do anything.

So "evolution does not work" may mean the search is worthless, or it may mean
17 of 21 dimensions are flat and the signal is diluted to nothing. Those are
different findings with different remedies, and one arm cannot separate them.

**Arm A** — the full 21-gene vocabulary. The existing result, re-run here so
both arms come from one script on one snapshot.

**Arm B** — mutation restricted to the four genes that measurably move fitness.
Same firms, same windows, same purge gap, same cohort structure, same null.

**Fixed now:**

  - The statistic is the mean **within-cohort** Spearman, one cohort being one
    firm's one generation. Pooling across cohorts is a Simpson artefact that
    reads between-firm capital differences as skill; it reported +0.57 where
    the within-cohort figure was +0.02.
  - The null is a 500-draw permutation **inside each cohort**, which holds
    "which firm" fixed and destroys only the thing under test.
  - An arm "carries information" only if its mean rho is positive **and** sits
    beyond the 95th percentile of its own null. Nothing else counts.

**How to read the outcome, decided in advance:**

  - Both arms null  -> the search rule itself does not work. Dilution is not the
                       explanation and adding live genes will not rescue it.
  - Only B carries  -> the vocabulary was the problem. Wiring the four unread
                       genes becomes worth doing.
  - Only A carries  -> would be surprising and most likely a bug; investigate
                       before believing.

Runs against a snapshot. Writes nothing to the live village.
"""
from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from statistics import fmean, stdev

SCRATCH = os.environ.get("EVO_SCRATCH", "/tmp")
os.environ["DATABASE_URL"] = f"sqlite:///{SCRATCH}/evo.db"
os.environ["TRADE_PVALUE_LEDGER"] = f"{SCRATCH}/pvalue_ledger_offline.json"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config                       # noqa: E402
from src.db.connection import Database              # noqa: E402
from src.trading import brain                       # noqa: E402
from src.trading.brain import evolver as evolver_mod  # noqa: E402
from src.trading.config import TradingConfig        # noqa: E402
from src.trading.ecosystem import Ecosystem         # noqa: E402
from src.trading.research import spearman           # noqa: E402

GENERATIONS = int(os.environ.get("EVO_GENERATIONS", "6"))
MIN_ALLOCATION = float(os.environ.get("EVO_MIN_ALLOCATION", "1"))
DRAWS = 500

#: The four the ablation found move fitness at all.
LIVE_GENES = ("fast_window", "slow_window", "rsi_window", "trend_bias")


def cohorts_for(eco, firms, generations) -> list:
    out = []
    market = eco.market()
    for firm in firms:
        spec = eco.specs().get(firm.firm_key)
        analysts = spec.analysts if spec else ("technical", "sentiment", "macro")
        record = firm
        for generation in range(1, generations + 1):
            try:
                gen = eco.evolver.evolve(record, market, generation, analysts)
            except Exception as exc:                      # noqa: BLE001
                print(f"    {firm.firm_key} gen {generation}: {exc}")
                break
            scored = [(float(c.fitness), float(c.holdout_fitness))
                      for c in gen.candidates if c.holdout_fitness is not None]
            if len(scored) >= 4:
                out.append(scored)
            record = eco.store.require_firm_by_id(record.id)
    return out


def assess(label: str, cohorts: list) -> dict:
    def rho_of(rows):
        return spearman([a for a, _ in rows], [b for _, b in rows])[0]

    rhos = [rho_of(c) for c in cohorts]
    mean = fmean(rhos)
    rng = random.Random(20260914)
    null = []
    for _ in range(DRAWS):
        draws = []
        for rows in cohorts:
            held = [b for _, b in rows]
            rng.shuffle(held)
            draws.append(rho_of(list(zip([a for a, _ in rows], held))))
        null.append(fmean(draws))
    null.sort()
    p95 = null[int(0.95 * len(null))]
    above = sum(1 for x in null if x >= mean)
    sd = stdev(rhos) if len(rhos) > 1 else 0.0
    se = sd / (len(rhos) ** 0.5) if rhos else 0.0
    carries = mean > 0 and (above / DRAWS) < 0.05
    print(f"\n  {label}")
    print(f"    cohorts {len(cohorts)}   candidates {sum(len(c) for c in cohorts)}")
    print(f"    mean within-cohort rho : {mean:+.4f}   "
          f"t={mean/se if se else float('nan'):+.3f}")
    print(f"    cohorts with rho > 0   : {sum(1 for r in rhos if r > 0)}/{len(rhos)}")
    print(f"    null 95th pct          : {p95:+.4f}")
    print(f"    permutations >= observed: {above}/{DRAWS}  (p = {above/DRAWS:.3f})")
    print(f"    CARRIES INFORMATION    : {'YES' if carries else 'no'}")
    return {"mean": mean, "p": above / DRAWS, "carries": carries,
            "cohorts": len(cohorts)}


def main() -> int:
    app_config = Config(database_url=os.environ["DATABASE_URL"])
    eco = Ecosystem(Database.from_url(app_config.database_url),
                    TradingConfig(), app_config)
    firms = [f for f in eco.store.firms()
             if not f.is_killed and float(f.allocation or 0) >= MIN_ALLOCATION]
    full = dict(evolver_mod.GENES)
    print("=" * 74)
    print(f"TWO ARMS — {len(firms)} funded firms, {GENERATIONS} generations each")
    print("=" * 74)

    print("\nArm A: full vocabulary "
          f"({len(full)} genes)")
    arm_a = assess("ARM A — all genes", cohorts_for(eco, firms, GENERATIONS))

    # Arm B: same everything, mutation restricted to the four live genes.
    restricted = {k: v for k, v in full.items() if k in LIVE_GENES}
    evolver_mod.GENES = restricted
    try:
        print(f"\nArm B: live genes only ({', '.join(LIVE_GENES)})")
        # fresh firms: arm A mutated them in the snapshot
        eco2 = Ecosystem(Database.from_url(app_config.database_url),
                         TradingConfig(), app_config)
        firms2 = [f for f in eco2.store.firms()
                  if not f.is_killed and float(f.allocation or 0) >= MIN_ALLOCATION]
        arm_b = assess("ARM B — four live genes", cohorts_for(eco2, firms2, GENERATIONS))
    finally:
        evolver_mod.GENES = full

    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    if not arm_a["carries"] and not arm_b["carries"]:
        print("  Both arms null. The selection rule does not work, and dilution")
        print("  by inert genes is NOT the explanation — restricting the search")
        print("  to the only four genes that move fitness does not rescue it.")
        print("  Wiring up the unread genes would not help.")
    elif arm_b["carries"] and not arm_a["carries"]:
        print("  Arm B carries information where arm A does not. The vocabulary")
        print("  was the problem: the signal was real and diluted across 17")
        print("  inert dimensions. Wiring the unread genes becomes worth doing.")
    elif arm_a["carries"] and not arm_b["carries"]:
        print("  Arm A carries and arm B does not, which is the surprising")
        print("  ordering. Suspect a bug before believing it.")
    else:
        print("  Both arms carry information.")
    print(f"\n  A: rho {arm_a['mean']:+.4f} p={arm_a['p']:.3f}   "
          f"B: rho {arm_b['mean']:+.4f} p={arm_b['p']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
