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
    # --- what the real fleet actually uses -------------------------------
    #
    # The village could not express any of these, and the consequence was not
    # that the fleet scored badly — it was that the fleet could not be scored
    # at all. Submitted to the court, VERITAS had every parameter that makes
    # it VERITAS dropped with the note "unrecognised parameters (they will be
    # ignored)", was backtested as the remainder, lost 1.74%, and was
    # convicted. That verdict was about a strategy wearing its name.
    #
    # It also explains why evolution has only ever searched moving-average
    # and RSI variants: not because that space was chosen, but because it was
    # the only space the vocabulary could describe.
    #
    # Entry thresholds, from VERITAS (RSI2 + Internal Bar Strength reversion).
    "rsi_entry": (D(2), D(50), False),
    "ibs_entry": (D("0.05"), D("0.95"), False),
    "pullback_atr": (D("0.5"), D(5), False),
    # Portfolio shape. A concurrency cap is a real strategy choice — it sets
    # how much of the book one idea may own — and every desk here had one
    # hardcoded.
    "max_positions": (D(1), D(10), True),
    # Cross-sectional momentum: how far back to rank, how much of the ranked
    # universe to hold, and the ceiling on any single name.
    "lookback": (D(4), D(104), True),
    "top_fraction": (D("0.05"), D(1), False),
    "max_per_name": (D("0.05"), D(1), False),
}

#: Parameters a strategy may legitimately declare that are deliberately NOT
#: genes, with the reason. Without this the obvious fix to the vocabulary gap
#: is to add every unrecognised name, and one of them must never be added.
#:
#: `bars_per_day` is 26 in VERITAS because a regular session is six and a half
#: hours of fifteen-minute bars. That is a fact about the resolution, not a
#: choice about the strategy, and `resolution.py` exists so exactly one place
#: in this system knows it — the module written after three separate bugs came
#: from somewhere else deciding how long a bar was. Making it evolvable would
#: let a genome mutate the length of the trading day, and the resulting
#: annualisation would be wrong in a way nothing would catch.
NOT_GENES: dict = {
    "bars_per_day": "a property of the bar resolution, owned by resolution.py",
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
    # The fleet's own shipped values, not invented neutrals. VERITAS really
    # enters at RSI 10 with IBS below 0.3 and a 2.5-ATR pullback, capped at 4
    # concurrent positions; the cross-sectional desk really ranks on 52 bars,
    # holds the top third and caps any one name at 20%. Starting anywhere else
    # would mean the first generation is already a different strategy from the
    # one whose evidence justified adding these genes at all.
    #
    # Existing firms are unaffected: no analyst reads these, so they sit inert
    # until a bot that uses them is recruited. Every gene needs a default
    # regardless — the court reads `BASE_GENOME` to fill what a submission
    # leaves out, and a gene present in GENES and missing here is a KeyError
    # that reads as "could not be read: 'rsi_entry'" on every file in the
    # directory, which is exactly how this was found.
    "rsi_entry": 10.0,
    "ibs_entry": 0.3,
    "pullback_atr": 2.5,
    "max_positions": 4,
    "lookback": 52,
    "top_fraction": 0.34,
    "max_per_name": 0.20,
}

#: Genes whose value is *a number of bars of price history*, as opposed to a
#: percentage, a bias or a trust weight. These are what decide how far back an
#: analyst reaches when it scores a single bar, and therefore how wide the gap
#: between a fitted window and its holdout has to be.
#:
#: Listed explicitly rather than matched on a `_window` suffix, because
#: `lookback` has no suffix and `shadow_dte_max` has the wrong kind of unit —
#: it is days to an option's expiry, not bars of history to read back over.
#: Guessing from names is how a gap silently stops covering the widest gene.
WINDOW_GENES: tuple = (
    "fast_window", "slow_window", "rsi_window", "value_window", "lookback",
)


