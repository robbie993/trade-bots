"""The validation harness — a separate path that beats on the code with real history.

Nothing in here trades, holds money, or writes to the engine. It loads a real
tape, runs genomes through the walk-forward lock against it, and prints what
died where. If this file crashes, nothing that could ever hold a position even
notices.

    python -m hive_mind.crucible_real                 # the default run
    python -m hive_mind.crucible_real --genomes 20    # fewer, faster
    python -m hive_mind.crucible_real --spend-holdout # the sealed years — logged

Four things this does that a naive "run 100 genomes and report the pass rate"
does not, each because the naive version answers a question nobody asked.

**It seals the recent years and counts your looks.** There is exactly one real
SPY history. Tuning the scout logic until the survival rate clears 70% on it
is the training-data trap moved up one level: from the genome to the person
writing the genome's code. The overfitting is identical and the backtest
cannot see it, because *you* are now the thing being fitted. So the last
``--holdout-years`` are sealed out of every default run, and every run appends
to a log — the harness tells you this is look #7 at this dataset, and a
survival rate on the seventh look is weaker evidence than the same number on
the first. That is not bookkeeping, it is the only defence there is.

**It reports where genomes died, not just how many.** "36% survived" is
compatible with a gauntlet that is far too easy and one that is far too hard.
The phase-by-phase mortality tells you which.

**It prints buy-and-hold next to the result.** SPY compounded across most of
these windows. A strategy that survives a gauntlet while underperforming a
thing you could have done by falling asleep has not demonstrated an edge; it
has demonstrated that the gauntlet does not ask about opportunity cost.

**It reads a high pass rate as a warning.** These genomes are drawn uniformly
at random. If a large share of *random* strategies clears the pipeline, the
likelier explanation is a lenient pipeline, not a fertile architecture. The
banner at the bottom says so in both directions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.money import D, ZERO, percent

from .evolver import create_random_genome
from .lock import LockConfig, WalkForwardLock
from .real_feed import MARKET_DIR, RealDataMissing, RealFeed, stress_source

TRADING_DAYS = 252
LOG_NAME = ".crucible_log.jsonl"
CERTIFIED = "certified"


# =========================================================================
# the seal, and the count of how often you have looked past it
# =========================================================================
def dataset_fingerprint(feed: RealFeed) -> str:
    """A name for this exact tape, so the log counts looks at *one* dataset."""
    bars = feed.bars()
    blob = f"{feed.symbol}|{len(bars)}|{bars[0].day}|{bars[-1].day}|{bars[-1].close}"
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def read_log(directory: Path) -> list:
    path = Path(directory) / LOG_NAME
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def append_log(directory: Path, record: dict) -> None:
    path = Path(directory) / LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # If a previous write was cut off mid-line, start a new one rather than
    # gluing this record onto the broken end — which would lose two records
    # instead of one, and quietly undercount the looks.
    prefix = ""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            prefix = "" if handle.read(1) == b"\n" else "\n"
    with path.open("a") as handle:
        handle.write(prefix + json.dumps(record, sort_keys=True) + "\n")


def looks_so_far(directory: Path, fingerprint: str, holdout: bool = False) -> int:
    return sum(
        1
        for r in read_log(directory)
        if r.get("dataset") == fingerprint and (not holdout or r.get("spent_holdout"))
    )


# =========================================================================
# the split
# =========================================================================
class Split:
    """Three windows the harness may use, and one it may not.

        [        history        ][ forward ][ SEALED ]
         phases 1 and 2           phase 3    nothing, by default

    The seal is at the end because that is where the future is. Holding out a
    random middle slice would leave the most recent regime in the training
    data, which is the one a deployed strategy meets first.
    """

    def __init__(self, feed: RealFeed, holdout_years: float, forward_years: float):
        self.feed = feed
        total = len(feed)
        self.sealed_bars = int(holdout_years * TRADING_DAYS)
        self.forward_bars = int(forward_years * TRADING_DAYS)
        self.history_end = total - self.sealed_bars - self.forward_bars
        self.forward_end = total - self.sealed_bars
        self.total = total

        if self.history_end < TRADING_DAYS * 5:
            raise RealDataMissing(
                f"{total} bars leaves only {self.history_end} for training after a "
                f"{holdout_years}-year seal and a {forward_years}-year forward window. "
                "Load more history, or shorten the seal — knowing what that costs."
            )

    @property
    def history(self):
        return self.feed.window(0, self.history_end)

    @property
    def forward(self):
        return self.feed.window(self.history_end, self.forward_end)

    @property
    def sealed(self):
        return self.feed.window(self.forward_end, self.total)

    def describe(self) -> str:
        bars = self.feed.bars()
        def span(a, b):
            return f"{bars[a].day} to {bars[b - 1].day}"
        return (
            f"  history : bars 0–{self.history_end} ({span(0, self.history_end)}) "
            f"— phases 1 and 2\n"
            f"  forward : bars {self.history_end}–{self.forward_end} "
            f"({span(self.history_end, self.forward_end)}) — phase 3\n"
            f"  SEALED  : bars {self.forward_end}–{self.total} "
            f"({span(self.forward_end, self.total)}) — nothing touches this"
        )


# =========================================================================
# the null this has to beat
# =========================================================================
def buy_and_hold(feed) -> dict:
    """What you would have got by buying once and going back to sleep."""
    bars = list(feed)
    if len(bars) < 2:
        return {}
    first, last = bars[0].close, bars[-1].close
    curve = [b.close for b in bars]
    peak, worst = curve[0], ZERO
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak * D(100))
    return {
        "return_pct": percent((last - first) / first * D(100)),
        "max_drawdown_pct": percent(worst),
    }


# =========================================================================
# the run
# =========================================================================
def run(
    genomes: int,
    directory: Path,
    holdout_years: float,
    forward_years: float,
    spend_holdout: bool,
    stress_runs: int,
    generations: int,
    population: int,
    min_years: float,
    allow_vix_proxy: bool,
    save: bool,
) -> int:
    print("=" * 78)
    print("THE VALIDATION HARNESS — real history, no money, nothing to break")
    print("=" * 78)

    feed = RealFeed(directory=directory, allow_vix_proxy=allow_vix_proxy)
    try:
        print(feed.describe())
    except RealDataMissing as exc:
        print(f"\n{exc}")
        return 1

    bars = feed.bars()
    years = len(bars) / TRADING_DAYS
    if years < min_years:
        print(
            f"\n✋ {len(bars)} bars is {years:.1f} years, under the {min_years:.0f} this "
            f"run requires.\n   A survival rate over a few years of one tape is a fact "
            f"about those years.\n   Load more history, or pass --min-years if you know "
            f"why you are doing that."
        )
        return 1

    fingerprint = dataset_fingerprint(feed)
    look = looks_so_far(directory, fingerprint) + 1
    split = Split(feed, holdout_years, forward_years)

    print(f"\ndataset {fingerprint} — {years:.1f} years")
    print(split.describe())
    print(f"\n👁  This is look #{look} at this dataset.")
    if look > 3:
        print(
            "   Each look spends some of what the tape can tell you. If the scout\n"
            "   logic changed between looks, the later numbers are partly a fit to\n"
            "   this history — by you, not by the genome, and no backtest sees it."
        )

    if spend_holdout:
        spent = looks_so_far(directory, fingerprint, holdout=True)
        print(
            f"\n🔓 --spend-holdout: running against the SEALED years. This is spend "
            f"#{spent + 1}.\n   After this they are no longer out of sample and cannot "
            f"be resealed."
        )
        history_feed, forward_feed = split.forward, split.sealed
    else:
        history_feed, forward_feed = split.history, split.forward

    baseline = buy_and_hold(forward_feed)
    if baseline:
        print(
            f"\n📉 Buy and hold over the forward window: "
            f"{baseline['return_pct']:+}% with a {baseline['max_drawdown_pct']}% drawdown.\n"
            f"   Anything that survives below this line beat a gauntlet, not a market."
        )

    config = LockConfig(
        stress_runs=stress_runs, generations=generations, population=population
    )
    print(
        f"\nRunning {genomes} random genomes through the four phases "
        f"({generations} generations x {population} population, {stress_runs} stress windows).\n"
    )

    deaths: Counter = Counter()
    survivors = []
    beat_baseline = 0
    started = time.monotonic()
    for i in range(genomes):
        if i == 1:
            # One genome is a good enough clock, and knowing after nine seconds
            # that this is a four-hour run beats finding out at hour three.
            each = time.monotonic() - started
            total = each * genomes
            print(
                f"       ~{each:.1f}s per genome, so ~{total / 60:.0f} min for "
                f"{genomes}. Ctrl-C is safe; nothing here holds state.\n"
            )
        genome = create_random_genome(seed=1_000 + i)
        lock = WalkForwardLock(
            config,
            seed=1_000 + i,
            stress_markets=stress_source(feed),
        )
        try:
            report = lock.prove(genome, history_feed, forward_feed, verbose=False)
        except Exception as exc:  # noqa: BLE001 - a harness that dies is no harness
            deaths["crashed"] += 1
            print(f"  {i + 1:>3}/{genomes} 💥 {genome.fingerprint()} crashed: {str(exc)[:70]}")
            continue

        if report.passed:
            survivors.append((genome, report))
            final = report.phases[-1].result
            forward_result = next(
                (p.result for p in report.phases if "paper" in p.name and p.result), None
            )
            note = ""
            if forward_result is not None and baseline:
                if forward_result.return_pct > baseline["return_pct"]:
                    beat_baseline += 1
                    note = " (beat buy-and-hold)"
                else:
                    note = " (under buy-and-hold)"
            sharpe = final.sharpe if final is not None else None
            deaths["passed"] += 1
            print(
                f"  {i + 1:>3}/{genomes} ✅ {genome.fingerprint()} PASSED"
                f"{f' sharpe {sharpe}' if sharpe is not None else ''}{note}"
            )
        else:
            failed = next((p for p in report.phases if not p.passed), None)
            where = failed.name if failed else "unknown"
            deaths[where] += 1
            print(f"  {i + 1:>3}/{genomes} ❌ {genome.fingerprint()} died at {where}")

    # -- the report -------------------------------------------------------
    passed = len(survivors)
    rate = 100.0 * passed / genomes if genomes else 0.0
    print("\n" + "-" * 78)
    print("WHERE THEY DIED")
    for where, count in deaths.most_common():
        print(f"  {count:>4}  {where}")
    print("-" * 78)
    print(f"SURVIVAL RATE ON REAL DATA: {passed}/{genomes} ({rate:.0f}%)")
    if baseline and passed:
        print(f"  of those, {beat_baseline} beat buy-and-hold on the forward window")

    print()
    crashed = deaths.get("crashed", 0)
    if crashed > genomes // 4:
        print(
            f"  ⛔ {crashed} of {genomes} runs CRASHED. This is not a result about\n"
            "  strategies, it is a broken harness — fix the exception before reading\n"
            "  a single number above it. (This is what the separation is for: the\n"
            "  thing that just fell over cannot hold a position.)"
        )
    elif rate == 0:
        print(
            "  Nothing survived. That is the most common honest outcome and it is\n"
            "  worth more than a number you have to squint at: random strategies do\n"
            "  not have an edge, and a pipeline that says so is working."
        )
    elif rate > 60:
        print(
            f"  ⚠️  {rate:.0f}% of RANDOM genomes cleared the pipeline. Read that as a\n"
            "  warning about the pipeline before reading it as news about the\n"
            "  architecture: strategies drawn out of a hat should mostly fail. Check\n"
            "  the phase-2 thresholds and whether the stress windows really are\n"
            "  regimes the genome never saw."
        )
    else:
        print(
            f"  {rate:.0f}% of random genomes survived. That is a fact about this\n"
            "  dataset and these thresholds — not yet an edge. What would make it\n"
            "  one: the same rate on the sealed years, on the first look."
        )

    if save and survivors:
        out = Path(directory) / CERTIFIED
        out.mkdir(parents=True, exist_ok=True)
        for genome, report in survivors:
            payload = {
                "genome": {k: str(v) for k, v in genome.to_dict().items()},
                "fingerprint": genome.fingerprint(),
                "certificate": report.certificate,
                "dataset": fingerprint,
                "look": look,
                "spent_holdout": spend_holdout,
                "certified_at": datetime.now(timezone.utc).isoformat(),
            }
            (out / f"{genome.fingerprint()}.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True)
            )
        print(f"\n  {len(survivors)} genome(s) written to {out}/ — the only thing the")
        print("  public build is ever allowed to read.")

    append_log(
        directory,
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "dataset": fingerprint,
            "bars": len(bars),
            "genomes": genomes,
            "passed": passed,
            "rate_pct": round(rate, 2),
            "spent_holdout": spend_holdout,
            "generations": generations,
            "population": population,
            "stress_runs": stress_runs,
            "deaths": dict(deaths),
        },
    )
    print(f"\n  Logged to {Path(directory) / LOG_NAME} as look #{look}.")
    return 0 if passed else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hive_mind.crucible_real",
        description="Run genomes through the walk-forward lock against real history.",
    )
    parser.add_argument("--genomes", type=int, default=100)
    parser.add_argument("--dir", default=str(MARKET_DIR), help="where the CSVs live")
    parser.add_argument("--holdout-years", type=float, default=5.0)
    parser.add_argument("--forward-years", type=float, default=3.0)
    parser.add_argument("--min-years", type=float, default=15.0)
    parser.add_argument("--stress-runs", type=int, default=12)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--population", type=int, default=6)
    parser.add_argument(
        "--spend-holdout",
        action="store_true",
        help="run against the sealed years. Irreversible, and logged as such.",
    )
    parser.add_argument(
        "--allow-vix-proxy",
        action="store_true",
        help="run without VIX.csv, on a labelled realised-volatility proxy",
    )
    parser.add_argument("--no-save", action="store_true", help="do not write survivors")
    args = parser.parse_args(argv)

    return run(
        genomes=args.genomes,
        directory=Path(args.dir),
        holdout_years=args.holdout_years,
        forward_years=args.forward_years,
        spend_holdout=args.spend_holdout,
        stress_runs=args.stress_runs,
        generations=args.generations,
        population=args.population,
        min_years=args.min_years,
        allow_vix_proxy=args.allow_vix_proxy,
        save=not args.no_save,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["Split", "buy_and_hold", "dataset_fingerprint", "looks_so_far", "run"]
