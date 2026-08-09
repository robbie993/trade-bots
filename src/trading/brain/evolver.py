"""EvoMap — evolution over strategy genomes.

A genome is a small dict of the numbers the analysts read: window lengths, the
trend-versus-reversion bias, the band that counts as fairly valued. The
evolver mutates them, backtests each variant on the *same* data, and keeps
the fittest.

Three properties this implementation insists on:

* **Deterministic.** The mutation RNG is seeded from ``brain.seed`` and the
  generation number. The same population, the same data and the same seed
  produce the same survivors on any machine. An evolutionary system you
  cannot replay is a system you cannot audit.
* **Evaluated on identical data.** Every candidate in a generation is run over
  the same bars. Otherwise the winner is whoever got the luckier sample.
* **Promotion is earned, not assumed.** A mutant replaces the incumbent only
  if it beats the incumbent's fitness *on that same data*. The incumbent is
  always re-scored rather than trusting a fitness from a previous run.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Sequence

from ...db.connection import utcnow_iso
from ...money import D, ZERO
from ..backtest import Backtester, BacktestResult
from ..config import BrainConfig, TradingConfig
from ..data.market_data import MarketData
from ..models import FirmRecord
from ..store import TradingStore

# name -> (low, high, integer?)
GENES: dict = {
    "fast_window": (D(3), D(30), True),
    "slow_window": (D(20), D(120), True),
    "rsi_window": (D(5), D(30), True),
    "trend_bias": (ZERO, D(100), False),
    "value_window": (D(30), D(150), True),
    "fair_band_pct": (D(2), D(25), False),
    "calm_vol_pct": (D(10), D(90), False),
}

BASE_GENOME: dict = {
    "fast_window": 10,
    "slow_window": 30,
    "rsi_window": 14,
    "trend_bias": 60,
    "value_window": 90,
    "fair_band_pct": 8,
    "calm_vol_pct": 35,
}


@dataclass
class Candidate:
    genome: dict
    parent: Optional[dict] = None
    result: Optional[BacktestResult] = None
    fitness: Decimal = ZERO
    is_incumbent: bool = False

    def __str__(self) -> str:
        tag = " (incumbent)" if self.is_incumbent else ""
        return f"fitness {self.fitness}{tag}: {json.dumps(self.genome, sort_keys=True)}"


@dataclass
class Generation:
    number: int
    candidates: list = field(default_factory=list)
    winner: Optional[Candidate] = None
    promoted: bool = False

    def summary(self) -> str:
        lines = [f"generation {self.number}: {len(self.candidates)} candidates"]
        for candidate in sorted(self.candidates, key=lambda c: -c.fitness)[:5]:
            lines.append(f"  {candidate}")
        if self.winner:
            lines.append(
                f"  winner: fitness {self.winner.fitness}"
                + (" — promoted" if self.promoted else " — not promoted (incumbent held)")
            )
        return "\n".join(lines)


class Evolver:
    def __init__(
        self,
        store: TradingStore,
        config: Optional[TradingConfig] = None,
    ):
        self.store = store
        self.config = config or TradingConfig()
        self.backtester = Backtester(self.config)

    @property
    def brain(self) -> BrainConfig:
        return self.config.brain

    # -- genome operations -------------------------------------------------
    def normalise(self, genome: dict) -> dict:
        """Clamp every gene into range and keep the windows ordered.

        ``fast_window >= slow_window`` is not a strategy, it is a bug that
        would silently invert every trend reading, so it is repaired here
        rather than left for an analyst to trip over.
        """
        out = dict(BASE_GENOME)
        out.update({k: v for k, v in (genome or {}).items() if k in GENES})
        for name, (low, high, is_int) in GENES.items():
            value = max(low, min(high, D(out.get(name, BASE_GENOME[name]))))
            out[name] = int(value) if is_int else str(value.quantize(D("0.01")))
        if int(D(out["fast_window"])) >= int(D(out["slow_window"])):
            out["fast_window"] = max(3, int(D(out["slow_window"])) // 3)
        return out

    def mutate(self, genome: dict, rng: random.Random) -> dict:
        mutant = dict(genome)
        rate = float(self.brain.mutation_rate)
        scale = D(self.brain.mutation_scale)
        for name, (low, high, is_int) in GENES.items():
            if rng.random() > rate:
                continue
            span = (high - low) * scale
            step = D(str(round(rng.uniform(-1, 1), 6))) * span
            value = D(mutant.get(name, BASE_GENOME[name])) + step
            mutant[name] = int(value) if is_int else str(value.quantize(D("0.01")))
        return self.normalise(mutant)

    def population(self, incumbent: dict, generation: int) -> list:
        rng = random.Random(f"{self.brain.seed}:{generation}")
        base = self.normalise(incumbent)
        out = [Candidate(genome=base, is_incumbent=True)]
        for _ in range(max(1, self.brain.population - 1)):
            out.append(Candidate(genome=self.mutate(base, rng), parent=base))
        return out

    # -- evolution ---------------------------------------------------------
    def evolve(
        self,
        firm: FirmRecord,
        market: MarketData,
        generation: int = 1,
        analysts: Sequence[str] = ("technical", "sentiment", "macro"),
    ) -> Generation:
        """Run one generation for one firm. Writes genomes; promotes at most one."""
        candidates = self.population(firm.genome or BASE_GENOME, generation)
        for candidate in candidates:
            # A fresh cursor per candidate: every genome sees identical bars.
            data = MarketData(market.feed, firm.universe or market.symbols)
            candidate.result = self.backtester.run(
                firm_key=firm.firm_key,
                symbols=firm.universe or market.symbols,
                market=data,
                genome=candidate.genome,
                analysts=analysts,
                capital=firm.initial_allocation or self.config.firm.allocation,
                risk_limit=firm.risk_limit,
            )
            candidate.fitness = candidate.result.fitness

        gen = Generation(number=generation, candidates=candidates)
        incumbent = next(c for c in candidates if c.is_incumbent)
        best = max(candidates, key=lambda c: c.fitness)
        gen.winner = best

        # The incumbent goes in first so the mutants can point at it. Every
        # mutant in a generation *is* a mutation of that one genome, and
        # recording which one turns `strategy_genomes` from a list into a
        # lineage — without it `parent_id` sits null and the descent of a
        # strategy is unrecoverable after the fact.
        parent_id = self.store.db.insert(
            "strategy_genomes",
            _genome_row(firm, generation, incumbent, best, incumbent),
        )
        for candidate in candidates:
            if candidate is incumbent:
                continue
            self.store.db.insert(
                "strategy_genomes",
                {**_genome_row(firm, generation, candidate, best, incumbent),
                 "parent_id": parent_id},
            )

        if (
            self.brain.promote_winners
            and best is not incumbent
            and best.fitness > incumbent.fitness
        ):
            self.store.update_firm_fields(firm.id, genome=json.dumps(best.genome, sort_keys=True))
            self.store.record_event(
                "evolution",
                f"{firm.firm_key}: genome promoted at generation {generation} "
                f"(fitness {incumbent.fitness} -> {best.fitness})",
                firm_id=firm.id,
                payload={"from": incumbent.genome, "to": best.genome},
            )
            gen.promoted = True
        return gen

    def history(self, firm_id: int, limit: int = 20) -> list:
        return self.store.db.query(
            "SELECT * FROM strategy_genomes WHERE firm_id = ? ORDER BY id DESC LIMIT ?",
            (firm_id, limit),
        )


def _genome_row(firm, generation: int, candidate, best, incumbent) -> dict:
    return {
        "firm_id": firm.id,
        "generation": generation,
        "genome": json.dumps(candidate.genome, sort_keys=True),
        "fitness": candidate.fitness,
        "trades": candidate.result.closed_trades if candidate.result else 0,
        "selected": candidate is best and best.fitness > incumbent.fitness,
        "notes": "incumbent" if candidate.is_incumbent else "mutant",
        "created_at": utcnow_iso(),
    }


__all__ = ["BASE_GENOME", "Candidate", "Evolver", "GENES", "Generation"]
