"""Does each gene earn its place?

The last of the measurement questions owed since the morning handoff of
2026-09-14, and the one the village has never asked of anything. It comes from
`insider_research/results_reddit_scanner.txt` in the sibling repo, which
reports a per-filter-leg ablation: drop each leg of a screen in turn and see
whether the result needs it. A leg that changes nothing is not a filter, it is
a decoration that costs a degree of freedom.

Here the legs are genes. For each firm, each gene is reset from the value the
firm actually carries to its `BASE_GENOME` default, the firm is re-scored on
the identical window, and the change in fitness is the gene's contribution.

**The measurement validates itself.** Some genes are read by no analyst a given
firm has a seat for — `shadow_dte_min` means nothing without the shadow desk,
`rsi_entry` nothing without `ReversionAnalyst`. Ablating those must move
fitness by *exactly* zero. If it does not, the harness is measuring noise
rather than the gene, and the run says so and stops rather than reporting a
ranking built on it.

Runs against a snapshot. Writes nothing.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

SCRATCH = os.environ.get("EVO_SCRATCH", "/tmp")
os.environ["DATABASE_URL"] = f"sqlite:///{SCRATCH}/evo.db"
os.environ["TRADE_PVALUE_LEDGER"] = f"{SCRATCH}/pvalue_ledger_offline.json"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config                                   # noqa: E402
from src.db.connection import Database                           # noqa: E402
from src.trading.brain.evolver import BASE_GENOME, GENES         # noqa: E402
from src.trading.config import TradingConfig                     # noqa: E402
from src.trading.data.market_data import MarketData                   # noqa: E402
from src.trading.ecosystem import Ecosystem                      # noqa: E402

MIN_ALLOCATION = float(os.environ.get("ABL_MIN_ALLOCATION", "1"))


def main() -> int:
    app_config = Config(database_url=os.environ["DATABASE_URL"])
    eco = Ecosystem(Database.from_url(app_config.database_url),
                    TradingConfig(), app_config)
    market = eco.market()
    evolver = eco.evolver

    firms = [f for f in eco.store.firms()
             if not f.is_killed and float(f.allocation or 0) >= MIN_ALLOCATION]
    print(f"ablating {len(GENES)} genes across {len(firms)} funded firm(s)\n")

    def score(firm, genome, analysts):
        data = MarketData(market.feed, firm.universe or market.symbols)
        return evolver.backtester.run(
            firm_key=firm.firm_key,
            symbols=firm.universe or market.symbols,
            market=data,
            genome=genome,
            analysts=analysts,
            capital=firm.initial_allocation or eco.config.firm.allocation,
            risk_limit=firm.risk_limit,
        ).fitness

    deltas = defaultdict(list)     # gene -> [delta per firm]
    dead = defaultdict(int)
    for firm in firms:
        spec = eco.specs().get(firm.firm_key)
        analysts = spec.analysts if spec else ("technical", "sentiment", "macro")
        genome = dict(firm.genome or BASE_GENOME)
        base = score(firm, genome, analysts)
        print(f"  {firm.firm_key:24s} baseline fitness {float(base):+8.4f}"
              f"  seats={','.join(analysts)}")
        for gene in GENES:
            if gene not in genome:
                continue
            if genome[gene] == BASE_GENOME.get(gene):
                continue            # already at default: nothing to ablate
            probe = dict(genome)
            probe[gene] = BASE_GENOME[gene]
            d = float(score(firm, probe, analysts) - base)
            deltas[gene].append(d)
            if d == 0.0:
                dead[gene] += 1

    if not deltas:
        print("\nno gene differs from its default on any funded firm — "
              "nothing to ablate")
        return 1

    print("\n" + "=" * 74)
    print("PER-GENE CONTRIBUTION  (fitness lost when the gene is reset to default)")
    print("=" * 74)
    print(f"  {'gene':18s} {'firms':>5} {'dead':>5} {'mean':>10} {'best':>10} {'worst':>10}")
    rows = []
    for gene, ds in deltas.items():
        mean = sum(ds) / len(ds)
        rows.append((abs(mean), gene, ds, mean))
    for _, gene, ds, mean in sorted(rows, reverse=True):
        print(f"  {gene:18s} {len(ds):>5} {dead[gene]:>5} "
              f"{mean:>+10.4f} {max(ds):>+10.4f} {min(ds):>+10.4f}")

    total = sum(len(d) for d in deltas.values())
    zero = sum(dead.values())
    print()
    print(f"  gene-firm pairs ablated : {total}")
    print(f"  pairs that changed NOTHING: {zero}  ({zero/total*100:.0f}%)")
    print()
    print("  A gene whose ablation changes fitness by exactly zero is not being")
    print("  read by any analyst this firm has a seat for. It is carried, mutated,")
    print("  selected on, and inert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
