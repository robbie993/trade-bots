"""The Atlas SPY-R genome: bounded parameters inside one strategy family.

The GA does not invent strategies. Every gene below has a range fixed before
any backtest ran, and the data definitions (which lookbacks exist, how the
panic inputs are scaled) are not genes at all. What the GA can change is how
much weight each documented ingredient gets and how hard the risk controls
bite.

Exposure can never exceed 100%: there is no gene for leverage.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, fields, replace

from ..signals.regime import COMPONENTS

# name: (kind, low, high) for floats/ints, (kind, choices) for categorical.
SPACE = {
    "mom_w21": ("float", 0.0, 0.40),
    "mom_w63": ("float", 0.0, 0.40),
    "mom_w126": ("float", 0.0, 0.50),
    "mom_w252": ("float", 0.0, 0.60),
    "skip_recent": ("choice", (0, 5)),
    "value_w": ("float", 0.0, 0.30),
    "lowvol_w": ("float", 0.0, 0.20),
    "trend_filter": ("choice", (0, 1, 2)),   # none / own momentum > 0 / > cash
    "top_n": ("int", 2, 6),
    "inverse_vol": ("choice", (0, 1)),
    "position_cap": ("float", 0.20, 1.00),
    "vol_lookback": ("choice", (21, 42, 63, 126)),
    "target_vol": ("float", 0.06, 0.20),
    "pw_drawdown": ("float", 0.0, 1.0),
    "pw_volatility": ("float", 0.0, 1.0),
    "pw_vol_accel": ("float", 0.0, 1.0),
    "pw_breadth": ("float", 0.0, 1.0),
    "pw_trend": ("float", 0.0, 1.0),
    "warn_threshold": ("float", 0.25, 0.60),
    "panic_threshold": ("float", 0.35, 0.90),
    "warn_exposure": ("float", 0.60, 0.80),
    "panic_exposure": ("float", 0.20, 0.50),
    "rebalance_days": ("choice", (5, 21)),
    "rebalance_threshold": ("float", 0.0, 0.10),
    "stop_rule": ("choice", (0.0, 0.10, 0.15, 0.20)),
    "panic_daily": ("choice", (0, 1)),
}

NUMERIC = [k for k, v in SPACE.items() if v[0] in ("float", "int")]


@dataclass(frozen=True)
class Genome:
    mom_w21: float = 0.10
    mom_w63: float = 0.20
    mom_w126: float = 0.30
    mom_w252: float = 0.40
    skip_recent: int = 5
    value_w: float = 0.10
    lowvol_w: float = 0.05
    trend_filter: int = 1
    top_n: int = 4
    inverse_vol: int = 1
    position_cap: float = 0.40
    vol_lookback: int = 63
    target_vol: float = 0.10
    # Robbie's starting weights: 0.25 / 0.25 / 0.20 / 0.20 / 0.10.
    pw_drawdown: float = 0.25
    pw_volatility: float = 0.25
    pw_vol_accel: float = 0.20
    pw_breadth: float = 0.20
    pw_trend: float = 0.10
    warn_threshold: float = 0.35
    panic_threshold: float = 0.60
    warn_exposure: float = 0.70
    panic_exposure: float = 0.35
    rebalance_days: int = 21
    rebalance_threshold: float = 0.02
    stop_rule: float = 0.0
    panic_daily: int = 1

    # -- derived -----------------------------------------------------------
    def mom_weights(self) -> dict:
        w = {21: self.mom_w21, 63: self.mom_w63, 126: self.mom_w126, 252: self.mom_w252}
        s = sum(w.values())
        return {k: (v / s if s > 1e-9 else 0.25) for k, v in w.items()}

    def panic_weights(self) -> list:
        w = [getattr(self, f"pw_{c}") for c in COMPONENTS]
        s = sum(w)
        return [x / s if s > 1e-9 else 1 / len(w) for x in w]

    def momentum_share(self) -> float:
        return max(0.0, 1.0 - self.value_w - self.lowvol_w)

    def key(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:10]

    def as_dict(self) -> dict:
        return asdict(self)


def repair(g: Genome) -> Genome:
    """Clip every gene into its range and keep panic above warn."""
    d = asdict(g)
    for k, spec in SPACE.items():
        if spec[0] == "float":
            d[k] = float(min(spec[2], max(spec[1], d[k])))
        elif spec[0] == "int":
            d[k] = int(round(min(spec[2], max(spec[1], d[k]))))
        elif d[k] not in spec[1]:
            d[k] = min(spec[1], key=lambda c: abs(c - d[k]))
    if d["panic_threshold"] < d["warn_threshold"] + 0.10:
        d["panic_threshold"] = min(0.90, d["warn_threshold"] + 0.10)
    if sum(d[f"mom_w{n}"] for n in (21, 63, 126, 252)) < 1e-6:
        d["mom_w252"] = 0.6
    return Genome(**d)


def random_genome(rng: random.Random) -> Genome:
    d = {}
    for k, spec in SPACE.items():
        if spec[0] == "float":
            d[k] = rng.uniform(spec[1], spec[2])
        elif spec[0] == "int":
            d[k] = rng.randint(spec[1], spec[2])
        else:
            d[k] = rng.choice(spec[1])
    return repair(Genome(**d))


def mutate(g: Genome, rng: random.Random, rate: float = 0.25, scale: float = 0.15) -> Genome:
    """Random small mutation: each gene moves with probability `rate`."""
    d = asdict(g)
    for k, spec in SPACE.items():
        if rng.random() > rate:
            continue
        if spec[0] == "float":
            d[k] += rng.gauss(0, scale * (spec[2] - spec[1]))
        elif spec[0] == "int":
            d[k] += rng.choice((-1, 1))
        else:
            d[k] = rng.choice(spec[1])
    return repair(Genome(**d))


def crossover(a: Genome, b: Genome, rng: random.Random) -> Genome:
    da, db = asdict(a), asdict(b)
    return repair(Genome(**{k: (da[k] if rng.random() < 0.5 else db[k]) for k in da}))


def nudge(g: Genome, **moves) -> Genome:
    """Targeted mutation: add each move to its gene (categoricals: set)."""
    d = asdict(g)
    for k, v in moves.items():
        if SPACE[k][0] == "choice":
            d[k] = v
        else:
            d[k] = d[k] + v
    return repair(Genome(**d))


def perturb(g: Genome, gene: str, frac: float) -> Genome:
    """Scale one numeric gene by (1 + frac), for the robustness gate."""
    d = asdict(g)
    d[gene] = d[gene] * (1 + frac) if d[gene] != 0 else (SPACE[gene][2] - SPACE[gene][1]) * abs(frac) * (1 if frac > 0 else 0)
    return repair(Genome(**d))


GENE_NAMES = [f.name for f in fields(Genome)]
