"""The crucible — a genome must survive data it was never allowed to see.

Everything else in ``brain/`` measures a strategy against the bars it was
tuned on. That number is not worthless, but it is not evidence either: an
evolver given enough generations will always find a genome that fits the
sample, and the better it gets at fitting, the less the fitness means. The
whole apparatus — population, mutation, promotion — is a machine for
producing exactly that illusion, and it produces it most convincingly right
before you deploy it.

So the crucible cuts the history into folds and runs each one twice:

    train window   the evolver may mutate freely
    test window    the winner is frozen and simply executed

and the only numbers that count toward the verdict come from the test window.

**The blindfold is structural, not procedural.** The evolver does not receive
the whole history with an instruction to stop at bar 400; it receives a
``WindowedFeed`` whose ``series()`` returns bars 0 to 399 and has no method
that returns any others. There is no cursor to mis-set and no convention to
forget, which matters because the failure this file exists to prevent is
precisely one that looks like success when it happens.

Two things this deliberately does not claim:

* It does not make a strategy profitable. Most genomes fail here, and a
  failing verdict on real history is the most useful output this repository
  produces.
* It does not prove anything about the *future*. Out-of-sample bars are still
  past bars. What a passing certificate says is narrower and worth saying:
  this genome was measured on data that could not have shaped it, and it
  survived. Everything that fails that test would have failed live too.

The verdict, and what it licenses, live in ``lock.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Optional, Sequence

from ...money import D, ZERO, percent
from ..backtest import Backtester, BacktestResult
from ..config import CrucibleConfig, TradingConfig
from ..data.market_data import MarketData
from ..models import FirmRecord
from ..store import TradingStore
from .evolver import BASE_GENOME, Evolver


class NotEnoughHistory(RuntimeError):
    """The data cannot support the requested schedule of folds.

    Raised rather than quietly shrinking the windows: a gauntlet run on six
    bars would return a verdict, and that verdict would be noise wearing the
    word "certified".
    """


# =========================================================================
# windows
# =========================================================================
@dataclass(frozen=True)
class Window:
    """A half-open range of bar indices, ``[start, end)``."""

    start: int
    end: int

    @property
    def bars(self) -> int:
        return max(0, self.end - self.start)

    def __str__(self) -> str:
        return f"[{self.start}:{self.end})"


class WindowedFeed:
    """A feed with the rest of history physically removed.

    ``MarketFeed`` has exactly one method, so slicing it is the complete
    blindfold: nothing downstream — not ``MarketData``, not the backtester,
    not an analyst reaching for a longer lookback — has any path to a bar
    outside the window, because the object that would have supplied it never
    saw one either.
    """

    def __init__(self, feed, window: Window):
        self.feed = feed
        self.window = window
        self.name = f"{getattr(feed, 'name', 'feed')}{window}"

    def series(self, symbol: str) -> list:
        return list(self.feed.series(symbol))[self.window.start : self.window.end]


@dataclass(frozen=True)
class Fold:
    """One train/test pair. ``lead_in`` is the part of the test slice that
    exists only so indicators have history; no decision is made on it."""

    number: int
    train: Window
    test: Window
    lead_in: int

    @property
    def test_slice(self) -> Window:
        """What the test run is actually handed: lead-in bars, then the window.

        The lead-in bars were part of training, and using them is not a leak:
        they are read to fill an indicator, never to decide. A genome that
        needs 30 bars of history cannot start trading on the first bar of an
        unseen window without them, and forcing it to would measure the
        warm-up, not the strategy.
        """
        return Window(max(0, self.test.start - self.lead_in), self.test.end)

    def summary(self) -> str:
        return f"fold {self.number}: train {self.train} ({self.train.bars} bars), test {self.test} ({self.test.bars} bars)"


def split(
    total_bars: int,
    folds: int = 3,
    test_pct: Decimal = D("0.15"),
    warmup: int = 30,
    anchored: bool = True,
) -> list[Fold]:
    """Cut a history into walk-forward folds, newest window last.

    Test windows are contiguous, equal, and never overlap: each fold's
    training data ends exactly where its test data begins. Fold *n*'s test
    window is fold *n+1*'s training data, which is the point — the schedule
    walks forward through time the way the operator will.
    """
    folds = max(1, int(folds))
    total = int(total_bars)
    test_bars = int(D(total) * D(test_pct))
    if test_bars < 2:
        # Two bars is the floor at which a fill and a mark can both happen.
        raise NotEnoughHistory(
            f"a test window of {test_pct} of {total} bars is {test_bars} bar(s); "
            "raise TRADE_CRUCIBLE_TEST_PCT or lengthen the history"
        )

    minimum_train = warmup + 20
    first_start = total - folds * test_bars
    if first_start < minimum_train:
        needed = minimum_train + folds * test_bars
        raise NotEnoughHistory(
            f"{total} bars cannot support {folds} folds of {test_bars} test bars with a "
            f"{warmup}-bar warm-up: the first training window would be {first_start} bars, "
            f"under the {minimum_train} it needs. Use at least {needed} bars "
            f"(TRADE_HISTORY_DAYS), fewer folds, or a smaller TRADE_CRUCIBLE_TEST_PCT."
        )

    train_bars = first_start  # rolling folds all train on this many bars
    out: list[Fold] = []
    for number in range(1, folds + 1):
        test_start = first_start + (number - 1) * test_bars
        test_end = test_start + test_bars if number < folds else total
        train_start = 0 if anchored else max(0, test_start - train_bars)
        out.append(
            Fold(
                number=number,
                train=Window(train_start, test_start),
                test=Window(test_start, test_end),
                lead_in=warmup,
            )
        )
    return out


# =========================================================================
# results
# =========================================================================
@dataclass
class FoldResult:
    fold: Fold
    genome: dict
    in_sample: Optional[BacktestResult] = None
    out_of_sample: Optional[BacktestResult] = None
    failures: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def decay_pct(self) -> Optional[Decimal]:
        """How much of the training fitness did not survive the holdout.

        None when the in-sample fitness was not positive — there is nothing to
        decay from, and the fold fails on its own numbers instead.
        """
        if self.in_sample is None or self.out_of_sample is None:
            return None
        trained = self.in_sample.fitness
        if trained <= 0:
            return None
        return percent((trained - self.out_of_sample.fitness) / trained * D(100))

    def summary(self) -> str:
        if self.out_of_sample is None:
            return f"  {self.fold.summary()} — not run"
        oos = self.out_of_sample
        decay = self.decay_pct
        head = (
            f"  fold {self.fold.number}  train {self.fold.train} -> test {self.fold.test}  "
            f"in-sample fitness {self.in_sample.fitness if self.in_sample else '-'}, "
            f"out-of-sample {oos.return_pct}% return, {oos.max_drawdown_pct}% drawdown, "
            f"{oos.closed_trades} closed trades, fitness {oos.fitness}"
            + (f", decay {decay}%" if decay is not None else "")
        )
        if self.passed:
            return head + "\n    PASSED"
        return head + "\n" + "\n".join(f"    FAILED: {why}" for why in self.failures)

    def to_payload(self) -> dict:
        return {
            "fold": self.fold.number,
            "train": [self.fold.train.start, self.fold.train.end],
            "test": [self.fold.test.start, self.fold.test.end],
            "genome": self.genome,
            "in_sample_fitness": str(self.in_sample.fitness) if self.in_sample else None,
            "out_of_sample": {
                "return_pct": str(self.out_of_sample.return_pct),
                "max_drawdown_pct": str(self.out_of_sample.max_drawdown_pct),
                "sharpe": str(self.out_of_sample.sharpe) if self.out_of_sample.sharpe else None,
                "closed_trades": self.out_of_sample.closed_trades,
                "fitness": str(self.out_of_sample.fitness),
            }
            if self.out_of_sample
            else None,
            "decay_pct": str(self.decay_pct) if self.decay_pct is not None else None,
            "failures": list(self.failures),
        }


@dataclass
class CrucibleReport:
    """What the gauntlet found, and the one genome it is about.

    ``genome`` is the survivor of the *last* fold — evolved on the most recent
    training window and then measured on the most recent window nothing had
    read. The earlier folds are evidence about the method that produced it,
    not about that genome, and the verdict says so by requiring the final fold
    to pass on its own.
    """

    firm_key: str
    genome: dict = field(default_factory=dict)
    symbols: list = field(default_factory=list)
    data_source: str = ""
    total_bars: int = 0
    folds: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    generations: int = 0

    @property
    def folds_passed(self) -> int:
        return sum(1 for f in self.folds if f.passed)

    @property
    def passed(self) -> bool:
        return bool(self.folds) and not self.reasons

    @property
    def oos_results(self) -> list:
        return [f.out_of_sample for f in self.folds if f.out_of_sample is not None]

    def _mean(self, values: list) -> Decimal:
        return percent(sum(values, ZERO) / D(len(values))) if values else ZERO

    @property
    def oos_return_pct(self) -> Decimal:
        return self._mean([D(r.return_pct) for r in self.oos_results])

    @property
    def oos_max_drawdown_pct(self) -> Decimal:
        return max([D(r.max_drawdown_pct) for r in self.oos_results], default=ZERO)

    @property
    def oos_sharpe(self) -> Optional[Decimal]:
        sharpes = [D(r.sharpe) for r in self.oos_results if r.sharpe is not None]
        return self._mean(sharpes) if sharpes else None

    @property
    def oos_fitness(self) -> Decimal:
        return self._mean([D(r.fitness) for r in self.oos_results])

    @property
    def in_sample_fitness(self) -> Decimal:
        return self._mean([D(f.in_sample.fitness) for f in self.folds if f.in_sample])

    @property
    def oos_trades(self) -> int:
        return sum(r.closed_trades for r in self.oos_results)

    def summary(self) -> str:
        verdict = "PASSED" if self.passed else "FAILED"
        lines = [
            f"crucible: {self.firm_key} — {verdict} "
            f"({self.folds_passed}/{len(self.folds)} folds) on {self.data_source}, "
            f"{self.total_bars} bars, {self.generations} generation(s) per training window",
        ]
        lines.extend(f.summary() for f in self.folds)
        lines.append(
            f"  out of sample overall: {self.oos_return_pct}% mean return, "
            f"worst drawdown {self.oos_max_drawdown_pct}%, {self.oos_trades} closed trades, "
            f"mean fitness {self.oos_fitness} (in-sample {self.in_sample_fitness})"
        )
        if self.reasons:
            lines.append("  verdict:")
            lines.extend(f"    - {why}" for why in self.reasons)
        else:
            lines.append("  verdict: survived every window it was not allowed to see")
        lines.append(f"  genome: {json.dumps(self.genome, sort_keys=True)}")
        return "\n".join(lines)

    def to_payload(self) -> dict:
        return {
            "firm_key": self.firm_key,
            "symbols": list(self.symbols),
            "data_source": self.data_source,
            "total_bars": self.total_bars,
            "generations": self.generations,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "folds": [f.to_payload() for f in self.folds],
        }


# =========================================================================
# the gauntlet
# =========================================================================
class Crucible:
    """Runs the walk-forward gauntlet. Writes nothing — ``lock.py`` does that."""

    def __init__(self, store: TradingStore, config: Optional[TradingConfig] = None):
        self.store = store
        self.config = config or TradingConfig()
        # Promotion is switched off inside the crucible for the same reason
        # the whole file exists: the evolver's own opinion of a genome is the
        # thing under test, and a run that could promote would be scoring its
        # own homework against the firm's live row.
        self._evo_config = replace(
            self.config, brain=replace(self.config.brain, promote_winners=False)
        )
        self.evolver = Evolver(store, self._evo_config)

    @property
    def settings(self) -> CrucibleConfig:
        return self.config.crucible

    def folds(self, total_bars: int) -> list:
        s = self.settings
        return split(
            total_bars,
            folds=s.folds,
            test_pct=s.test_pct,
            warmup=s.warmup,
            anchored=s.anchored,
        )

    def run(
        self,
        firm: FirmRecord,
        feed,
        symbols: Sequence[str] = (),
        analysts: Sequence[str] = ("technical", "sentiment", "macro"),
        generations: Optional[int] = None,
    ) -> CrucibleReport:
        """Evolve inside each training window, freeze, and measure on the rest."""
        universe = [s.upper() for s in (symbols or firm.universe)] or ["SPY"]
        total = MarketData(feed, universe).length()
        rounds = self.settings.generations if generations is None else int(generations)
        report = CrucibleReport(
            firm_key=firm.firm_key,
            symbols=universe,
            data_source=getattr(feed, "name", "") or "unknown",
            total_bars=total,
            generations=rounds,
            genome=self.evolver.normalise(firm.genome or BASE_GENOME),
        )

        carried = report.genome
        for fold in self.folds(total):
            result = FoldResult(fold=fold, genome=carried)

            # -- train: the evolver sees this window and nothing else -------
            trained = replace_genome(firm, carried)
            train_market = MarketData(WindowedFeed(feed, fold.train), universe)
            trainer = Backtester(self._evo_config, warmup=self.settings.warmup)
            for generation in range(1, rounds + 1):
                gen = self.evolver.compete(
                    trained, train_market, generation, analysts, backtester=trainer
                )
                if gen.winner is not None:
                    trained = replace_genome(firm, gen.winner.genome)
                    result.in_sample = gen.winner.result
            if rounds == 0:
                # Nothing was evolved; score the frozen genome in-sample anyway
                # so the decay comparison has a left-hand side.
                result.in_sample = trainer.run(
                    firm_key=firm.firm_key,
                    symbols=universe,
                    market=MarketData(WindowedFeed(feed, fold.train), universe),
                    genome=carried,
                    analysts=analysts,
                    capital=firm.initial_allocation or self.config.firm.allocation,
                    risk_limit=firm.risk_limit,
                )
            result.genome = trained.genome

            # -- test: frozen, on bars the training run could not read ------
            test_market = MarketData(WindowedFeed(feed, fold.test_slice), universe)
            # The warm-up is exactly the lead-in, so the first decision of the
            # run lands on the first bar of the unseen window — not one earlier.
            result.out_of_sample = Backtester(self.config, warmup=fold.lead_in).run(
                firm_key=firm.firm_key,
                symbols=universe,
                market=test_market,
                genome=trained.genome,
                analysts=analysts,
                capital=firm.initial_allocation or self.config.firm.allocation,
                risk_limit=firm.risk_limit,
            )
            result.failures = self.judge_fold(result)
            report.folds.append(result)
            carried = trained.genome

        report.genome = carried
        report.reasons = self.judge(report)
        return report

    # -- judgement --------------------------------------------------------
    def judge_fold(self, result: FoldResult) -> list:
        """Every way this fold failed, in English. Empty means it survived."""
        s = self.settings
        oos = result.out_of_sample
        failures: list = []
        if oos is None:
            return ["the test window did not run"]
        if oos.closed_trades < s.min_oos_trades:
            failures.append(
                f"{oos.closed_trades} closed trade(s) out of sample, under the "
                f"{s.min_oos_trades} needed to say anything — unproven, not passed"
            )
        if D(oos.return_pct) < D(s.min_oos_return_pct):
            failures.append(
                f"returned {oos.return_pct}% out of sample, under the "
                f"{s.min_oos_return_pct}% floor"
            )
        if D(oos.max_drawdown_pct) > D(s.max_oos_drawdown_pct):
            failures.append(
                f"drew down {oos.max_drawdown_pct}% out of sample, over the "
                f"{s.max_oos_drawdown_pct}% limit"
            )
        decay = result.decay_pct
        if decay is not None and decay > D(s.max_fitness_decay_pct):
            failures.append(
                f"fitness {result.in_sample.fitness} in training collapsed to "
                f"{oos.fitness} out of sample ({decay}% decay, limit "
                f"{s.max_fitness_decay_pct}%) — that gap is overfitting, not bad luck"
            )
        return failures

    def judge(self, report: CrucibleReport) -> list:
        """Why the whole gauntlet failed. Empty means it passed."""
        s = self.settings
        if not report.folds:
            return ["no folds were run"]
        required = s.min_folds_passed or len(report.folds)
        reasons: list = []
        if report.folds_passed < required:
            reasons.append(
                f"{report.folds_passed} of {len(report.folds)} folds passed; "
                f"{required} were required"
            )
        last = report.folds[-1]
        if not last.passed:
            reasons.append(
                "the final fold failed, and the final fold is the only one that "
                "measured this exact genome on unseen data"
            )
        if report.oos_fitness <= 0:
            reasons.append(
                f"mean out-of-sample fitness {report.oos_fitness} is not positive"
            )
        return reasons


def replace_genome(firm: FirmRecord, genome: dict) -> FirmRecord:
    """A detached copy of the firm carrying a different genome.

    Detached on purpose: the crucible never holds a handle on the row it might
    later be asked to change.
    """
    clone = FirmRecord(**{**firm.__dict__, "genome": dict(genome)})
    return clone


__all__ = [
    "Crucible",
    "CrucibleReport",
    "Fold",
    "FoldResult",
    "NotEnoughHistory",
    "Window",
    "WindowedFeed",
    "replace_genome",
    "split",
]
