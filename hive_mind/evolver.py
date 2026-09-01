"""The genetic core — the genome, and the only code allowed to change it.

A genome is eleven numbers. That is the entire strategy: what counts as a VIX
spike, whether to fade a move or follow it, how much of the book one idea may
have, when to stop out. Everything the scouts and the council do is a reading
of these numbers against today's bar.

Keeping the strategy as *data* rather than as code is what makes the rest of
this possible. A genome can be fingerprinted, stored, compared, killed and
refused. A strategy spread across five files of branching cannot be any of
those things, and "which version lost the money?" becomes unanswerable.

The numbers are ``Decimal``, quantised to two places, for the same reason the
rest of the repository is: several of these genes multiply cash. A gene that
arrives as 0.1 and hashes as 0.10000000000000000555 is a gene whose
fingerprint depends on how it was constructed, and the fingerprint is the
whole licensing mechanism in ``lock.py``.

The mutation rules:

* **Seeded.** Same seed, same generation, same mutants, on any machine. An
  evolutionary system you cannot replay is a system you cannot audit. The
  float that ``random.uniform`` returns is rounded and turned into a Decimal
  before it touches a gene — the same move ``src/trading/brain/evolver.py``
  makes, for the same reason.
* **Clamped.** Every gene has a range, and a mutant outside it is repaired,
  not rejected — a genome that drifts to 40x leverage is not a bold strategy,
  it is a bug with a P&L.
* **Gated by gene.** ``mutate`` takes the set of genes it may touch. That
  parameter is what makes phase 4 of the walk-forward lock enforceable rather
  than aspirational: on real money the sizing genes may move and the strategy
  genes may not, and the restriction lives in the mutation function itself.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, fields, replace
from decimal import Decimal
from typing import Iterable, Optional

from src.money import D

STEP = D("0.01")

# name -> (low, high)
BOUNDS: dict = {
    "vix_spike": (D(18), D(45)),
    "vix_calm": (D(10), D(22)),
    "fear_threshold": (D("-1.6"), D("-0.2")),
    "greed_threshold": (D("0.2"), D("1.6")),
    "trend_bias": (D(0), D(1)),
    "momentum_window": (D(3), D(40)),
    "conviction_floor": (D("0.15"), D("0.85")),
    "risk_tolerance": (D("0.05"), D(1)),
    "leverage_cap": (D("0.25"), D(3)),
    "position_pct": (D("0.02"), D("0.50")),
    "stop_loss_pct": (D(2), D(25)),
}

# The two halves of the genome, and the reason the split exists at all.
#
# STRATEGY genes decide *what the system believes*: when a VIX print is a
# spike, whether a move is faded or followed, how long a trend has to be to
# count. SIZING genes decide *how much it bets on that belief*.
#
# On real money the first set is frozen. Letting a live book re-decide what it
# believes, from a fortnight of its own fills, is how a system arrives at a new
# strategy at the worst possible moment with no out-of-sample evidence at all.
# Sizing can move because being wrong about size is recoverable and being wrong
# about the thesis is not.
STRATEGY_GENES = frozenset(
    {
        "vix_spike",
        "vix_calm",
        "fear_threshold",
        "greed_threshold",
        "trend_bias",
        "momentum_window",
        "conviction_floor",
    }
)
SIZING_GENES = frozenset({"risk_tolerance", "leverage_cap", "position_pct", "stop_loss_pct"})
ALL_GENES = STRATEGY_GENES | SIZING_GENES
INTEGER_GENES = frozenset({"momentum_window"})


@dataclass(frozen=True)
class Genome:
    """The whole strategy, as eleven numbers."""

    vix_spike: Decimal = D(30)
    vix_calm: Decimal = D(16)
    fear_threshold: Decimal = D("-0.5")
    greed_threshold: Decimal = D("0.8")
    trend_bias: Decimal = D("0.5")  # 0 = always fade, 1 = always follow
    momentum_window: Decimal = D(10)
    conviction_floor: Decimal = D("0.45")  # below this the council does nothing
    risk_tolerance: Decimal = D("0.7")
    leverage_cap: Decimal = D("1.5")
    position_pct: Decimal = D("0.10")
    stop_loss_pct: Decimal = D(12)

    def __post_init__(self) -> None:
        for f in fields(self):
            object.__setattr__(self, f.name, D(getattr(self, f.name)))

    # -- identity ---------------------------------------------------------
    @property
    def window(self) -> int:
        """The momentum lookback, as the integer number of bars it is."""
        return int(self.momentum_window)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def fingerprint(self) -> str:
        """A stable name for one exact parameter set.

        The genes are already quantised, so the canonical form is just their
        strings — no rounding, no float round-trip, and a fingerprint that
        means "these numbers" rather than "this object".
        """
        canonical = {k: str(v) for k, v in sorted(self.to_dict().items())}
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def describe(self) -> str:
        return (
            f"vix_spike={self.vix_spike} trend_bias={self.trend_bias} "
            f"window={self.window} risk={self.risk_tolerance} "
            f"lev={self.leverage_cap} size={self.position_pct} "
            f"stop={self.stop_loss_pct}%"
        )

    def diff(self, other: "Genome") -> str:
        changes = [
            f"{name} {getattr(self, name)}->{getattr(other, name)}"
            for name in sorted(ALL_GENES)
            if getattr(self, name) != getattr(other, name)
        ]
        return ", ".join(changes) if changes else "(identical)"


def clamp(genome: Genome) -> Genome:
    """Repair a genome into its ranges, and into internal sense."""
    values = {}
    for name, (low, high) in BOUNDS.items():
        value = max(low, min(high, D(getattr(genome, name))))
        values[name] = D(int(value)) if name in INTEGER_GENES else value.quantize(STEP)
    # A "calm" threshold above the "spike" threshold is not a cautious
    # strategy, it is a strategy whose two states are unreachable.
    if values["vix_calm"] >= values["vix_spike"]:
        values["vix_calm"] = max(BOUNDS["vix_calm"][0], values["vix_spike"] - D(4))
    if values["fear_threshold"] >= values["greed_threshold"]:
        values["fear_threshold"] = -abs(values["greed_threshold"])
    return replace(genome, **values)


BASE_GENOME = clamp(Genome())


class Evolver:
    """Mutation and selection over genomes. Holds no opinion about money."""

    def __init__(self, seed: int = 20260901, mutation_rate: float = 0.35, scale: str = "0.18"):
        self.seed = int(seed)
        self.mutation_rate = float(mutation_rate)
        self.scale = D(scale)
        self.generation = 0
        self.lineage: list = []  # (generation, parent fingerprint, child fingerprint)

    def rng(self, generation: int, tag: str = "") -> random.Random:
        return random.Random(f"{self.seed}:{generation}:{tag}")

    def mutate(
        self,
        genome: Genome,
        rng: random.Random,
        genes: Optional[Iterable[str]] = None,
    ) -> Genome:
        """One mutant. ``genes`` is the *only* set this may touch."""
        allowed = set(genes) if genes is not None else set(ALL_GENES)
        unknown = allowed - ALL_GENES
        if unknown:
            raise ValueError(f"not genes: {', '.join(sorted(unknown))}")

        values = {}
        # sorted(), not the set's own order. Python randomises string hashing
        # per process, so iterating a set of gene names hands the same RNG
        # draws to different genes on every run — and the reproducibility
        # claim quietly stops being true, in a way that only shows up when two
        # runs of the same command print different numbers.
        for name in sorted(allowed):
            if rng.random() > self.mutation_rate:
                continue
            low, high = BOUNDS[name]
            draw = D(str(round(rng.uniform(-1.0, 1.0), 6)))
            values[name] = D(getattr(genome, name)) + draw * (high - low) * self.scale
        return clamp(replace(genome, **values)) if values else genome

    def population(
        self,
        incumbent: Genome,
        size: int = 8,
        generation: Optional[int] = None,
        genes: Optional[Iterable[str]] = None,
    ) -> list:
        """The incumbent first, then mutants of it.

        The incumbent is always in the population and always re-scored on the
        same data as the challengers. Carrying a fitness forward from a
        previous run compares two numbers that were never measured against the
        same bars, which is not a comparison.
        """
        generation = self.generation if generation is None else generation
        rng = self.rng(generation)
        out = [incumbent]
        for _ in range(max(0, size - 1)):
            out.append(self.mutate(incumbent, rng, genes=genes))
        return out

    def record(self, generation: int, parent: Genome, child: Genome) -> None:
        self.lineage.append((generation, parent.fingerprint(), child.fingerprint()))


def create_random_genome(seed: int = 0) -> Genome:
    """A genome drawn uniformly from the gene bounds.

    For asking what a *population* of strategies does on a dataset, rather
    than what one hand-chosen strategy does. Read the answer carefully: if a
    large share of random genomes clears a gauntlet, the likelier explanation
    is that the gauntlet is too easy, not that the architecture has an edge.
    ``crucible_real.py`` reports the null controls next to the survival rate
    for exactly that reason.
    """
    rng = random.Random(f"random-genome:{seed}")
    values = {
        name: D(str(round(rng.uniform(float(low), float(high)), 6)))
        for name, (low, high) in BOUNDS.items()
    }
    return clamp(Genome(**values))


__all__ = [
    "ALL_GENES",
    "BASE_GENOME",
    "BOUNDS",
    "Evolver",
    "Genome",
    "SIZING_GENES",
    "create_random_genome",
    "STRATEGY_GENES",
    "clamp",
]
