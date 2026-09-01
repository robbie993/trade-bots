"""``python -m hive_mind`` — watch it think, then watch it be refused.

Three runs, and the third is the one worth reading twice:

    (default)             a narrated fortnight, one day at a time
    --lock                the full four-phase pipeline, with its verdict
    --show-hallucination  the same genome scored on perfect fills and on real
                          ones, side by side
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from .engine import GodBrokerEngine, backtest
from .evolver import BASE_GENOME
from .lock import LockConfig, WalkForwardLock
from .market import MarketFeed, PerfectVenue, Venue
from .memory import ObsidianMemory


def narrated(days: int, seed: int) -> int:
    """A short run with the log turned on. This is the 'watch it think' mode."""
    feed = MarketFeed(seed=seed, plan=[("calm_bull", 30), ("crash", 14), ("rebound", 24)])
    bars = list(feed)

    # The scouts cannot read an indicator off fewer bars than its window, so
    # the first stretch is genuinely all `hold`. Running it quietly is not
    # hiding anything — it is skipping the part where there is nothing to see.
    warmup = 26
    narrate_from = warmup
    narrate_to = min(len(bars), narrate_from + max(5, days))

    print("=" * 74)
    print("THE HIVE MIND, THINKING OUT LOUD")
    print("=" * 74)
    print(f"genome: {BASE_GENOME.describe()}")
    print("capital: $100,000 | 5 scouts | fills cross the spread and pay impact")
    print(
        f"warming up on bars 1–{warmup} in silence (no scout may read an indicator "
        f"off\nfewer bars than its window), then narrating "
        f"{narrate_to - narrate_from} days across a VIX spike."
    )

    engine = GodBrokerEngine(
        genome=BASE_GENOME,
        memory=ObsidianMemory(),
        venue=Venue(),
        capital=100_000.0,
        seed=seed,
        verbose=False,
    )
    for index, bar in enumerate(bars[:narrate_to]):
        engine.verbose = index >= narrate_from
        engine.step(bar)
    if engine.shares:
        engine._trade_to(0.0, bars[narrate_to - 1], note="end of run")
        engine.equity_curve[-1] = engine.equity(bars[narrate_to - 1])

    from .engine import Result

    result = Result(
        label="narrated run",
        start_capital=engine.start_capital,
        final_equity=engine.equity_curve[-1],
        equity_curve=list(engine.equity_curve),
        trades=list(engine.trades),
        fills=engine.venue.fills,
        fees=engine.venue.total_fees,
        slippage=engine.venue.total_slippage,
        bars=narrate_to,
        genome=engine.genome,
        stopped_out=engine.stopped_out,
    )

    print("\n" + "=" * 74)
    print(result.summary())
    print(f"\nthe venue took ${result.slippage:,.2f} in spread and impact and "
          f"${result.fees:,.2f} in fees across {result.fills} fills.")
    print("\nscouts:")
    print(engine.council.standings())
    print(
        "\nThat number is after costs. Run --show-hallucination to see what it "
        "would have been\nif the simulator had filled every order at the close for free."
    )
    return 0


def hallucination(seed: int) -> int:
    """The same genome, the same bars, two fill models."""
    feed = MarketFeed.scenario("chop_2015", seed=seed)

    honest = backtest(BASE_GENOME, feed, seed=seed, venue=Venue(), label="real fills")
    perfect = backtest(
        BASE_GENOME, feed, seed=seed, venue=PerfectVenue(), label="perfect fills"
    )

    print("=" * 74)
    print("THE SIMULATION HALLUCINATION")
    print("=" * 74)
    print(f"  {perfect.summary()}")
    print(f"  {honest.summary()}")
    gap = perfect.return_pct - honest.return_pct
    print(
        f"\n  The gap is {gap:+.2f} percentage points, on {honest.fills} fills. That is "
        f"money\n  the simulator was inventing — and it is the smaller half of the problem."
    )
    print(
        "\n  The larger half: an evolver scored on the top line learns that size is\n"
        "  free, so it evolves toward more of it. It then arrives live carrying a\n"
        "  genome tuned for a market where slippage does not exist. Perfect fills\n"
        "  do not just flatter a backtest — they teach a habit that only costs\n"
        "  money later. Nothing in this package uses PerfectVenue to decide anything."
    )
    return 0


MIXED_HISTORY = [
    ("calm_bull", 220),
    ("chop", 160),
    ("rate_shock", 90),
    ("grind_down", 70),
    ("crash", 35),
    ("rebound", 80),
    ("calm_bull", 145),
]

# Eight years of nothing but rallies — the training-data trap, as a feed. A
# genome evolved here learns one market and calls it the world.
ONE_SIDED_HISTORY = [
    ("melt_up", 300),
    ("calm_bull", 120),
    ("melt_up", 200),
    ("chop", 80),
    ("melt_up", 150),
]


def locked(seed: int, stress_runs: int, overfit: bool, lenient: bool) -> int:
    """The whole pipeline: phase 1 through phase 4, and the verdict."""
    genome = BASE_GENOME
    plan = MIXED_HISTORY
    if overfit:
        # A trend-follower, trained on a history that is almost all trend.
        genome = replace(BASE_GENOME, trend_bias=0.95, position_pct=0.25, momentum_window=15)
        plan = ONE_SIDED_HISTORY

    history = MarketFeed(seed=seed, plan=plan)
    forward = MarketFeed.scenario("melt_up_2021", seed=seed + 4242)

    config = LockConfig(stress_runs=stress_runs)
    if lenient:
        # Every bar loosened, so the later phases actually execute. The bars
        # ARE the lock, so this is a demonstration of the machinery and not of
        # a strategy. Nothing that runs under --lenient has proved anything.
        config = LockConfig(
            stress_runs=stress_runs,
            gauntlet_min_return_pct=-100.0,
            gauntlet_max_drawdown_pct=100.0,
            gauntlet_min_trades=1,
            stress_min_pass_rate=0.0,
            stress_max_drawdown_pct=100.0,
            stress_worst_case_pct=-100.0,
            paper_min_return_pct=-100.0,
            paper_min_sharpe=-99.0,
        )

    print("=" * 74)
    print("THE STRAITJACKET")
    print("=" * 74)
    print(f"  history : {len(history)} bars, "
          f"{'almost all rallies (the training-data trap)' if overfit else 'seven regimes'}")
    print(f"  forward : {len(forward)} bars the pipeline has never touched")
    print(f"  genome  : {genome.describe()}")
    if lenient:
        print("  NOTE    : --lenient. Every threshold is loosened so you can watch phases")
        print("            3 and 4 run. The thresholds are the lock; nothing proved here.")

    lock = WalkForwardLock(config, seed=seed)

    # Before anything is proved, the engine is not allowed to touch itself.
    permit = lock.permit(genome)
    print(f"\n  permit before phase 1: {'ALLOWED' if permit else 'REFUSED'} — {permit.reason}")

    report = lock.prove(genome, history, forward, verbose=True)
    print("\n" + report.summary())

    if report.genome_out is not None:
        after = lock.permit(report.genome_out)
        print(
            f"\n  permit after the pipeline: "
            f"{'ALLOWED' if after else 'REFUSED'} — {after.reason}"
        )
    return 0 if report.passed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hive_mind",
        description="The Evolving Hive-Mind Trader — a simulation, not a trading bot.",
    )
    parser.add_argument("--lock", action="store_true", help="run the four-phase pipeline")
    parser.add_argument(
        "--show-hallucination",
        action="store_true",
        help="perfect fills vs real ones, on the same bars",
    )
    parser.add_argument("--days", type=int, default=14, help="days to narrate (default 14)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--stress-runs", type=int, default=100, help="regimes in phase 2b (default 100)"
    )
    parser.add_argument(
        "--overfit-demo",
        action="store_true",
        help="with --lock: train a trend-follower on a history of nothing but rallies, "
        "and watch the stress tests catch what the holdout missed",
    )
    parser.add_argument(
        "--lenient",
        action="store_true",
        help="with --lock: loosen every threshold so phases 3 and 4 run. A demo of the "
        "machinery, not of a strategy.",
    )
    args = parser.parse_args(argv)

    if args.lock:
        return locked(args.seed, args.stress_runs, args.overfit_demo, args.lenient)
    if args.show_hallucination:
        return hallucination(args.seed)
    return narrated(args.days, args.seed)


if __name__ == "__main__":
    sys.exit(main())
