"""The walk-forward lock — the straitjacket the engine has to earn its way out of.

Without this file, the engine is a machine for producing a convincing lie. Turn
online evolution on, point it at today's tape, and by lunchtime it will have
mutated into whatever fitted the morning. The equity curve will look excellent,
because it was fitted to the same bars it is being scored on, and every dollar
of that performance is retrospective.

So the genome is locked, and it opens in four stages. Each one answers a
different question, and none of them is skippable.

    Phase 1  HISTORICAL CRUCIBLE   evolve freely — on old data only
    Phase 2  THE GAUNTLET          frozen, once, through data it never saw
             THE STRESS TESTS      frozen, across many regimes it never saw
    Phase 3  PAPER FORWARD-TEST    frozen, on "current" data, no real money
    Phase 4  THE SUICIDE FUND      real money, tiny, sizing genes only

The three properties that make it a lock rather than a checklist:

**The holdout is removed, not avoided.** Training gets a ``WindowFeed`` that
physically has no bars past the split. There is no cursor to mis-set. A leak
from the holdout into training does not look like a bug — it looks like a
strategy that works — so it must be impossible rather than discouraged.

**A failure is permanent.** A genome that loses money in the gauntlet goes in
the graveyard by fingerprint and can never be proposed again. Without that, a
failed idea comes back next week with a different random seed, and the pipeline
becomes a machine for finding the one run in twenty that passed.

**The certificate binds the strategy, not the whole genome.** Phase 4 lets
sizing drift on real money; it must not let the *thesis* drift. So a
certificate names a fingerprint of the strategy genes only. Change how much it
bets and the licence holds; change what it believes and the licence is void
that instant — including by hand, by import, or by a code path nobody has
written yet.

What a completed pipeline does not mean: that the strategy works. Out-of-sample
bars are still past bars, and this repository generates its own. It means the
one thing that can honestly be checked before the money moves — that the
numbers came from data which could not have shaped them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional

from src.money import D, ZERO, percent

from .engine import GodBrokerEngine, Result, backtest
from .evolver import ALL_GENES, SIZING_GENES, STRATEGY_GENES, Evolver, Genome
from .market import SCENARIOS, MarketFeed, Venue


class Phase(str, Enum):
    UNPROVEN = "unproven"
    CRUCIBLE = "historical_crucible"
    GAUNTLET = "gauntlet"
    STRESS = "stress_tests"
    PAPER = "paper_forward_test"
    SUICIDE_FUND = "suicide_fund"
    SCALED = "scaled"
    DEAD = "dead"


# What each phase may mutate. The empty frozensets are the point of the file.
MUTABLE_IN: dict = {
    Phase.UNPROVEN: frozenset(),
    Phase.CRUCIBLE: ALL_GENES,
    Phase.GAUNTLET: frozenset(),
    Phase.STRESS: frozenset(),
    Phase.PAPER: frozenset(),
    Phase.SUICIDE_FUND: SIZING_GENES,
    Phase.SCALED: SIZING_GENES,
    Phase.DEAD: frozenset(),
}


def strategy_fingerprint(genome: Genome) -> str:
    """A name for what a genome *believes*, ignoring how much it bets.

    Sizing is allowed to move on real money and the thesis is not, so the
    licence has to be issued against the half that must hold still.
    """
    canonical = {name: str(getattr(genome, name)) for name in sorted(STRATEGY_GENES)}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Permit:
    """Whether the engine may mutate, which genes, and why."""

    allowed: bool
    reason: str
    genes: frozenset = frozenset()

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class PhaseResult:
    name: str
    passed: bool
    detail: str
    result: Optional[Result] = None
    extra: dict = field(default_factory=dict)

    def summary(self) -> str:
        mark = "PASSED" if self.passed else "FAILED"
        head = f"  [{mark}] {self.name}\n         {self.detail}"
        if self.result is not None:
            head += f"\n         {self.result.summary()}"
        return head


@dataclass
class LockReport:
    genome_in: Optional[Genome] = None
    genome_out: Optional[Genome] = None
    phases: list = field(default_factory=list)
    certificate: Optional[str] = None
    killed: bool = False

    @property
    def passed(self) -> bool:
        return bool(self.phases) and all(p.passed for p in self.phases)

    def summary(self) -> str:
        lines = ["=" * 74, "WALK-FORWARD LOCK", "=" * 74]
        if self.genome_in is not None:
            lines.append(f"  in : {self.genome_in.describe()}")
        lines.extend(p.summary() for p in self.phases)
        if self.genome_out is not None:
            lines.append(f"  out: {self.genome_out.describe()}")
        if self.passed:
            lines.append(
                f"  VERDICT: cleared to size up. certificate {self.certificate} "
                "(strategy genes frozen; sizing may evolve)"
            )
        elif self.killed:
            lines.append(
                "  VERDICT: genome killed. Its fingerprint is in the graveyard and "
                "will not be accepted again."
            )
        else:
            lines.append("  VERDICT: not cleared. It does not touch real money.")
        lines.append("=" * 74)
        return "\n".join(lines)


@dataclass
class LockConfig:
    """Every threshold, in one place, so the whole bar is readable at once."""

    # Decimal, like every other threshold in this repository that decides
    # whether money moves — and these decide the largest one there is.

    # Phase 1
    train_fraction: Decimal = D("0.80")
    generations: int = 6
    population: int = 8

    # Phase 2 — the gauntlet
    gauntlet_min_return_pct: Decimal = D("0.0")
    gauntlet_max_drawdown_pct: Decimal = D("25.0")
    gauntlet_min_trades: int = 10

    # Phase 2b — the stress tests
    stress_runs: int = 100
    stress_min_pass_rate: Decimal = D("0.60")
    stress_max_drawdown_pct: Decimal = D("35.0")
    # No single regime may be a disaster, however good the average is. An
    # average across 100 runs hides the one that ends the account.
    stress_worst_case_pct: Decimal = D("-25.0")

    # Phase 3 — paper forward
    paper_min_return_pct: Decimal = D("0.0")
    paper_min_sharpe: Decimal = D("0.5")

    # Phase 4 — the suicide fund
    suicide_capital: Decimal = D("100.00")
    suicide_days: int = 21
    suicide_min_return_pct: Decimal = D("20.0")
    suicide_min_sharpe: Decimal = D("1.5")

    def __post_init__(self) -> None:
        for name in (
            "train_fraction",
            "gauntlet_min_return_pct",
            "gauntlet_max_drawdown_pct",
            "stress_min_pass_rate",
            "stress_max_drawdown_pct",
            "stress_worst_case_pct",
            "paper_min_return_pct",
            "paper_min_sharpe",
            "suicide_capital",
            "suicide_min_return_pct",
            "suicide_min_sharpe",
        ):
            setattr(self, name, D(getattr(self, name)))


class WalkForwardLock:
    """Holds the phase, the graveyard, and the answer to "may I mutate?"."""

    def __init__(
        self,
        config: Optional[LockConfig] = None,
        seed: int = 20260901,
        stress_markets=None,
    ):
        self.config = config or LockConfig()
        self.seed = int(seed)
        self.phase = Phase.UNPROVEN
        self.graveyard: set = set()
        self.certificate: Optional[str] = None
        self.evolver = Evolver(seed=self.seed)
        # Where phase 2b gets the regimes it has never seen. The default is
        # the generated scenarios; on real history it is disjoint windows of
        # the real tape (see `real_feed.real_stress_windows`), because "many
        # regimes it never saw" means something different when there is only
        # one tape and it is not ours to generate.
        self._stress_markets = stress_markets

    # -- the gate ---------------------------------------------------------
    def permit(self, genome: Genome) -> Permit:
        """May this genome mutate right now, and which genes?"""
        if genome.fingerprint() in self.graveyard:
            return Permit(
                False,
                f"genome {genome.fingerprint()} is in the graveyard — it failed the "
                "gauntlet and does not get a second seed",
            )
        if strategy_fingerprint(genome) in self.graveyard:
            return Permit(
                False,
                f"the strategy {strategy_fingerprint(genome)} is in the graveyard; "
                "re-sizing a killed thesis does not revive it",
            )

        genes = MUTABLE_IN[self.phase]
        if not genes:
            return Permit(
                False,
                f"phase {self.phase.value}: the genome is frozen here. "
                + (
                    "It has proved nothing yet."
                    if self.phase is Phase.UNPROVEN
                    else "This phase measures what it already is; mutating mid-measurement "
                    "would mean measuring nothing."
                ),
            )

        if self.phase in (Phase.SUICIDE_FUND, Phase.SCALED):
            if self.certificate and strategy_fingerprint(genome) != self.certificate:
                return Permit(
                    False,
                    f"the certificate is for strategy {self.certificate}; this genome's "
                    f"is {strategy_fingerprint(genome)}. The thesis changed, so the "
                    "licence is void.",
                )
            return Permit(
                True,
                f"phase {self.phase.value}: sizing only, strategy frozen under "
                f"certificate {self.certificate}",
                SIZING_GENES,
            )

        return Permit(True, f"phase {self.phase.value}: training on historical data only", genes)

    def kill(self, genome: Genome, why: str) -> None:
        """Permanent. Both fingerprints, so a re-size cannot smuggle it back."""
        self.graveyard.add(genome.fingerprint())
        self.graveyard.add(strategy_fingerprint(genome))
        self.phase = Phase.DEAD

    # =====================================================================
    # the pipeline
    # =====================================================================
    def prove(
        self,
        genome: Genome,
        history: MarketFeed,
        forward: MarketFeed,
        verbose: bool = True,
    ) -> LockReport:
        """Run all four phases in order. Stops at the first failure."""
        report = LockReport(genome_in=genome)
        if genome.fingerprint() in self.graveyard or strategy_fingerprint(genome) in self.graveyard:
            report.killed = True
            report.phases.append(
                PhaseResult("phase 0 — graveyard check", False, "this genome was already killed")
            )
            return report

        say = print if verbose else (lambda *a, **k: None)

        # -- Phase 1 ------------------------------------------------------
        say("\nPhase 1 — HISTORICAL CRUCIBLE: evolving on old data, holdout removed")
        self.phase = Phase.CRUCIBLE
        evolved, phase1 = self._crucible(genome, history, say)
        report.phases.append(phase1)
        if not phase1.passed:
            return report

        # -- Phase 2 ------------------------------------------------------
        say("\nPhase 2 — THE GAUNTLET: one frozen run through data it has never seen")
        self.phase = Phase.GAUNTLET
        phase2 = self._gauntlet(evolved, history, say, phase1.extra.get("in_sample"))
        report.phases.append(phase2)
        if not phase2.passed:
            self.kill(evolved, phase2.detail)
            report.killed = True
            report.genome_out = evolved
            return report

        say("\nPhase 2b — THE STRESS TESTS: the same frozen genome, many regimes")
        self.phase = Phase.STRESS
        phase2b = self._stress(evolved, say)
        report.phases.append(phase2b)
        if not phase2b.passed:
            self.kill(evolved, phase2b.detail)
            report.killed = True
            report.genome_out = evolved
            return report

        # -- Phase 3 ------------------------------------------------------
        say("\nPhase 3 — PAPER FORWARD-TEST: current data, no evolution, no money")
        self.phase = Phase.PAPER
        phase3 = self._paper(evolved, forward, say)
        report.phases.append(phase3)
        if not phase3.passed:
            report.genome_out = evolved
            return report

        # -- Phase 4 ------------------------------------------------------
        say("\nPhase 4 — THE SUICIDE FUND: $100 of real money, sizing genes only")
        self.certificate = strategy_fingerprint(evolved)
        self.phase = Phase.SUICIDE_FUND
        final, phase4 = self._suicide_fund(evolved, forward, say)
        report.phases.append(phase4)
        report.genome_out = final
        if phase4.passed:
            self.phase = Phase.SCALED
            report.certificate = self.certificate
        else:
            # A failed suicide fund is not a smaller position, it is a stop.
            # Leaving the lock in SUICIDE_FUND would keep permitting sizing
            # mutations on real money that has just been shown not to work.
            self.phase = Phase.PAPER
        return report

    # -- phase 1 ----------------------------------------------------------
    def _crucible(self, genome: Genome, history: MarketFeed, say) -> tuple:
        bars = len(history)
        split = int(bars * self.config.train_fraction)
        if split < 120 or bars - split < 60:
            return genome, PhaseResult(
                "phase 1 — historical crucible",
                False,
                f"{bars} bars cannot be split {self.config.train_fraction:.0%}/"
                f"{1 - self.config.train_fraction:.0%} into a usable train and holdout",
            )

        train = history.window(0, split)
        say(f"         train bars 0–{split}, holdout {split}–{bars} (removed from the feed)")

        incumbent = genome
        incumbent_score = self._score(backtest(incumbent, train, seed=self.seed, label="incumbent"))
        for generation in range(1, self.config.generations + 1):
            candidates = self.evolver.population(
                incumbent, size=self.config.population, generation=generation
            )
            scored = []
            for i, candidate in enumerate(candidates):
                # A fresh feed and a fresh memory per candidate, and the *same*
                # seed for all of them. The seed drives the scouts' own
                # randomness, so varying it per candidate would let a mutant
                # win on a luckier council rather than a better genome — and
                # the winner would then not reproduce when re-run.
                result = backtest(
                    candidate,
                    history.window(0, split),
                    seed=self.seed,
                    label=f"gen{generation}.{i}",
                )
                scored.append((self._score(result), candidate, result))
            scored.sort(key=lambda row: -row[0])
            best_score, best, best_result = scored[0]
            if best_score > incumbent_score:
                self.evolver.record(generation, incumbent, best)
                say(
                    f"         gen {generation}: fitness {incumbent_score:+.2f} -> "
                    f"{best_score:+.2f}  {incumbent.diff(best)}"
                )
                incumbent, incumbent_score = best, best_score
            else:
                say(f"         gen {generation}: nothing beat the incumbent ({incumbent_score:+.2f})")

        in_sample = backtest(incumbent, history.window(0, split), seed=self.seed, label="in-sample")
        return incumbent, PhaseResult(
            "phase 1 — historical crucible",
            True,
            f"evolved over {self.config.generations} generations on bars 0–{split}; the "
            f"holdout was never in the feed. Passing here claims nothing about the "
            f"strategy — only that training happened where it was allowed to. The "
            f"in-sample fitness below is the number phase 2 exists to distrust.",
            in_sample,
            {"split": split, "fitness": incumbent_score, "in_sample": in_sample},
        )

    def _score(self, result: Result) -> Decimal:
        """Fitness: return, penalised by drawdown and by too small a sample.

        Drawdown at half weight, because a return earned through a 30% hole is
        not the same return. The small-sample term is not a penalty for being
        new — it is a refusal to reward a number built from four trades.
        """
        base = result.return_pct - result.max_drawdown_pct / D(2)
        if result.closed_trades < 10:
            base = base * D(result.closed_trades) / D(10)
        return percent(base)

    # -- phase 2 ----------------------------------------------------------
    def _gauntlet(
        self,
        genome: Genome,
        history: MarketFeed,
        say,
        in_sample: Optional[Result] = None,
    ) -> PhaseResult:
        split = int(len(history) * self.config.train_fraction)
        holdout = history.window(split, len(history))
        result = backtest(genome, holdout, seed=self.seed, label="holdout")
        say(f"         {result.summary()}")

        # The comparison that names overfitting out loud. A genome that scored
        # well in training and badly here did not get unlucky: the training
        # number was the memorised one, and this is what was underneath it.
        if in_sample is not None:
            say(
                f"         in-sample {in_sample.return_pct:+.2f}% vs holdout "
                f"{result.return_pct:+.2f}% — a gap of "
                f"{in_sample.return_pct - result.return_pct:+.2f} points"
            )

        failures = []
        if result.return_pct <= self.config.gauntlet_min_return_pct:
            failures.append(
                f"returned {result.return_pct:+.2f}% on unseen bars, at or under the "
                f"{self.config.gauntlet_min_return_pct:.1f}% floor"
            )
        if result.max_drawdown_pct > self.config.gauntlet_max_drawdown_pct:
            failures.append(
                f"drew down {result.max_drawdown_pct:.2f}%, over the "
                f"{self.config.gauntlet_max_drawdown_pct:.1f}% limit"
            )
        if result.closed_trades < self.config.gauntlet_min_trades:
            failures.append(
                f"{result.closed_trades} closed trades is under the "
                f"{self.config.gauntlet_min_trades} needed to mean anything — "
                "unproven, which is not the same as passed"
            )
        return PhaseResult(
            "phase 2 — the gauntlet",
            not failures,
            "; ".join(failures) if failures else "survived the holdout it was never shown",
            result,
        )

    # -- phase 2b ---------------------------------------------------------
    def stress_plan(self) -> list:
        """``stress_runs`` distinct (scenario, seed) markets, deterministically."""
        names = sorted(SCENARIOS)
        return [
            (names[i % len(names)], 9_000 + i * 37)
            for i in range(max(1, self.config.stress_runs))
        ]

    def stress_markets(self) -> list:
        """``(label, feed, seed)`` for every regime phase 2b will run."""
        if self._stress_markets is not None:
            return list(self._stress_markets(self.config.stress_runs))
        return [
            (name, MarketFeed.scenario(name, seed=seed), seed)
            for name, seed in self.stress_plan()
        ]

    def _stress(self, genome: Genome, say) -> PhaseResult:
        runs = []
        markets = self.stress_markets()
        if not markets:
            return PhaseResult(
                "phase 2b — the stress tests",
                False,
                "no stress regimes were available to run — an untested genome is "
                "not a passing one",
            )
        for scenario, feed, seed in markets:
            result = backtest(genome, feed, seed=seed, label=scenario)
            passed = (
                result.return_pct > 0
                and result.max_drawdown_pct <= self.config.stress_max_drawdown_pct
            )
            runs.append((scenario, result, passed))

        survived = sum(1 for _, _, ok in runs if ok)
        rate = D(survived) / D(len(runs))
        worst = min(runs, key=lambda row: row[1].return_pct)
        by_scenario: dict = {}
        for scenario, result, ok in runs:
            entry = by_scenario.setdefault(scenario, [0, 0, ZERO])
            entry[0] += 1
            entry[1] += 1 if ok else 0
            entry[2] += result.return_pct

        for scenario in sorted(by_scenario):
            count, ok, total = by_scenario[scenario]
            say(
                f"         {scenario:<14} {ok:>3}/{count:<3} survived, "
                f"mean {percent(total / D(count)):+7}%"
            )

        failures = []
        if rate < self.config.stress_min_pass_rate:
            failures.append(
                f"survived {survived}/{len(runs)} regimes ({rate:.0%}), under the "
                f"{self.config.stress_min_pass_rate:.0%} required"
            )
        if worst[1].return_pct < self.config.stress_worst_case_pct:
            failures.append(
                f"worst regime ({worst[0]}) lost {worst[1].return_pct:.2f}%, past the "
                f"{self.config.stress_worst_case_pct:.1f}% single-regime limit — an "
                "average that hides an account-ending run is not a pass"
            )
        return PhaseResult(
            "phase 2b — the stress tests",
            not failures,
            "; ".join(failures)
            if failures
            else f"survived {survived}/{len(runs)} unseen regimes; worst was "
            f"{worst[0]} at {worst[1].return_pct:+.2f}%",
            extra={"pass_rate": rate, "runs": len(runs), "worst": worst[0]},
        )

    # -- phase 3 ----------------------------------------------------------
    def _paper(self, genome: Genome, forward: MarketFeed, say) -> PhaseResult:
        engine = GodBrokerEngine(
            genome=genome,
            venue=Venue(),
            capital=100_000.0,
            seed=self.seed,
            lock=self,
            online_evolution=True,  # asked for, and refused — see below
        )
        result = engine.run(forward, label="paper forward")
        say(f"         {result.summary()}")

        # The engine was told to evolve and did not, because this phase's
        # permit is empty. Asserting it here turns the freeze from a claim in
        # a docstring into a checked fact about the run that just happened.
        mutated = engine.mutations
        say(f"         evolution attempts refused: {len(engine.refusals)}, mutations: {mutated}")

        failures = []
        if mutated:
            failures.append(f"the genome moved {mutated} time(s) during a frozen phase")
        if result.return_pct <= self.config.paper_min_return_pct:
            failures.append(
                f"returned {result.return_pct:+.2f}% forward, at or under the "
                f"{self.config.paper_min_return_pct:.1f}% floor"
            )
        sharpe = result.sharpe
        if sharpe is None:
            failures.append("too few bars to compute a Sharpe worth quoting")
        elif sharpe < self.config.paper_min_sharpe:
            failures.append(
                f"Sharpe {sharpe:.2f} is under the {self.config.paper_min_sharpe:.2f} required"
            )
        return PhaseResult(
            "phase 3 — paper forward-test",
            not failures,
            "; ".join(failures)
            if failures
            else "held up on current data with evolution frozen",
            result,
        )

    # -- phase 4 ----------------------------------------------------------
    def _suicide_fund(self, genome: Genome, forward: MarketFeed, say) -> tuple:
        days = self.config.suicide_days
        bars = list(forward)[-days:]
        engine = GodBrokerEngine(
            genome=genome,
            venue=Venue(),
            capital=self.config.suicide_capital,
            seed=self.seed,
            lock=self,
            online_evolution=True,  # permitted here — sizing genes only
        )
        result = engine.run(bars, label="suicide fund")
        say(f"         {result.summary()}")
        say(
            f"         strategy fingerprint {strategy_fingerprint(genome)} -> "
            f"{strategy_fingerprint(engine.genome)} "
            f"({'unchanged' if strategy_fingerprint(engine.genome) == strategy_fingerprint(genome) else 'CHANGED'})"
        )

        failures = []
        if strategy_fingerprint(engine.genome) != strategy_fingerprint(genome):
            failures.append("the strategy genes moved on real money")
        if result.return_pct < self.config.suicide_min_return_pct:
            failures.append(
                f"turned ${self.config.suicide_capital:.0f} into "
                f"${result.final_equity:.2f} ({result.return_pct:+.2f}%), under the "
                f"{self.config.suicide_min_return_pct:.0f}% required to scale up"
            )
        sharpe = result.sharpe
        if sharpe is None:
            failures.append(
                f"{days} days is too short to compute a Sharpe — which is itself the "
                "answer: one month of a small account cannot clear a Sharpe bar"
            )
        elif sharpe < self.config.suicide_min_sharpe:
            failures.append(
                f"Sharpe {sharpe:.2f} is under the {self.config.suicide_min_sharpe:.2f} required"
            )
        return engine.genome, PhaseResult(
            "phase 4 — the suicide fund",
            not failures,
            "; ".join(failures) if failures else "cleared to size up, slowly",
            result,
        )


__all__ = [
    "LockConfig",
    "LockReport",
    "MUTABLE_IN",
    "Permit",
    "Phase",
    "PhaseResult",
    "WalkForwardLock",
    "strategy_fingerprint",
]
