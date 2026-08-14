"""Automatic evolution — the village improving its own strategies.

The point of the brain, and the one piece of autonomy here that changes what a
firm *does* rather than what it may risk. So the tests are about the two things
that make that acceptable: it cannot adopt a genome that only fits the past,
and it cannot touch a firm trading real money.
"""

from __future__ import annotations

from decimal import Decimal

from src.trading.brain.evolver import BASE_GENOME, Evolver
from src.trading.config import BrainConfig, TradingConfig


# =========================================================================
# chosen on one half of history, adopted on the other
# =========================================================================
class _Result:
    """The handful of fields a genome row is written from."""

    def __init__(self, fitness):
        self.fitness = Decimal(str(fitness))
        self.return_pct = self.fitness
        self.max_drawdown_pct = Decimal(0)
        self.closed_trades = 50
        self.final_equity = Decimal("100000")
        self.fills = 50
        self.win_rate_pct = Decimal(50)
        self.sharpe = Decimal(1)

    def summary(self):
        return f"fitness {self.fitness}"


def _rigged(evolver, fit_scores, holdout_scores):
    """Make the backtester answer from a script.

    `fit_scores` and `holdout_scores` map a gene value to a fitness, so a test
    can build the exact case that matters: a mutant that wins the fit and loses
    the bars it was not fitted on.
    """
    class Rigged:
        def run(self, **kw):
            key = int(Decimal(str(kw["genome"]["fast_window"])))
            table = holdout_scores if kw.get("start") else fit_scores
            return _Result(table.get(key, 0))

    evolver.backtester = Rigged()


def test_a_genome_that_only_fits_the_past_is_refused(store, firm_record, market_data):
    """The failure this whole design turns on.

    Evolution searches seven dimensions for whatever curve best fits bars it
    has already seen. Promote on that alone and the loop is an overfitting
    machine that reports the overfit as progress.
    """
    evolver = Evolver(store, TradingConfig())
    # Every mutant scores brilliantly on the fit and terribly on the tail.
    _rigged(evolver,
            fit_scores={w: 100 for w in range(3, 31)} | {10: 1},
            holdout_scores={w: -100 for w in range(3, 31)} | {10: 50})
    store.update_firm_fields(firm_record.id, genome=None)
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    gen = evolver.evolve(firm, market_data, generation=1)
    assert gen.promoted is False
    assert "held-out" in gen.refused
    assert "fitted to the past" in gen.refused


def test_a_genome_that_wins_both_is_adopted(store, firm_record, market_data):
    evolver = Evolver(store, TradingConfig())
    _rigged(evolver,
            fit_scores={w: 100 for w in range(3, 31)} | {10: 1},
            holdout_scores={w: 100 for w in range(3, 31)} | {10: 1})
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    gen = evolver.evolve(firm, market_data, generation=1)
    assert gen.promoted is True, gen.refused
    assert gen.refused == ""
    assert store.require_firm_by_id(firm_record.id).genome != BASE_GENOME


def test_too_short_a_holdout_refuses_rather_than_guesses(store, firm_record, market_data):
    """A village that has been running for an afternoon has nothing to hold
    out, and the answer to that is the same as everywhere else here."""
    config = TradingConfig(brain=BrainConfig(min_holdout_bars=10_000))
    evolver = Evolver(store, config)
    _rigged(evolver, fit_scores={w: 100 for w in range(3, 31)} | {10: 1},
            holdout_scores={w: 100 for w in range(3, 31)})
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    gen = evolver.evolve(firm, market_data, generation=1)
    assert gen.promoted is False
    assert "insufficient data" in gen.refused


def test_the_candidates_are_fitted_without_seeing_the_tail(store, firm_record, market_data):
    """The split has to actually reach the backtester, or none of the above
    means anything."""
    evolver = Evolver(store, TradingConfig())
    seen = []

    class Watching:
        def run(self, **kw):
            seen.append((kw.get("start"), kw.get("steps")))
            return _Result(1)

    evolver.backtester = Watching()
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)
    evolver.evolve(firm, market_data, generation=1)

    fits = [s for s in seen if s[0] is None]
    assert fits, "nothing was fitted"
    assert all(steps is not None and steps > 0 for _, steps in fits), \
        "the fit ran over the whole history, tail included"


# =========================================================================
# what it will not touch
# =========================================================================
def test_evolution_is_off_until_the_switch_is_on(ecosystem):
    assert ecosystem.run_evolution(ecosystem.market()) == []


def _every_bar(ecosystem):
    """Make evolution due on every bar, so a test does not depend on the
    fixture's date happening to divide by twenty."""
    import dataclasses

    from src.trading.config import BrainConfig

    ecosystem.config = dataclasses.replace(
        ecosystem.config, brain=dataclasses.replace(ecosystem.config.brain,
                                                    evolve_every=1))
    ecosystem.evolver = type(ecosystem.evolver)(ecosystem.store, ecosystem.config)
    assert BrainConfig().evolve_every > 1     # the default really is a cadence
    return ecosystem


def test_a_live_firm_is_never_evolved_underneath_itself(ecosystem):
    _every_bar(ecosystem)
    """A firm through the promotion gate was approved on the evidence of one
    genome. Swapping it with nobody asked makes the approval meaningless."""
    ecosystem.settings.set("evolution", True, by="test")
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")
    before = ecosystem.store.get_firm(firm.firm_key).genome

    lines = ecosystem.run_evolution(ecosystem.market())
    assert any("real money" in line and firm.firm_key in line for line in lines)
    assert ecosystem.store.get_firm(firm.firm_key).genome == before


def test_a_killed_firm_is_left_alone(ecosystem):
    _every_bar(ecosystem)
    from src.trading.models import FirmStatus

    ecosystem.settings.set("evolution", True, by="test")
    firm = ecosystem.store.firms()[0]
    ecosystem.store.set_firm_status(firm.id, FirmStatus.KILLED.value)
    lines = ecosystem.run_evolution(ecosystem.market())
    assert not any(firm.firm_key in line for line in lines)


def test_it_runs_on_a_bar_cadence_not_a_tick_cadence(ecosystem):
    _every_bar(ecosystem)
    """Bars, never ticks. The distinction has its own module for a reason."""
    ecosystem.settings.set("evolution", True, by="test")
    market = ecosystem.market()

    first = ecosystem.run_evolution(market)
    assert first, "nothing happened on a bar that was due"

    # The loop comes round every sixty seconds and the bar has not moved, so
    # nothing more is owed. Without this guard a daily village would sweep a
    # generation every minute for a day.
    for _ in range(20):
        assert ecosystem.run_evolution(market) == []


def test_evolution_moves_no_capital_and_asks_for_no_approval(ecosystem):
    _every_bar(ecosystem)
    ecosystem.settings.set("evolution", True, by="test")
    before = [(f.firm_key, f.allocation, f.cash) for f in ecosystem.store.firms()]
    ecosystem.run_evolution(ecosystem.market())
    after = [(f.firm_key, f.allocation, f.cash) for f in ecosystem.store.firms()]
    assert before == after
    assert ecosystem.gate.pending() == []


def test_the_switch_is_on_the_wall(ecosystem):
    from src.trading.settings import KNOWN

    assert "evolution" in KNOWN
    assert ecosystem.settings.get("evolution", default=False) is False
    ecosystem.settings.toggle("evolution", default=False, by="test")
    assert ecosystem.settings.get("evolution", default=False) is True
