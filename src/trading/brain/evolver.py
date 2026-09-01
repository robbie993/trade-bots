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
    # How far a position may fall from what it cost before it is closed
    # regardless of what the analysts think. Zero switches it off, which is
    # what every firm did before this gene existed.
    "stop_loss_pct": (ZERO, D(25), False),
    # How much of a stranger's opinion this firm wants, as a percentage of the
    # published confidence. `SignalAnalyst` has always read this and its
    # docstring has always called it "a gene rather than a constant, because
    # trusting an imported screener is a choice the evolver is allowed to turn
    # down" — but it was never in this table, so the evolver could not turn it
    # down, or up, or anything. Every firm sat on the default of 100 and took
    # every scanner entirely at its word, permanently.
    #
    # It belongs here because a source's reliability is not knowable in
    # advance and is not the same for every firm: a Reddit sentiment feed may
    # be worth listening to on memecoins and worthless on Treasuries, and no
    # amount of arguing settles that as cheaply as letting each firm discover
    # it on held-out bars. 100 remains the starting point, so nothing changes
    # until evolution has evidence to change it with.
    "signal_trust": (ZERO, D(150), False),
    # One trust gene per source seat. They were a single gene until the news
    # desk arrived and made the problem obvious: a headline, a moving average
    # and a bankruptcy postmortem are not the same kind of evidence, and a
    # desk forced to weight them identically cannot express what it has
    # learned. Separate genes let the bonds desk mute news while keeping its
    # screener, and let the memecoin desk do the opposite, without either
    # decision being made by hand.
    "news_trust": (ZERO, D(150), False),
    "scribe_trust": (ZERO, D(150), False),
    # The shadow options desk. Its first version hardcoded every one of these
    # as a module constant, so the evolver had nothing to move and the desk
    # could have run for a year without changing a thing — which is exactly
    # what "it learned nothing" meant when it was asked.
    #
    # Ranges are bounded by what the evidence supports rather than by what is
    # expressible. Writing closer than half a standard deviation is a different
    # strategy from the one the account's 21 contracts describe, and a spread
    # cap above 30% admits contracts the 2026-07-31 study already showed cannot
    # be traded profitably at any signal strength.
    "shadow_dte_min": (D(7), D(30), True),
    "shadow_dte_max": (D(21), D(60), True),
    "shadow_strike_sd": (D("0.5"), D("2.5"), False),
    "shadow_spread_cap": (D(2), D(30), False),
    "shadow_confidence": (ZERO, D(80), False),
}

BASE_GENOME: dict = {
    "fast_window": 10,
    "slow_window": 30,
    "rsi_window": 14,
    "trend_bias": 60,
    "value_window": 90,
    "fair_band_pct": 8,
    "calm_vol_pct": 35,
    # Off by default, and that is a measured decision rather than caution.
    # A stop looked like the obvious fix for an average loss eight times the
    # average win, so it was A/B'd over 120 bars before shipping: at 8% the
    # village made $9,671 with a 1.07 loss/win ratio, and with the stop off it
    # made $16,587 at 0.84. The stop was converting recoverable drawdowns into
    # realised losses faster than it was cutting real ones.
    #
    # So it is a gene at zero. Evolution can find the level that suits a
    # particular firm on a particular market, which is the whole reason it is
    # a gene and not a constant — and one number picked by hand for nine very
    # different desks was never going to be right for any of them.
    "stop_loss_pct": 0,
    # Full trust to start, which is what every firm has been running on since
    # the signals seat existed. Not because full trust is right — because
    # changing what firms currently do and letting them evolve away from it
    # are two different experiments, and only the second one is this one.
    "signal_trust": 100,
    "news_trust": 100,
    "scribe_trust": 100,
    # Starting points from what the account actually did: its written
    # contracts cluster around a month out, and option_bot.py settled on
    # one realised SD below spot with no filter on top, having measured
    # that every filter it tried made results worse.
    "shadow_dte_min": 21,
    "shadow_dte_max": 45,
    "shadow_strike_sd": 1.0,
    "shadow_spread_cap": 15.0,
    "shadow_confidence": 20.0,
}


@dataclass
class Candidate:
    genome: dict
    parent: Optional[dict] = None
    result: Optional[BacktestResult] = None
    fitness: Decimal = ZERO
    is_incumbent: bool = False
    #: Fitness on the held-out tail — bars this candidate was not chosen on.
    #: `None` means the holdout was too short to say anything, which is a
    #: refusal to promote rather than a licence to.
    holdout_fitness: Optional[Decimal] = None

    def __str__(self) -> str:
        tag = " (incumbent)" if self.is_incumbent else ""
        held = ("" if self.holdout_fitness is None
                else f" / held-out {self.holdout_fitness}")
        return (f"fitness {self.fitness}{held}{tag}: "
                f"{json.dumps(self.genome, sort_keys=True)}")


