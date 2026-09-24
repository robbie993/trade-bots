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


def _unpurged(**brain):
    """A config with the purge gap switched off.

    The tests below drive a *rigged* backtester: it reads one gene out of the
    genome and answers from a lookup table. Nothing computes an indicator, so
    there is no lookback to reach backwards across a boundary and nothing for
    a purge gap to protect. Left on, the derived gap (as wide as
    `value_window`, 150 bars) exceeds what the 180-bar fixture can spare and
    every one of these collapses to "insufficient data" — which would be the
    tests measuring the fixture's length rather than the control flow they are
    about.

    Switched off *explicitly and by name*, so that a reader can tell these
    apart from a run against real bars, where 0 is never the right answer.
    `test_the_purge_gap_is_on_by_default` is what holds the default honest.

    `mutation_rate=1` is here for a different reason, and it is a scar.
    `mutate` draws one `rng.random()` per gene from a single stream, walking
    `GENES` in insertion order. Every one of these tests scores a candidate
    purely by its `fast_window`, so they quietly depend on that gene clearing
    the rate at this seed — and when seven genes were added to the table the
    stream shifted, `fast_window` stopped being drawn, all eight candidates
    scored identically, and three tests failed with "no mutant beat the
    incumbent", which names neither the gene nor the seed nor the cause.
    At rate 1 every gene moves every time and the tests stop depending on
    where in the table a gene happens to sit.
    """
    brain.setdefault("mutation_rate", Decimal(1))
    return TradingConfig(brain=BrainConfig(purge_bars=0, **brain))


def _varied(gen, gene="fast_window"):
    """Fail loudly when the population never moved the gene under test.

    Without this the symptom of a shifted RNG stream is "no mutant beat the
    incumbent" — a sentence about fitness, for a problem that is entirely
    about mutation. Half an hour went into that mistranslation once.
    """
    values = {int(Decimal(str(c.genome[gene]))) for c in gen.candidates}
    assert len(values) > 1, (
        f"the population never varied {gene} (all {values}), so this test "
        f"scored eight identical candidates and measured nothing")


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
    evolver = Evolver(store, _unpurged())
    # Every mutant scores brilliantly on the fit and terribly on the tail.
    _rigged(evolver,
            fit_scores={w: 100 for w in range(3, 31)} | {10: 1},
            holdout_scores={w: -100 for w in range(3, 31)} | {10: 50})
    store.update_firm_fields(firm_record.id, genome=None)
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    gen = evolver.evolve(firm, market_data, generation=1)
    _varied(gen)
    assert gen.promoted is False
    assert "held-out" in gen.refused
    assert "fitted to the past" in gen.refused


def test_a_genome_that_wins_both_is_adopted(store, firm_record, market_data):
    evolver = Evolver(store, _unpurged())
    _rigged(evolver,
            fit_scores={w: 100 for w in range(3, 31)} | {10: 1},
            holdout_scores={w: 100 for w in range(3, 31)} | {10: 1})
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    gen = evolver.evolve(firm, market_data, generation=1)
    _varied(gen)
    assert gen.promoted is True, gen.refused
    assert gen.refused == ""
    assert store.require_firm_by_id(firm_record.id).genome != BASE_GENOME


def test_too_short_a_holdout_refuses_rather_than_guesses(store, firm_record, market_data):
    """A village that has been running for an afternoon has nothing to hold
    out, and the answer to that is the same as everywhere else here."""
    evolver = Evolver(store, _unpurged(min_holdout_bars=10_000))
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
    evolver = Evolver(store, _unpurged())
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
# the fitted window and the holdout must not touch
# =========================================================================
def _windows(evolver, market, firm):
    """The bar ranges the backtester was actually asked for.

    Resolved the way `backtest.run` resolves them — `steps` counts from the
    warmup, not from zero — because that gap between what the evolver meant
    and what the backtester did is the whole bug.
    """
    seen = []

    class Watching:
        warmup = 90

        def run(self, **kw):
            seen.append((kw.get("start"), kw.get("steps")))
            return _Result(1)

    evolver.backtester = Watching()
    evolver.evolve(firm, market, generation=1)

    total = market.length()
    fitted, held = [], []
    for start, steps in seen:
        first = Watching.warmup if start is None else max(int(start), Watching.warmup)
        last = total if steps is None else min(total, first + steps)
        (held if start is not None else fitted).append((first, last))
    return fitted, held, total