#: Which measurement regime the scores written now belong to.
#:
#: **Bump this whenever a change makes new fitness numbers incomparable with
#: old ones**, and say why in a migration comment. It is not a schema version
#: and it is not a release number: it answers only "may these two stored
#: numbers be subtracted?"
#:
#: 1 — everything before 2026-09-14. The holdout overlapped the fitted window
#:     by up to 42% of its bars, and fitness had no benchmark in it.
#: 2 — the fitted window and the holdout abut with a purge gap between them,
#:     and fitness is excess over max(own-universe buy-and-hold, cash).
#:
#: The in-memory equivalent is `BacktestResult.comparable_with`, which refuses
#: mismatched windows and mismatched hurdles. This is the same refusal for
#: numbers that have been written down, where the context is long gone.
MEASUREMENT_EPOCH = 2


def max_lookback_bars() -> int:
    """The furthest back any analyst can reach for a genome inside its ranges.

    Derived, never hardcoded. A new window gene added to `GENES` and listed in
    `WINDOW_GENES` widens the purge gap automatically; a hardcoded 150 would
    have gone on describing the old vocabulary while the new one leaked.

    Both halves matter. `value_window` tops out at 150 bars, which is the
    widest gene; the analysts also carry their own hardcoded floors
    (`FundamentalAnalyst.minimum_bars` is 90), and a seat that reaches further
    than any gene would otherwise be invisible here.
    """
    widest = 0
    for name in WINDOW_GENES:
        bounds = GENES.get(name)
        if bounds:
            widest = max(widest, int(bounds[1]))
    try:
        from ..firms.analysts import ANALYSTS

        for seat in ANALYSTS.values():
            widest = max(widest, int(getattr(seat, "minimum_bars", 0) or 0))
    except Exception:               # noqa: BLE001 - a gap is not worth a crash
        pass
    return widest


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

    def mark_epoch(self, genome_id, epoch: int = MEASUREMENT_EPOCH,
                   reason: str = "") -> None:
        """Stamp a stored genome with the regime its score was measured under.

        Every row, not only the ones carrying a held-out score: an unmarked row
        is indistinguishable from a pre-fix one, and the backfill in migration
        025 deliberately does not supply a default for exactly that reason.

        Never fails a run, for the same reason `_keep_holdout` does not — but
        note the asymmetry that costs: a *missing* stamp is a row a later
        reader cannot place, so the failure here is quieter than it looks. It
        is accepted only because the alternative is a stopped village.
        """
        if genome_id is None:
            return
        try:
            self.store.db.insert("genome_epoch", {
                "genome_id": genome_id,
                "epoch": int(epoch),
                "reason": reason or "purged holdout, benchmark-hurdled fitness",
            })
        except Exception:               # noqa: BLE001
            pass

    def _keep_holdout(self, genome_id, candidate, fitted_bars: int,
                      holdout_bars: int) -> None:
        """Write the second exam beside the first.

        It lived on the in-memory Candidate and was discarded every
        generation, which is why the one question that separates a strategy
        from a curve fit had to be answered by re-running 280 backtests.

        Both bar counts go with it: fitness is window-sized, so without them
        a later reader cannot tell whether a difference between the two
        numbers is the genome or the window. Never fails a run — a missing
        diagnostic row is worth less than a stopped village.
        """
        if genome_id is None or candidate.holdout_fitness is None:
            return
        try:
            self.store.db.insert("genome_holdout", {
                "genome_id": genome_id,
                "holdout_fitness": candidate.holdout_fitness,
                "holdout_bars": holdout_bars,
                "fitted_bars": fitted_bars,
            })
        except Exception:               # noqa: BLE001
            pass

    def _record_rank_test(self, firm, generation: int, candidates,
                          purge_bars: int = 0) -> str:
        """Ask whether this generation's ranking predicted anything, and count
        the asking.

        The evolver has always sorted candidates by in-sample fitness and
        adopted the top one. Whether that sort carries any out-of-sample
        information was never checked, and when it finally was — across the
        whole stored population — it did not: Spearman +0.056 on n=280.

        So the question is asked every generation now, and recorded in the
        ledger that knows how many times it has been asked. The look is
        counted whether the answer flatters the generation or not, because a
        ledger that only hears about the good runs is worse than no ledger.
        Failing to write it must never fail a run — evolution is allowed to
        proceed, it is just no longer allowed to proceed unmeasured.
        """
        from datetime import datetime, timezone

        from ..research import PValueLedger

        try:
            rho, p, n = _spearman_of(candidates)
            if n < 3:
                return ""
            ledger = PValueLedger()
            subject = f"evolver:{firm.firm_key}"
            ctx = ledger.context(subject, p)
            note = _rank_note(rho, p, n, ctx)
            ledger.record(
                subject=subject,
                test="in-sample fitness rank predicts held-out rank",
                p=p,
                run_date=datetime.now(timezone.utc).date().isoformat(),
                # The gap goes in the note because it is the reason the number
                # means anything. A rho measured across a leaking boundary and
                # one measured across a purged boundary are different claims,
                # and the ledger is where a later reader finds out which this
                # was. It rides here rather than in a `genome_holdout` column
                # because that table is created with `CREATE TABLE IF NOT
                # EXISTS` and every migration re-runs on `init-db` — adding a
                # column to it would not reach the databases already running,
                # and the insert would fail into `_keep_holdout`'s swallowed
                # exception, losing the whole diagnostic row silently.
                note=f"generation {generation}, purge gap {purge_bars} bars, {note}",
                verdict="PASS" if (rho > 0 and ctx["survives_bonferroni_05"])
                        else "FAIL",
            )
            return note
        except Exception:               # noqa: BLE001 - never fail a run
            return ""

    def purge_bars(self) -> int:
        """Bars discarded between the fitted window and the holdout.

        `TRADE_EVO_PURGE` overrides it; -1 (the default) means derive it from
        the vocabulary. Zero is legitimate and is what a test with a rigged
        backtester wants — there are no indicators there to reach backwards —
        but it is never the right answer against real bars.
        """
        configured = int(getattr(self.brain, "purge_bars", -1))
        return max(0, configured) if configured >= 0 else max_lookback_bars()

    def _split(self, market: MarketData, symbols) -> tuple:
        """Where the fitted window ends, where the holdout begins, how long it is.

        Returns ``(fitted_bars, holdout_start, holdout_bars)``. Zero fitted
        bars means there is not enough history to divide at all, in which case
        everything is fitted and nothing can be adopted — the correct answer
        for a village that has been running for an afternoon.

        **Two separate ways the old split leaked, both fixed here.**

        *One: the fitted window ran straight through the split point.* `split`
        was computed as an index (`total - holdout`) and then handed to the
        backtester as `steps`. The backtester counts steps from its warmup,
        not from zero, so the fitted window was `[warmup, warmup + split)` —
        `warmup` bars longer than intended, reaching `warmup` bars past the
        split and into the tail. Measured on the live village: the fitted
        window was [90, 594) and the "holdout" [504, 720), so **90 of the 216
        held-out bars, 42% of them, were bars the candidates had been scored
        on.** On a 180-bar test fixture the holdout was 100% contained in the
        fitted window. `fitted_bars` is now the count the backtester actually
        wants — bars *after* the warmup — so the two windows abut instead of
        overlapping.

        *Two: no purge gap.* Even with the windows abutting, the holdout's
        first bars compute their indicators out of the fitted bars — up to
        `value_window` of them, 150 at the top of its range. The gap discards
        that many bars between the two so no held-out score is a function of a
        bar the genome was selected on. It costs real history and it is worth
        it: without it "held out" is a claim the arithmetic does not support.

        The gap comes out of the *fitted* side. `holdout_fraction` keeps
        meaning what it says — that share of history is held out — rather than
        quietly describing a window a third of its documented size, which is
        the failure mode this repository has hit often enough to name.
        """
        data = MarketData(market.feed, symbols)
        data.register(list(symbols))
        total = data.length()
        fraction = D(self.brain.holdout_fraction)
        if total < 2 or fraction <= 0 or fraction >= 1:
            return 0, 0, 0
        warmup = max(0, int(getattr(self.backtester, "warmup", 0) or 0))
        holdout = int(D(total) * fraction)
        gap = self.purge_bars()
        holdout_start = total - holdout
        # Bars the backtester will actually score, counted from its warmup.
        fitted_bars = holdout_start - gap - warmup
        if fitted_bars <= 0 or holdout <= 0:
            return 0, 0, 0
        return fitted_bars, holdout_start, holdout

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

        fitted_bars, holdout_start, holdout_bars = self._split(market, symbols)
        gap = self.purge_bars()

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
            candidate.result = score(candidate.genome, steps=fitted_bars or None)
            candidate.fitness = candidate.result.fitness

        gen = Generation(number=generation, candidates=candidates)
        incumbent = next(c for c in candidates if c.is_incumbent)
        best = max(candidates, key=lambda c: c.fitness)
        gen.winner = best

        # The second exam, on bars nobody was chosen against.
        #
        # Every candidate sits it, not just the incumbent and the winner. The
        # adopt decision only ever needed those two, which is why it was
        # written that way — but the far more important question is whether
        # the *ranking* means anything, and that needs the whole population.
        # Answering it once, retrospectively, cost 280 re-run backtests: the
        # in-sample rank predicted the held-out rank with a Spearman of
        # +0.056 (n=280, t=+0.94), which is no information at all. Sitting
        # the whole cohort makes that a standing readout rather than an
        # archaeology project, so the next claim that evolution is working
        # can be checked against the generation that made it.
        enough_holdout = holdout_bars >= self.brain.min_holdout_bars
        if enough_holdout:
            for candidate in candidates:
                candidate.holdout_fitness = score(
                    candidate.genome, start=holdout_start).fitness
            self._record_rank_test(firm, generation, candidates, gap)

        # (the rank test above is recorded before anything is adopted, so the
        # ledger counts the look whether or not the generation liked itself)

        # The incumbent goes in first so the mutants can point at it. Every
        # mutant in a generation *is* a mutation of that one genome, and
        # recording which one turns `strategy_genomes` from a list into a
        # lineage — without it `parent_id` sits null and the descent of a
        # strategy is unrecoverable after the fact.
        parent_id = self.store.db.insert(
            "strategy_genomes",
            _genome_row(firm, generation, incumbent, best, incumbent),
        )
        self.mark_epoch(parent_id)
        self._keep_holdout(parent_id, incumbent, fitted_bars, holdout_bars)
        for candidate in candidates:
            if candidate is incumbent:
                continue
            genome_id = self.store.db.insert(
                "strategy_genomes",
                {**_genome_row(firm, generation, candidate, best, incumbent),
                 "parent_id": parent_id},
            )
            self.mark_epoch(genome_id)
            self._keep_holdout(genome_id, candidate, fitted_bars, holdout_bars)

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


def _spearman_of(candidates) -> tuple:
    """`(rho, p, n)` for in-sample rank against held-out rank in one cohort."""
    from ..research import spearman

    pairs = [(float(c.fitness), float(c.holdout_fitness)) for c in candidates
             if c.fitness is not None and c.holdout_fitness is not None]
    return spearman([a for a, _ in pairs], [b for _, b in pairs])


def _rank_note(rho: float, p: float, n: int, ctx: dict) -> str:
    """One line saying whether this generation's ranking ranked anything."""
    verdict = ("ranks nothing" if p > 0.05 or rho <= 0
               else "survives its own history" if ctx["survives_bonferroni_05"]
               else "nominally positive, fails the look count")
    return (f"rho={rho:+.3f} p={p:.3f} n={n} | look {ctx['tests_including_this']}, "
            f"needs p<{ctx['bonferroni_floor']:.4f} | {verdict}")


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