@dataclass
class Generation:
    number: int
    candidates: list = field(default_factory=list)
    winner: Optional[Candidate] = None
    promoted: bool = False
    #: Why the winner was not adopted, when it was not. Empty on a promotion.
    refused: str = ""

    def summary(self) -> str:
        lines = [f"generation {self.number}: {len(self.candidates)} candidates"]
        for candidate in sorted(self.candidates, key=lambda c: -c.fitness)[:5]:
            lines.append(f"  {candidate}")
        if self.winner:
            lines.append(
                f"  winner: fitness {self.winner.fitness}"
                + (" — promoted" if self.promoted
                   else f" — not promoted ({self.refused or 'incumbent held'})")
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

    def _split(self, market: MarketData, symbols) -> tuple:
        """Where the fitted history ends and the held-out tail begins.

        Returns ``(split_index, holdout_bars)``. A split of zero means there
        is not enough history to divide at all, in which case everything is
        fitted and nothing can be adopted — which is the correct answer for a
        village that has been running for an afternoon.
        """
        data = MarketData(market.feed, symbols)
        data.register(list(symbols))
        total = data.length()
        fraction = D(self.brain.holdout_fraction)
        if total < 2 or fraction <= 0 or fraction >= 1:
            return 0, 0
        holdout = int(D(total) * fraction)
        split = total - holdout
        if split <= 0:
            return 0, 0
        return split, holdout

    # -- evolution ---------------------------------------------------------
    def evolve(
        self,
        firm: FirmRecord,
        market: MarketData,
        generation: int = 1,
        analysts: Sequence[str] = ("technical", "sentiment", "macro"),
    ) -> Generation:
        """Run one generation for one firm. Writes genomes; promotes at most one.

        **Chosen on one half of history, adopted on the other.** Mutants are
        scored over the early bars and the best is taken; that winner and the
        incumbent are then re-run over a held-out tail neither was selected
        against, and the genome only changes if it wins *there* too.

        Without that split this loop is an overfitting machine: it searches a
        seven-dimensional space for whatever curve best fits bars it has
        already seen, promotes it, and reports the fit as progress. The result
        looks like learning and is closer to memorising a past that is not
        coming back. The held-out tail is the only thing here that can tell
        the difference.

        If the tail is too short to say anything, the answer is the village's
        usual one — *insufficient data* — and the incumbent stands.
        """
        candidates = self.population(firm.genome or BASE_GENOME, generation)
        symbols = firm.universe or market.symbols
        capital = firm.initial_allocation or self.config.firm.allocation

        split, holdout_bars = self._split(market, symbols)

        def score(genome, start=None, steps=None):
            # A fresh cursor per run: every genome sees identical bars.
            data = MarketData(market.feed, symbols)
            return self.backtester.run(
                firm_key=firm.firm_key,
                symbols=symbols,
                market=data,
                genome=genome,
                analysts=analysts,
                capital=capital,
                risk_limit=firm.risk_limit,
                start=start,
                steps=steps,
            )

        for candidate in candidates:
            # Fitted on the early bars only, so the tail stays unseen.
            candidate.result = score(candidate.genome, steps=split or None)
            candidate.fitness = candidate.result.fitness

        gen = Generation(number=generation, candidates=candidates)
        incumbent = next(c for c in candidates if c.is_incumbent)
        best = max(candidates, key=lambda c: c.fitness)
        gen.winner = best

        # The second exam, on bars neither of them was chosen against.
        enough_holdout = holdout_bars >= self.brain.min_holdout_bars
        if enough_holdout and best is not incumbent:
            for candidate in (incumbent, best):
                candidate.holdout_fitness = score(
                    candidate.genome, start=split).fitness

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

        if not self.brain.promote_winners:
            gen.refused = "promotion is switched off"
        elif best is incumbent or best.fitness <= incumbent.fitness:
            gen.refused = "no mutant beat the incumbent"
        elif not enough_holdout:
            gen.refused = (
                f"only {holdout_bars} held-out bar(s), need "
                f"{self.brain.min_holdout_bars} — insufficient data to adopt"
            )
        elif (best.holdout_fitness is None
                or incumbent.holdout_fitness is None
                or best.holdout_fitness <= incumbent.holdout_fitness):
            gen.refused = (
                f"it won the fit ({incumbent.fitness} -> {best.fitness}) and lost "
                f"the held-out bars ({incumbent.holdout_fitness} -> "
                f"{best.holdout_fitness}) — fitted to the past, not to the market"
            )

        if not gen.refused:
            # **Genes are the evolver's. Everything else in the genome is the
            # firm's, and must survive being improved.**
            #
            # `normalise` rebuilds from BASE_GENOME and keeps only keys in
            # GENES, which is right for tuning and catastrophic as a write:
            # promoting a genome used to replace the whole dict, so a firm came
            # out of its first generation having lost every non-gene key it
            # carried.
            #
            # For a bankruptcy heir that is most of what it is. `analysts` is
            # where an heir's inherited seats live — it is not in the YAML and
            # never will be — so a promotion silently took firm_a_etf_ii from
            # four seats back to the default one and cut it off from the
            # scanners and the scribe. `inherited_from`, `inherited_lesson` and
            # `predecessor_diagnosis` went with it: the firm would have been
            # improved into an orphan with no memory of what killed its parent.
            #
            # Measured before this line existed: analysts ['fundamental',
            # 'technical', 'sentiment', 'signals'] -> None, at generation 1.
            carried = {k: v for k, v in (firm.genome or {}).items()
                       if k not in GENES}
            promoted = {**carried, **best.genome}
            self.store.update_firm_fields(
                firm.id, genome=json.dumps(promoted, sort_keys=True)
            )
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
