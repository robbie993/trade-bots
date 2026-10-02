"""Hard gates first, then a score. Never Sharpe alone.

A genome is judged against SPY over exactly the same days. Before any score
is computed it must clear every hard gate:

    CAGR      > SPY CAGR
    max DD    < SPY max DD (shallower)
    trades    >= 4 a year            (an idle book is not a strategy)
    exposure  >= 50% on average      (the "idler" gate: cash cannot win)
    worst 12m >= -25%

Then the score, Robbie's weights:

    0.35 excess return + 0.25 drawdown + 0.15 Sharpe + 0.15 Calmar
    + 0.10 consistency (share of rolling 12-month windows beating SPY)

each term a bounded tanh of the gap to SPY, so no single number can run
away with it. During the search a genome that fails gates still gets a
fitness (its score minus 2 per failed gate) so evolution can climb towards
passing, but it can never be called a pass.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..validation import metrics as M

MIN_TRADES_PER_YEAR = 4
MIN_MEAN_EXPOSURE = 0.50
MIN_WORST_12M = -0.25
SCORE_WEIGHTS = {"excess": 0.35, "drawdown": 0.25, "sharpe": 0.15, "calmar": 0.15,
                 "consistency": 0.10}


@dataclass
class Verdict:
    stats: M.Stats
    spy: M.Stats
    gates: dict
    score: float
    fitness: float
    mean_exposure: float
    trades_per_year: float
    turnover_per_year: float
    beat_12m_share: float
    extras: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(self.gates.values())

    @property
    def beats_return(self) -> bool:
        return self.gates["cagr_above_spy"]

    @property
    def beats_drawdown(self) -> bool:
        return self.gates["drawdown_below_spy"]

    def row(self) -> dict:
        return {
            "cagr": self.stats.cagr, "spy_cagr": self.spy.cagr,
            "max_dd": self.stats.max_dd, "spy_max_dd": self.spy.max_dd,
            "sharpe": self.stats.sharpe, "spy_sharpe": self.spy.sharpe,
            "calmar": self.stats.calmar, "spy_calmar": self.spy.calmar,
            "sortino": self.stats.sortino, "worst_12m": self.stats.worst_12m,
            "exposure": self.mean_exposure, "trades_per_year": self.trades_per_year,
            "turnover_per_year": self.turnover_per_year,
            "beat_spy_12m_share": self.beat_12m_share,
            "score": self.score, "fitness": self.fitness, "passed": self.passed,
            "failed_gates": [k for k, v in self.gates.items() if not v],
        }


def judge(ret: np.ndarray, bench: np.ndarray, cash: np.ndarray, exposure: np.ndarray,
          trades: int, turnover: float) -> Verdict:
    s = M.stats(ret, cash)
    b = M.stats(bench, cash)
    years = max(len(ret) / M.TD, 1e-9)
    expo = float(exposure.mean()) if len(exposure) else 0.0
    tpy = trades / years
    beat = M.rolling_beat_share(ret, bench, M.TD)
    gates = {
        "cagr_above_spy": s.cagr > b.cagr,
        "drawdown_below_spy": s.max_dd > b.max_dd,
        "enough_trades": tpy >= MIN_TRADES_PER_YEAR,
        "enough_exposure": expo >= MIN_MEAN_EXPOSURE,
        "worst_12m_ok": s.worst_12m >= MIN_WORST_12M,
    }
    cons = 0.0 if math.isnan(beat) else (beat - 0.5) * 2
    parts = {
        "excess": math.tanh((s.cagr - b.cagr) / 0.05),
        "drawdown": math.tanh((abs(b.max_dd) - abs(s.max_dd)) / 0.15),
        "sharpe": math.tanh((s.sharpe - b.sharpe) / 0.5),
        "calmar": math.tanh((s.calmar - b.calmar) / 0.3),
        "consistency": cons,
    }
    score = sum(SCORE_WEIGHTS[k] * v for k, v in parts.items())
    failed = sum(1 for v in gates.values() if not v)
    return Verdict(stats=s, spy=b, gates=gates, score=score, fitness=score - 2 * failed,
                   mean_exposure=expo, trades_per_year=tpy, turnover_per_year=turnover / years,
                   beat_12m_share=beat, extras={"score_parts": parts})


def judge_run(run) -> Verdict:
    return judge(run.ret, run.bench, run.cash, run.exposure, run.trades, run.turnover)


def classify(validation: Verdict, insample: Verdict, perturb_pass: float,
             cost_pass: bool, walk_forward_pass: bool, oos: Verdict | None) -> str:
    """Robbie's ladder: failure / interesting / candidate / strong / production."""
    both = validation.beats_return and validation.beats_drawdown
    if not (validation.beats_return or validation.beats_drawdown):
        return "RESEARCH FAILURE"
    if not both:
        return "INTERESTING (one dimension only)"
    if not (insample.beats_return and insample.beats_drawdown and perturb_pass >= 0.70):
        return "INTERESTING (beats SPY in validation but not robustly)"
    if not (cost_pass and walk_forward_pass):
        return "CANDIDATE"
    if oos is None:
        return "STRONG CANDIDATE (frozen OOS not yet run)"
    if oos.beats_return and oos.beats_drawdown and oos.stats.sharpe >= oos.spy.sharpe \
            and oos.stats.calmar >= oos.spy.calmar:
        return "PRODUCTION CANDIDATE"
    return "STRONG CANDIDATE (failed frozen OOS)"
