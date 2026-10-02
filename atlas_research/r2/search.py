"""The R2 genetic search: one family at a time, never across families.

Generation 0 is 60 random genomes plus the family default. Each of the next
4 generations takes the top 10 by train fitness and gives each 10 children:
6 aimed at what its verdict says went wrong, 4 ordinary mutations or
crossovers. The champion is the best of the final top 10 on VALIDATE.
"""
from __future__ import annotations

import multiprocessing as mp
import random

import numpy as np

from .engine import backtest
from .families import DEFAULTS, SPACES
from .fitness import judge

FIXES = {
    # trails SPY when it rises: carry more risk while the trend is healthy
    "bull_lag": [dict(exp_normal=+0.08), dict(gross_normal=+0.08), dict(spy_max=+0.08),
                 dict(exp_weak=+0.1), dict(top_n=-1), dict(sma=250), dict(spy_min=+0.1),
                 dict(target_vol=+0.03), dict(overlay_w=+0.08), dict(core_w=+0.05)],
    # loses as much as SPY when it falls: cut harder, react sooner
    "crash_drawdown": [dict(exp_bear=-0.1), dict(exp_crash=-0.1), dict(sma=100),
                       dict(rebalance_days=5), dict(spy_min=-0.1), dict(target_vol=-0.02),
                       dict(fallback=2), dict(below_frac=0.0), dict(core_w=-0.05)],
    "whipsaw": [dict(band=+0.02), dict(rebalance_days=21), dict(sma=250), dict(mom_w21=-0.1)],
    "return_shortfall": [dict(mom_w126=+0.1), dict(mom_w252=+0.1), dict(top_n=-1),
                         dict(overlay_w=+0.08), dict(skip_recent=5)],
}


def tags_of(v, run) -> list:
    tags = []
    if v.up_capture < 1.0:
        tags.append("bull_lag")
    if v.yearly_dd_gain < 0 or v.down_capture > 0.8:
        tags.append("crash_drawdown")
    if run.costs_paid / (len(run.ret) / 252) > 0.004:
        tags.append("whipsaw")
    if v.yearly_excess < 0 and not tags:
        tags.append("return_shortfall")
    return tags


def repair(fam: str, g: dict) -> dict:
    out = {}
    for k, spec in SPACES[fam].items():
        x = g[k]
        if spec[0] == "float":
            x = float(min(spec[2], max(spec[1], x)))
        elif spec[0] == "int":
            x = int(round(min(spec[2], max(spec[1], x))))
        elif x not in spec[1]:
            x = min(spec[1], key=lambda c: abs(c - x))
        out[k] = x
    if all(out[f"mom_w{n}"] < 1e-6 for n in (21, 63, 126, 252)):
        out["mom_w252"] = 0.6
    if fam == "B":   # keep the ladder ordered: normal >= weak >= bear >= crash
        out["exp_weak"] = min(out["exp_weak"], out["exp_normal"])
        out["exp_bear"] = min(out["exp_bear"], out["exp_weak"])
        out["exp_crash"] = min(out["exp_crash"], out["exp_bear"])
    return out


def key(g: dict) -> tuple:
    return tuple(sorted((k, round(v, 6) if isinstance(v, float) else v) for k, v in g.items()))


def rand(fam, rng):
    g = {}
    for k, spec in SPACES[fam].items():
        g[k] = (rng.uniform(spec[1], spec[2]) if spec[0] == "float" else
                rng.randint(spec[1], spec[2]) if spec[0] == "int" else rng.choice(spec[1]))
    return repair(fam, g)


def mutate(fam, g, rng, rate=0.25, scale=0.15):
    g = dict(g)
    for k, spec in SPACES[fam].items():
        if rng.random() > rate:
            continue
        if spec[0] == "float":
            g[k] += rng.gauss(0, scale * (spec[2] - spec[1]))
        elif spec[0] == "int":
            g[k] += rng.choice((-1, 1))
        else:
            g[k] = rng.choice(spec[1])
    return repair(fam, g)


def nudge(fam, g, moves, rng):
    g = dict(g)
    for k, v in moves.items():
        if k not in SPACES[fam]:
            continue
        if SPACES[fam][k][0] == "choice":
            g[k] = v
        else:
            g[k] += v * (rng.uniform(0.5, 1.5) if isinstance(v, float) else 1)
    return repair(fam, g)


def children(fam, g, tags, others, rng, n=10, targeted=6):
    menu = [mv for t in tags for mv in FIXES[t] if any(k in SPACES[fam] for k in mv)]
    kids = []
    for i in range(targeted if menu else 0):
        c = g
        for j in range(rng.randint(1, 3)):
            c = nudge(fam, c, menu[(i + j) % len(menu)], rng)
        kids.append(c)
    while len(kids) < n:
        if others and rng.random() < 0.3:
            o = rng.choice(others)
            c = repair(fam, {k: (g[k] if rng.random() < 0.5 else o[k]) for k in g})
            kids.append(mutate(fam, c, rng, rate=0.1))
        else:
            kids.append(mutate(fam, g, rng))
    return kids


_CTX = {}


def _eval(args):
    fam, g = args
    run = backtest(_CTX["rm"], fam, g, *_CTX["span"])
    v = judge(run)
    return g, v, tags_of(v, run), run.ret.astype(np.float32)


def search(rm, fam, train, val, seed=11, pop0=60, generations=4, top=10, processes=4, log=print):
    rng = random.Random(seed)
    seen = {}

    def evaluate(batch):
        batch = [g for g in batch if key(g) not in seen]
        uniq = {key(g): g for g in batch}
        if not uniq:
            return
        _CTX.update(rm=rm, span=train)
        with mp.get_context("fork").Pool(processes) as pool:
            for g, v, tags, ret in pool.map(_eval, [(fam, g) for g in uniq.values()], chunksize=4):
                seen[key(g)] = (g, v, tags, ret)

    evaluate([repair(fam, DEFAULTS[fam])] + [rand(fam, rng) for _ in range(pop0)])
    for gen in range(generations):
        ranked = sorted(seen.values(), key=lambda x: -x[1].fitness)[:top]
        others = [p[0] for p in ranked]
        brood = []
        for g, v, tags, _ in ranked:
            brood += children(fam, g, tags, [o for o in others if o is not g], rng)
        evaluate(brood)
    ranked = sorted(seen.values(), key=lambda x: -x[1].fitness)[:top]
    finalists = [(g, tv, judge(backtest(rm, fam, g, *val))) for g, tv, _, _ in ranked]
    champ = max(finalists, key=lambda f: f[2].fitness)
    log(f"    {fam}: {len(seen)} genomes, best train fitness {ranked[0][1].fitness:+.3f}, "
        f"champion validation fitness {champ[2].fitness:+.3f}")
    return champ, len(seen)
