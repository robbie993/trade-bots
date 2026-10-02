"""Gauntlet gates 5-9: try to break the champion.

5  Parameter robustness: every numeric gene moved -20%, -10%, +10%, +20%
   (integers by one step). Reports the share that still beat SPY on both
   CAGR and drawdown.
6  Universe robustness: drop each ETF in turn; drop SPY and QQQ together.
   (Large-, mid-cap and stock universes need the stock data that does not
   exist yet; see data/fundamentals.py.)
7  Costs at 2x and 3x the spread model.
8  Slippage at 2x and 3x.
9  Regimes: the autopsy's per-regime table.
"""
from __future__ import annotations

import numpy as np

from ..evolution.gauntlet import judge_run
from ..evolution.genome import NUMERIC, SPACE, Genome, perturb, repair
from ..portfolio.engine import Market, backtest


def parameter_perturbation(m: Market, g: Genome, start, end) -> dict:
    rows = []
    for gene in NUMERIC:
        if SPACE[gene][0] == "int":
            moves = [("-1", repair(Genome(**{**g.as_dict(), gene: getattr(g, gene) - 1}))),
                     ("+1", repair(Genome(**{**g.as_dict(), gene: getattr(g, gene) + 1})))]
        else:
            moves = [(f"{f:+.0%}", perturb(g, gene, f)) for f in (-0.2, -0.1, 0.1, 0.2)]
        for label, gg in moves:
            if gg == g:
                continue
            v = judge_run(backtest(m, gg, start, end))
            rows.append({"gene": gene, "move": label, "cagr": v.stats.cagr,
                         "max_dd": v.stats.max_dd, "beats_both": v.beats_return and v.beats_drawdown,
                         "passes_all_gates": v.passed})
    share = float(np.mean([r["beats_both"] for r in rows])) if rows else float("nan")
    worst = min(rows, key=lambda r: r["cagr"]) if rows else None
    return {"variants": len(rows), "share_beating_spy_both": share,
            "share_passing_all_gates": float(np.mean([r["passes_all_gates"] for r in rows])) if rows else float("nan"),
            "worst_variant": worst, "rows": rows}


def universe_robustness(m: Market, g: Genome, start, end) -> dict:
    out = {}
    base = np.ones(len(m.symbols), dtype=bool)
    for i, sym in enumerate(m.symbols):
        u = base.copy()
        u[i] = False
        v = judge_run(backtest(m, g, start, end, universe=u))
        out[f"without_{sym}"] = {"cagr": v.stats.cagr, "max_dd": v.stats.max_dd,
                                 "beats_both": v.beats_return and v.beats_drawdown}
    u = base.copy()
    u[m.symbols.index("SPY")] = False
    u[m.symbols.index("QQQ")] = False
    v = judge_run(backtest(m, g, start, end, universe=u))
    out["without_SPY_and_QQQ"] = {"cagr": v.stats.cagr, "max_dd": v.stats.max_dd,
                                  "beats_both": v.beats_return and v.beats_drawdown}
    share = float(np.mean([x["beats_both"] for x in out.values()]))
    return {"share_beating_spy_both": share, "variants": out}


def cost_stress(m: Market, g: Genome, start, end) -> dict:
    out = {}
    for name, kw in {"base": {}, "costs_2x": {"spread_mult": 2}, "costs_3x": {"spread_mult": 3},
                     "slippage_2x": {"slippage_mult": 2}, "slippage_3x": {"slippage_mult": 3},
                     "both_3x": {"spread_mult": 3, "slippage_mult": 3}}.items():
        run = backtest(m, g, start, end, **kw)
        v = judge_run(run)
        out[name] = {"cagr": v.stats.cagr, "max_dd": v.stats.max_dd, "sharpe": v.stats.sharpe,
                     "costs_paid_per_year": run.costs_paid / (len(run.ret) / 252),
                     "beats_both": v.beats_return and v.beats_drawdown}
    return out