def test_the_fitted_window_never_reaches_into_the_holdout(store, firm_record, market_data):
    """The bug this split had from the start.

    `split` was an index and was passed as `steps`; the backtester counts
    steps from its warmup, so the fitted window ran `warmup` bars past the
    split and into the tail. On the live village that put **90 of the 216
    held-out bars — 42% — inside the window the candidates were scored on**,
    and on this 180-bar fixture the holdout was 100% contained in it. Every
    "held-out" number the evolver has ever reported was partly a re-read of
    the bars it was chosen on.
    """
    evolver = Evolver(store, _unpurged())
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    fitted, held, _ = _windows(evolver, market_data, firm)
    assert fitted and held, "nothing was fitted, or nothing was held out"
    for f_first, f_last in fitted:
        for h_first, h_last in held:
            overlap = min(f_last, h_last) - max(f_first, h_first)
            assert overlap <= 0, (
                f"fitted [{f_first},{f_last}) overlaps holdout "
                f"[{h_first},{h_last}) by {overlap} bars")


def test_the_purge_gap_is_on_by_default_and_is_as_wide_as_the_widest_lookback(
        store, firm_record, market_data):
    """Abutting windows are not enough: the holdout's first bars still compute
    their indicators out of the fitted bars. The gap is what stops that, and
    it has to be at least as wide as the furthest an analyst can reach.
    """
    from src.trading.brain.evolver import max_lookback_bars

    evolver = Evolver(store, TradingConfig())        # the shipped default
    assert evolver.purge_bars() == max_lookback_bars() > 0
    # `value_window` tops out at 150, and nothing may reach further than the
    # gap that is supposed to cover it.
    from src.trading.brain.evolver import GENES, WINDOW_GENES

    for gene in WINDOW_GENES:
        assert int(GENES[gene][1]) <= evolver.purge_bars(), \
            f"{gene} can reach past the purge gap"


def test_a_holdout_that_cannot_be_purged_is_refused_rather_than_leaked(
        store, firm_record, market_data):
    """180 bars cannot spare a 150-bar gap on top of a 90-bar warmup and a
    54-bar tail. The honest answer is the village's usual one, not a holdout
    that quietly overlaps."""
    evolver = Evolver(store, TradingConfig())        # gap on, as shipped
    _rigged(evolver, fit_scores={w: 100 for w in range(3, 31)} | {10: 1},
            holdout_scores={w: 100 for w in range(3, 31)} | {10: 1})
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    assert evolver._split(market_data, firm.universe) == (0, 0, 0)
    gen = evolver.evolve(firm, market_data, generation=1)
    assert gen.promoted is False


def test_the_gap_comes_out_of_the_fitted_side_so_the_fraction_keeps_its_meaning(store):
    """`holdout_fraction` says 30% is held out. It has to still be 30%.

    Carving the gap out of the holdout instead would leave the knob reading
    0.30 while delivering 9% — a config constant describing different data,
    which is a failure this repository has hit enough times to have a name
    for.
    """
    from src.trading.config import DataConfig

    config = TradingConfig(
        brain=BrainConfig(holdout_fraction=Decimal("0.30")),
        data=DataConfig(source="synthetic", seed=12345, history_days=720),
    )
    evolver = Evolver(store, config)
    from src.trading.data.feeds import SyntheticFeed
    from src.trading.data.market_data import MarketData

    symbols = ["SPY", "QQQ"]
    market = MarketData(SyntheticFeed(seed=12345, days=720), symbols)
    fitted, holdout_start, holdout_bars = evolver._split(market, symbols)

    total = market.length()
    assert holdout_bars == int(total * Decimal("0.30"))
    assert holdout_start + holdout_bars == total
    # And the gap really is between them, not inside either.
    assert holdout_start - (90 + fitted) == evolver.purge_bars()


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


