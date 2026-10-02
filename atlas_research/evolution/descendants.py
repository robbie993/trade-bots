"""Top 10 -> 10 descendants each, aimed at where each parent failed.

For every parent, 6 of its 10 children are targeted: each failure tag from
its autopsy maps to a few directed moves (with a little noise, so siblings
differ), and they are applied in order of the tags. The other 4 children
are ordinary small mutations or a crossover with another top-10 parent, so
the search does not tunnel into one idea.
"""
from __future__ import annotations

import random

from .genome import Genome, crossover, mutate, nudge

FIXES = {
    # stayed invested into the drawdown: react faster and cut harder
    "crash_drawdown": [
        dict(vol_lookback=21), dict(panic_exposure=-0.10), dict(panic_threshold=-0.10),
        dict(warn_threshold=-0.07), dict(target_vol=-0.02), dict(panic_daily=1),
        dict(pw_vol_accel=+0.2), dict(trend_filter=2),
    ],
    # came back too late after a low: let it re-risk sooner
    "missed_rebound": [
        dict(panic_exposure=+0.08), dict(panic_threshold=+0.08), dict(rebalance_days=5),
        dict(mom_w21=+0.1), dict(mom_w63=+0.1), dict(pw_drawdown=-0.2), dict(pw_trend=+0.2),
    ],
    # behind SPY when markets rose: carry more risk, concentrate
    "bull_lag": [
        dict(target_vol=+0.03), dict(top_n=-1), dict(position_cap=+0.15),
        dict(lowvol_w=-0.07), dict(warn_threshold=+0.07), dict(inverse_vol=0),
    ],
    "too_defensive": [
        dict(target_vol=+0.03), dict(warn_exposure=+0.07), dict(panic_exposure=+0.08),
        dict(position_cap=+0.15), dict(trend_filter=1), dict(warn_threshold=+0.07),
    ],
    # paying for churn: trade less
    "whipsaw": [
        dict(rebalance_days=21), dict(rebalance_threshold=+0.03), dict(stop_rule=0.0),
        dict(mom_w21=-0.1), dict(panic_daily=0),
    ],
    "return_shortfall": [
        dict(target_vol=+0.03), dict(top_n=-1), dict(mom_w126=+0.1), dict(mom_w252=+0.1),
        dict(value_w=-0.05),
    ],
}


def _jitter(moves: dict, rng: random.Random) -> dict:
    return {k: (v * rng.uniform(0.5, 1.5) if isinstance(v, float) and k not in
                ("stop_rule",) else v) for k, v in moves.items()}


def children(parent: Genome, tags: list, others: list, rng: random.Random,
             n: int = 10, targeted: int = 6) -> list:
    kids = []
    menu = [mv for t in tags for mv in FIXES.get(t, [])]
    for i in range(targeted):
        if not menu:
            break
        # one to three moves from the menu, earlier (worse) tags first
        k = rng.randint(1, 3)
        picks = [menu[(i + j) % len(menu)] for j in range(k)]
        g = parent
        for mv in picks:
            g = nudge(g, **_jitter(mv, rng))
        kids.append(g)
    while len(kids) < n:
        if others and rng.random() < 0.3:
            kids.append(mutate(crossover(parent, rng.choice(others), rng), rng, rate=0.1))
        else:
            kids.append(mutate(parent, rng))
    return kids
