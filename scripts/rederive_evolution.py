"""Re-derive the evolution verdict on a clean apparatus.

Evening handoff §7.1. "Evolution does not work (rho=+0.056, n=280)" was
measured through a holdout that overlapped its fitted window by 42% *and* a
fitness function with no benchmark. Both are fixed. The null very likely
survives — leakage inflates correlation, so the old number was if anything
overstated — but it has not been re-measured, and an unre-measured number is
exactly what this project does not accept.

Runs against a *snapshot* of the live database, never the live one: the
evolver writes genomes, and a deliberate offline run must not inject
generations into the running village or contaminate epoch 2. Take the
snapshot with the sqlite backup API, which is consistent against a live
writer — `cp` on a WAL database is not, and that has already cost this
project one bad backup:

    python -c "import sqlite3; s=sqlite3.connect('file:data/mvv.db?mode=ro',\\
      uri=True); d=sqlite3.connect('\$EVO_SCRATCH/evo.db'); s.backup(d)"

Reports, in order:
  1. that the split is actually clean (overlap 0, purge gap applied) — and
     refuses to report a correlation at all if it is not
  2. the mean **within-cohort** Spearman, one cohort being one firm's one
     generation: the eight candidates the selection rule actually chooses
     between
  3. the pooled figure alongside it, labelled as the artefact it is
  4. a permutation null that shuffles *inside* each cohort, so it destroys
     only the thing under test and holds "which firm" fixed

**Why the unit matters, since it is the whole result.** The first run of this
script pooled all candidates and reported rho = +0.5688, p < 1e-4, n = 64 —
while every per-firm rho was near zero or negative, one of them -1.0. That is
Simpson's paradox: candidates from a well-capitalised firm score high on both
windows and candidates from a poor one score low on both, so the pooled
statistic reads back "which firm this genome belongs to" — known before any
evolution happened — as predictive power. The selection rule never chooses
between firms. It chooses the best of eight mutants inside one generation.

The evolver's own per-generation readout (`_record_rank_test`) was always
within-cohort and is unaffected. The pooled retrospective — the n=280 that
produced rho=+0.056 — was not.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

SCRATCH = os.environ.get("EVO_SCRATCH", "/tmp")
os.environ["DATABASE_URL"] = f"sqlite:///{SCRATCH}/evo.db"
# Never let an offline run reach the real p-value ledger.
os.environ["TRADE_PVALUE_LEDGER"] = f"{SCRATCH}/pvalue_ledger_offline.json"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config                      # noqa: E402
from src.db.connection import Database             # noqa: E402
from src.trading.config import TradingConfig       # noqa: E402
from src.trading.ecosystem import Ecosystem        # noqa: E402
from src.trading.research import spearman          # noqa: E402

GENERATIONS = int(os.environ.get("EVO_GENERATIONS", "4"))

# Restrict to firms carrying real capital. The village's books hold 48 firms
# and 28 of them have never placed a fill — unfunded heirs left behind by the
# `file_successor` cascade of 2026-09-01 (fixed; see
# `tests/test_trading_heir_cascade.py`). An unfunded firm still backtests,
# because `evolve` falls back to the configured capital, so it is a legitimate
# test-bed for a genome — but the result should not depend on including them,
# and this is how that gets checked rather than assumed.
MIN_ALLOCATION = float(os.environ.get("EVO_MIN_ALLOCATION", "0"))


def main() -> int:
    app_config = Config(database_url=os.environ["DATABASE_URL"])
    db = Database.from_url(app_config.database_url)
    eco = Ecosystem(db, TradingConfig(), app_config)

    market = eco.market()
    evolver = eco.evolver

    # ---- 1. is the split actually clean? --------------------------------
    firms = [f for f in eco.store.firms() if not f.is_killed]
    if MIN_ALLOCATION > 0:
        firms = [f for f in firms if float(f.allocation or 0) >= MIN_ALLOCATION]
        print(f"(restricted to {len(firms)} firm(s) with allocation >= "
              f"{MIN_ALLOCATION:,.0f})")
    probe = firms[0]
    symbols = probe.universe or market.symbols
    fitted_bars, holdout_start, holdout_bars = evolver._split(market, symbols)
    gap = evolver.purge_bars()
    warmup = int(getattr(evolver.backtester, "warmup", 0) or 0)
    fitted_end = warmup + fitted_bars          # last bar the fit actually reads
    print("=" * 72)
    print("SPLIT")
    print(f"  purge gap          : {gap} bars (max lookback in the vocabulary)")
    print(f"  backtester warmup  : {warmup}")
    print(f"  fitted window      : [{warmup}, {fitted_end})   ({fitted_bars} bars)")
    print(f"  holdout            : [{holdout_start}, {holdout_start + holdout_bars})"
          f"   ({holdout_bars} bars)")
    overlap = max(0, fitted_end - holdout_start)
    print(f"  OVERLAP            : {overlap} bars"
          f"   <- the 42% leak was {'gone' if overlap == 0 else 'STILL HERE'}")
    print(f"  gap actually left  : {holdout_start - fitted_end} bars")
    if overlap:
        print("  refusing to report a rho across a leaking boundary")
        return 1

    # ---- 2. run generations, pool every candidate -----------------------
    print("=" * 72)
    print(f"RUNNING {GENERATIONS} generation(s) for {len(firms)} firm(s) "
          f"on the snapshot")
    # A *cohort* is one firm's one generation: the eight candidates the
    # selection rule actually chooses between. Pooling candidates across
    # cohorts measures something else entirely — see the note below.
    cohorts = []                    # list of [(in-sample, holdout), ...]
    by_firm = defaultdict(int)
    for firm in firms:
        spec = eco.specs().get(firm.firm_key)
        analysts = spec.analysts if spec else ("technical", "sentiment", "macro")
        record = firm
        for generation in range(1, GENERATIONS + 1):
            try:
                gen = evolver.evolve(record, market, generation, analysts)
            except Exception as exc:                        # noqa: BLE001
                print(f"  {firm.firm_key} gen {generation}: FAILED {exc}")
                break
            scored = [(float(c.fitness), float(c.holdout_fitness))
                      for c in gen.candidates if c.holdout_fitness is not None]
            if len(scored) >= 4:
                cohorts.append(scored)
                by_firm[firm.firm_key] += len(scored)
            record = eco.store.require_firm_by_id(record.id)
        print(f"  {firm.firm_key:26s} {by_firm[firm.firm_key]:4d} scored candidates")

    total = sum(len(c) for c in cohorts)
    if len(cohorts) < 5:
        print(f"\nonly {len(cohorts)} cohorts — not enough to say anything")
        return 1

    # ---- 3. the rank test, within cohorts --------------------------------
    #
    # The pooled statistic is a trap and it fired on the first run: pooling
    # all candidates gave rho = +0.5688 (p < 1e-4, n = 64) while every single
    # per-firm rho was near zero or negative, one of them -1.0. That gap is
    # Simpson's paradox. Candidates from a well-capitalised firm score high on
    # both windows and candidates from a poor one score low on both, so
    # pooling measures *which firm a genome belongs to* — a fact known before
    # any evolution happened — and reads it back as predictive power.
    #
    # The selection rule never chooses between firms. It chooses the best of
    # eight mutants inside one firm's generation. So the cohort is the unit,
    # and the only honest aggregate is over within-cohort rankings.
    import random
    from statistics import fmean, stdev

    def cohort_rho(rows):
        r, _, nn = spearman([a for a, _ in rows], [b for _, b in rows])
        return r, nn

    rhos = [cohort_rho(c)[0] for c in cohorts]
    mean_rho = fmean(rhos)
    print("=" * 72)
    print("RANK TEST  — does in-sample fitness rank predict held-out rank?")
    print(f"  cohorts (firm x generation) : {len(cohorts)}")
    print(f"  candidates                  : {total}")
    print(f"  mean within-cohort rho      : {mean_rho:+.4f}")
    if len(rhos) > 1:
        sd = stdev(rhos)
        se = sd / (len(rhos) ** 0.5)
        print(f"  sd {sd:.4f}   se {se:.4f}   "
              f"t = {mean_rho / se if se else float('nan'):+.3f}")
    pos = sum(1 for r in rhos if r > 0)
    print(f"  cohorts with rho > 0        : {pos}/{len(rhos)}")

    # For contrast, the statistic that lied.
    flat_a = [a for c in cohorts for a, _ in c]
    flat_b = [b for c in cohorts for _, b in c]
    pooled_rho, pooled_p, pooled_n = spearman(flat_a, flat_b)
    print(f"  (pooled across cohorts      : {pooled_rho:+.4f}, p={pooled_p:.4f}, "
          f"n={pooled_n} — the Simpson artefact, not a result)")

    # ---- 4. shuffle null, permuting *within* each cohort -----------------
    #
    # Shuffling the whole pool would destroy the between-cohort structure too,
    # and so would compare against a null that nobody is claiming. Permuting
    # inside each cohort holds "which firm" fixed and destroys only the thing
    # under test: whether the in-sample ordering knows the held-out ordering.
    rng = random.Random(20260914)
    null = []
    for _ in range(500):
        draws = []
        for rows in cohorts:
            held_ = [b for _, b in rows]
            rng.shuffle(held_)
            draws.append(cohort_rho(list(zip([a for a, _ in rows], held_)))[0])
        null.append(fmean(draws))
    null.sort()
    hi = sum(1 for x in null if x >= mean_rho)
    print("  shuffle null (500 permutations, within cohort):")
    print(f"    null mean {fmean(null):+.4f}   "
          f"5th/95th pct {null[24]:+.4f} / {null[474]:+.4f}")
    print(f"    permutations at or above observed: {hi}/500  "
          f"(empirical p = {hi/500:.3f})")

    print("=" * 72)
    works = mean_rho > 0 and hi / 500 < 0.05
    print(f"VERDICT: the selection rule "
          f"{'carries' if works else 'carries no'} out-of-sample information.")
    print(f"         mean within-cohort rho = {mean_rho:+.4f} over {len(cohorts)} "
          f"cohorts ({total} candidates),")
    print(f"         purge gap {gap} bars, fitted/holdout overlap 0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