# =========================================================================
# flipping it from a terminal
# =========================================================================
def test_the_switch_can_be_flipped_without_a_browser(db, tmp_path, firms_yaml,
                                                     monkeypatch, capsys):
    """Mission Control was the only way to touch a switch, which is no use to
    somebody already in a shell — and this village is mostly run from one."""
    monkeypatch.setenv("DATABASE_URL", db.url)
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    from src.cli import main

    main(["trade", "init"])
    capsys.readouterr()

    main(["trade", "switches"])
    listing = capsys.readouterr().out
    assert "evolution" in listing and "off" in listing

    main(["trade", "switch", "evolution", "--on"])
    assert "evolution is now ON" in capsys.readouterr().out

    main(["trade", "switches"])
    assert "ON" in capsys.readouterr().out

    main(["trade", "switch", "evolution", "--off"])
    assert "now off" in capsys.readouterr().out


def test_an_unknown_switch_is_refused_by_name(db, tmp_path, firms_yaml,
                                              monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.url)
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    from src.cli import main

    assert main(["trade", "switch", "nonsense"]) == 1
    out = capsys.readouterr().out
    assert "no switch called" in out
    assert "evolution" in out, "it should say what the real ones are"


def test_the_page_and_the_terminal_flip_the_same_switch(db, tmp_path, firms_yaml,
                                                        monkeypatch, capsys):
    """One village, one set of controls. A switch flipped in a shell has to be
    the switch the page is showing, or the two disagree about what is running."""
    monkeypatch.setenv("DATABASE_URL", db.url)
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    from src.cli import main
    from src.trading.settings import Settings

    main(["trade", "init"])
    main(["trade", "switch", "evolution", "--on"])
    capsys.readouterr()

    assert Settings(db).get("evolution", default=False) is True


def test_a_ranking_that_fails_its_look_count_cannot_promote(
    store, firm_record, market_data, monkeypatch
):
    """The look counter is a gate, not a narration.

    A genome that wins the fit *and* the holdout is normally adopted — the test
    above proves it. This is that same genome, refused, because the generation's
    own ranking did not survive the number of times this firm has been asked.
    Before this, it promoted anyway: on the strength of an ordering it had just
    recorded as carrying no out-of-sample information.
    """
    from src.trading.brain.evolver import Evolver as _Ev

    monkeypatch.setattr(
        _Ev, "_record_rank_test",
        lambda self, firm, generation, candidates, purge_bars=0: (
            "rho=+0.010 p=0.900 | ranks nothing", False),
    )

    evolver = Evolver(store, _unpurged())
    _rigged(evolver,
            fit_scores={w: 100 for w in range(3, 31)} | {10: 1},
            holdout_scores={w: 100 for w in range(3, 31)} | {10: 1})
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    gen = evolver.evolve(firm, market_data, generation=1)
    assert gen.promoted is False, "it won both exams and must still be refused"
    assert "look count" in gen.refused


def test_a_ranking_that_survives_its_look_count_still_promotes(
    store, firm_record, market_data, monkeypatch
):
    """The gate must not be a blanket refusal — the same genome, allowed."""
    from src.trading.brain.evolver import Evolver as _Ev

    monkeypatch.setattr(
        _Ev, "_record_rank_test",
        lambda self, firm, generation, candidates, purge_bars=0: (
            "rho=+0.400 p=0.001 | survives its own history", True),
    )

    evolver = Evolver(store, _unpurged())
    _rigged(evolver,
            fit_scores={w: 100 for w in range(3, 31)} | {10: 1},
            holdout_scores={w: 100 for w in range(3, 31)} | {10: 1})
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)

    gen = evolver.evolve(firm, market_data, generation=1)
    assert gen.promoted is True, gen.refused


def test_the_gate_reads_the_rank_result_not_just_the_fit():
    """`_record_rank_test` must hand back a verdict the caller can act on."""
    import inspect

    from src.trading.brain.evolver import Evolver

    src = inspect.getsource(Evolver.evolve)
    assert "ranked" in src, "evolve must capture the rank verdict"
    assert "elif not ranked" in src, "and refuse promotion on it"

    rec = inspect.getsource(Evolver._record_rank_test)
    assert "return note, bool(" in rec, "the recorder must return a verdict"
    assert 'return "", True' in rec, (
        "a ledger it cannot write must not become a gate it cannot pass"
    )
