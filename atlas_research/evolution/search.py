"""The genetic search: discovery on TRAIN, then selection on VALIDATION.

    generation 0   80 random genomes + the hand-written default
    generations    top 10 by train fitness -> 10 descendants each (100)
    selection      top 10 of everything ever seen, re-run on VALIDATION;
                   the champion is the best validation fitness among those
                   that pass every gate there (if none pass, the best of
                   them, marked as having failed)

The test period is never touched here. Every genome evaluated is counted and
its daily train returns kept, because the overfitting statistics in
`validation/reality_check.py` need the whole search, not just its winner.
"""
from __future__ import annotations

import multiprocessing as mp
import random
from dataclasses import dataclass, field

import numpy as np

from ..portfolio.engine import Market, backtest
from . import autopsy as A
from .descendants import children
from .gauntlet import Verdict, judge_run
from .genome import Genome, random_genome

_M: Market | None = None
_SPAN: tuple = ()


def _init(m, span):
    global _M, _SPAN
    _M, _SPAN = m, span


def _eval(g: Genome):
    run = backtest(_M, g, *_SPAN)
    v = judge_run(run)
    tags = A.diagnose(A.autopsy(_M, run, _LABELS), run) if _LABELS is not None else []
    return g, v, run.ret.astype(np.float32), tags


_LABELS = None


@dataclass
class SearchResult:
    champion: Genome
    champion_train: Verdict
    champion_val: Verdict
    champion_passed_validation: bool
    finalists: list                      # [(genome, train verdict, val verdict)]
    evaluated: int
    generations: int
    train_returns: np.ndarray            # [genome, day], every genome evaluated
    train_bench: np.ndarray
    train_cash: np.ndarray
    genomes: list = field(default_factory=list)
    history: list = field(default_factory=list)   # best train fitness per generation


def run_search(m: Market, train: tuple, val: tuple, seed: int = 7,
               pop0: int = 80, generations: int = 5, top: int = 10,
               processes: int = 4, log=print) -> SearchResult:
    global _LABELS
    rng = random.Random(seed)
    _LABELS = A.regime_labels(m)
    seen: dict[str, tuple] = {}

    def evaluate(batch):
        batch = [g for g in dict.fromkeys(batch) if g.key() not in seen]
        if not batch:
            return
        with mp.get_context("fork").Pool(processes, initializer=_init, initargs=(m, train)) as pool:
            for g, v, ret, tags in pool.map(_eval, batch, chunksize=4):
                seen[g.key()] = (g, v, ret, tags)

    first = [Genome()] + [random_genome(rng) for _ in range(pop0)]
    evaluate(first)
    history = []
    for gen in range(generations + 1):
        ranked = sorted(seen.values(), key=lambda x: -x[1].fitness)
        best = ranked[0][1]
        history.append({"generation": gen, "evaluated": len(seen), "best_fitness": best.fitness,
                        "best_passed_train": best.passed,
                        "passing_train": sum(1 for x in seen.values() if x[1].passed)})
        log(f"  gen {gen}: {len(seen)} evaluated, best train fitness {best.fitness:+.3f} "
            f"(passes gates: {best.passed}), {history[-1]['passing_train']} pass train gates")
        if gen == generations:
            break
        parents = ranked[:top]
        others = [p[0] for p in parents]
        brood = []
        for g, v, _, tags in parents:
            brood += children(g, tags, [o for o in others if o is not g], rng)
        evaluate(brood)

    ranked = sorted(seen.values(), key=lambda x: -x[1].fitness)
    finalists = []
    for g, tv, _, _ in ranked[:top]:
        vv = judge_run(backtest(m, g, *val))
        finalists.append((g, tv, vv))
    passing = [f for f in finalists if f[2].passed]
    pool_ = passing or finalists
    champ = max(pool_, key=lambda f: f[2].fitness)

    keys = list(seen)
    R = np.stack([seen[k][2] for k in keys])
    ref = backtest(m, Genome(), *train)
    return SearchResult(
        champion=champ[0], champion_train=champ[1], champion_val=champ[2],
        champion_passed_validation=bool(passing), finalists=finalists,
        evaluated=len(seen), generations=generations, train_returns=R,
        train_bench=ref.bench, train_cash=ref.cash, genomes=[seen[k][0] for k in keys],
        history=history,
    )
