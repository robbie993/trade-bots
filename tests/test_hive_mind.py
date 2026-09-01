"""The hive mind, and the straitjacket it has to earn its way out of.

These tests assert the *mechanism*, never a verdict. Whether a genome makes
money on a generated tape is a fact about the generator's seed, and asserting
it would be the same mistake the walk-forward lock exists to catch. What has to
hold is narrower and checkable: that training cannot reach the holdout, that a
frozen phase is actually frozen, that real money only ever moves the sizing
genes, and that a killed genome stays killed.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from hive_mind.council import VillageCouncil
from hive_mind.engine import GodBrokerEngine, MutationRefused, Result, backtest
from hive_mind.evolver import (
    ALL_GENES,
    BASE_GENOME,
    BOUNDS,
    SIZING_GENES,
    STRATEGY_GENES,
    Evolver,
    Genome,
    clamp,
)
from hive_mind.lock import (
    LockConfig,
    Phase,
    WalkForwardLock,
    strategy_fingerprint,
)
from hive_mind.market import MarketFeed, PerfectVenue, Venue, WindowFeed
from hive_mind.memory import ObsidianMemory
from hive_mind.scouts import ScoutAI


@pytest.fixture
def feed():
    return MarketFeed(seed=99, plan=[("calm_bull", 120), ("crash", 30), ("rebound", 100)])


@pytest.fixture
def bar(feed):
    return feed.bars()[60]


# =========================================================================
# the tape, and what a fill costs
# =========================================================================
def test_the_tape_is_the_same_on_every_run():
    first = MarketFeed(seed=3, plan=[("chop", 50)]).bars()
    second = MarketFeed(seed=3, plan=[("chop", 50)]).bars()
    assert [b.close for b in first] == [b.close for b in second]
    assert [b.close for b in MarketFeed(seed=4, plan=[("chop", 50)]).bars()] != [
        b.close for b in first
    ]


def test_a_window_feed_has_no_bars_outside_its_window(feed):
    whole = feed.bars()
    window = WindowFeed(feed, 0, 80)
    bars = window.bars()
    assert len(bars) == 80
    assert bars[-1].close == whole[79].close
    # Not "declines to return them" — has none. There is no other accessor.
    assert not hasattr(window, "future")
    assert len(list(window)) == 80


def test_a_buy_fills_above_the_close_and_a_sell_below(bar):
    venue = Venue()
    bought = venue.execute("buy", 100, bar)
    sold = venue.execute("sell", 100, bar)
    assert bought.price > bar.close
    assert sold.price < bar.close
    assert bought.fee > 0 and bought.slippage > 0


def test_size_costs_more_per_share(bar):
    venue = Venue()
    small = venue.execute("buy", 50, bar)
    large = venue.execute("buy", 5_000, bar)
    small_cost = small.price - bar.close
    large_cost = large.price - bar.close
    assert large_cost > small_cost, "impact must grow with size, or size is free"


def test_the_spread_widens_when_the_tape_is_frightened(feed):
    bars = feed.bars()
    calm = min(bars, key=lambda b: b.vix)
    scared = max(bars, key=lambda b: b.vix)
    assert scared.spread / scared.close > calm.spread / calm.close


def test_perfect_fills_flatter_the_same_genome(feed):
    honest = backtest(BASE_GENOME, feed, seed=1, venue=Venue())
    perfect = backtest(BASE_GENOME, feed, seed=1, venue=PerfectVenue())
    assert perfect.return_pct > honest.return_pct
    assert honest.slippage > 0 and perfect.slippage == 0


# =========================================================================
# memory
# =========================================================================
def test_memory_refuses_to_average_a_sample_of_two():
    memory = ObsidianMemory(minimum_sample=5)
    for _ in range(2):
        memory.store("SPY", 32.0, "long", 100.0, 1.0)
    recall = memory.recall("SPY", 32.0)
    assert not recall.known
    assert recall.trades == 2
    assert "nothing comparable" in str(recall)


def test_memory_files_by_regime_not_by_day():
    memory = ObsidianMemory(minimum_sample=1)
    memory.store("SPY", 29.0, "long", 10.0, 1.0)
    memory.store("SPY", 31.0, "long", -5.0, -0.5)  # same VIX bucket (both round to 30)
    memory.store("SPY", 12.0, "long", 10.0, 1.0)  # a different one
    assert memory.nodes() == 2
    assert memory.recall("SPY", 30.0).trades == 2


def test_memory_can_be_rolled_back():
    memory = ObsidianMemory(minimum_sample=1)
    memory.store("SPY", 20.0, "long", 1.0, 0.1)
    snapshot = memory.snapshot()
    memory.store("SPY", 20.0, "long", 2.0, 0.2)
    assert memory.total_trades() == 2
    memory.restore(snapshot)
    assert memory.total_trades() == 1


# =========================================================================
# scouts and the council
# =========================================================================
def test_a_scout_refuses_its_own_over_leveraged_idea(bar):
    scout = ScoutAI(id=1, memory=ObsidianMemory())
    from hive_mind.scouts import Proposal

    too_big = Proposal(1, "buy", BASE_GENOME.leverage_cap + 0.5, "greed", 0.5)
    assert not scout.fact_check(too_big, bar, BASE_GENOME)
    fine = Proposal(1, "buy", BASE_GENOME.leverage_cap, "sized", 0.5)
    assert scout.fact_check(fine, bar, BASE_GENOME)


def test_a_scout_will_not_read_an_indicator_off_too_little_history(feed):
    scout = ScoutAI(id=1, memory=ObsidianMemory())
    bars = feed.bars()
    window = int(BASE_GENOME.momentum_window)

    # A window-of-10 reading needs 11 closes. Below that there is no proposal
    # at all, at any bar.
    for i in range(window):
        assert scout.propose(bars[i], bars[: i + 1], BASE_GENOME) is None

    # Above it there is at least sometimes one — otherwise the check above
    # would pass on a scout that never speaks, which proves nothing.
    spoke = any(
        scout.propose(bars[i], bars[: i + 1], BASE_GENOME) is not None
        for i in range(window, 120)
    )
    assert spoke


def test_a_vote_key_survives_an_action_with_an_underscore_in_it():
    """The bug in the obvious implementation: keys built by string join."""
    from hive_mind.scouts import Proposal

    a = Proposal(1, "buy_the_dip", 1.5, "", 0.5)
    b = Proposal(2, "buy", 1.5, "", 0.5)
    assert a.key != b.key
    assert a.key == ("buy_the_dip", 1.5)


def test_confidence_moves_with_outcomes_and_stays_inside_its_bounds():
    scout = ScoutAI(id=1, memory=ObsidianMemory())
    start = scout.confidence
    scout.settle(True)
    assert scout.confidence > start
    for _ in range(200):
        scout.settle(False)
    assert scout.confidence >= ScoutAI.MIN_CONFIDENCE
    for _ in range(400):
        scout.settle(True)
    assert scout.confidence <= ScoutAI.MAX_CONFIDENCE


def test_a_split_vote_under_the_conviction_floor_does_nothing(feed):
    council = VillageCouncil(ObsidianMemory(), scouts=5, seed=1)
    bars = feed.bars()
    genome = replace(BASE_GENOME, conviction_floor=0.99)  # nothing can clear it
    decision = council.debate(bars[60], bars[:61], genome)
    assert decision.action == "hold"
    assert not decision.is_trade
    assert decision.blocked_by_conviction or "no scout" in decision.reason


def test_an_empty_debate_is_a_hold_not_a_guess(feed):
    council = VillageCouncil(ObsidianMemory(), scouts=5, seed=1)
    bars = feed.bars()
    decision = council.debate(bars[2], bars[:3], BASE_GENOME)  # no history yet
    assert decision.action == "hold"
    assert decision.confidence == 0.0


def test_the_council_cannot_exceed_the_genomes_leverage_cap(feed):
    council = VillageCouncil(ObsidianMemory(), scouts=5, seed=2)
    bars = feed.bars()
    genome = replace(BASE_GENOME, leverage_cap=0.3, conviction_floor=0.15)
    for i in range(30, 200):
        decision = council.debate(bars[i], bars[: i + 1], genome)
        assert decision.leverage <= genome.leverage_cap + 1e-9


# =========================================================================
# the genome
# =========================================================================
def test_every_gene_is_clamped_into_its_range():
    wild = Genome(leverage_cap=99.0, position_pct=5.0, risk_tolerance=-3.0, vix_spike=1.0)
    fixed = clamp(wild)
    for name, (low, high) in BOUNDS.items():
        assert low <= getattr(fixed, name) <= high


def test_a_calm_threshold_above_the_spike_threshold_is_repaired():
    fixed = clamp(Genome(vix_spike=20.0, vix_calm=21.0))
    assert fixed.vix_calm < fixed.vix_spike


def test_the_fingerprint_names_the_numbers_not_the_object():
    assert BASE_GENOME.fingerprint() == replace(BASE_GENOME).fingerprint()
    assert BASE_GENOME.fingerprint() != replace(BASE_GENOME, trend_bias=0.51).fingerprint()


def test_the_strategy_fingerprint_ignores_sizing_and_only_sizing():
    resized = replace(BASE_GENOME, position_pct=0.4, leverage_cap=2.5, risk_tolerance=0.3)
    assert strategy_fingerprint(resized) == strategy_fingerprint(BASE_GENOME)
    assert resized.fingerprint() != BASE_GENOME.fingerprint()

    rethought = replace(BASE_GENOME, trend_bias=0.9)
    assert strategy_fingerprint(rethought) != strategy_fingerprint(BASE_GENOME)


def test_mutation_touches_only_the_genes_it_was_given():
    evolver = Evolver(seed=5, mutation_rate=1.0, scale=0.4)
    rng = evolver.rng(1)
    mutant = evolver.mutate(BASE_GENOME, rng, genes=SIZING_GENES)
    for name in STRATEGY_GENES:
        assert getattr(mutant, name) == getattr(BASE_GENOME, name)
    assert any(getattr(mutant, n) != getattr(BASE_GENOME, n) for n in SIZING_GENES)


def test_mutation_does_not_depend_on_the_order_the_genes_arrive_in():
    """Set iteration order is per-process, so iterating one is not reproducible."""
    evolver = Evolver(seed=5, mutation_rate=0.5, scale=0.3)
    as_set = evolver.mutate(BASE_GENOME, evolver.rng(1), genes=SIZING_GENES)
    as_list = evolver.mutate(BASE_GENOME, evolver.rng(1), genes=list(SIZING_GENES)[::-1])
    assert as_set.fingerprint() == as_list.fingerprint()


def test_the_same_command_gives_the_same_answer_in_a_different_process():
    """The claim is 'on any machine', so one process proving it is not enough."""
    import subprocess
    import sys

    script = (
        "from hive_mind.evolver import BASE_GENOME, Evolver, ALL_GENES;"
        "e = Evolver(seed=5, mutation_rate=0.6, scale=0.3);"
        "print(e.mutate(BASE_GENOME, e.rng(1), genes=ALL_GENES).fingerprint())"
    )
    outputs = set()
    for hash_seed in ("0", "1", "12345"):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": hash_seed, "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        outputs.add(proc.stdout.strip())
    assert len(outputs) == 1, f"the mutant changed with PYTHONHASHSEED: {outputs}"


def test_mutation_refuses_a_gene_that_is_not_one():
    evolver = Evolver(seed=5)
    with pytest.raises(ValueError):
        evolver.mutate(BASE_GENOME, evolver.rng(1), genes={"sharpe_ratio"})


def test_a_population_always_contains_the_incumbent_unchanged():
    evolver = Evolver(seed=5)
    population = evolver.population(BASE_GENOME, size=6, generation=1)
    assert population[0] is BASE_GENOME
    assert len(population) == 6


# =========================================================================
# the engine
# =========================================================================
def test_two_identical_runs_agree_to_the_cent(feed):
    first = backtest(BASE_GENOME, feed, seed=11)
    second = backtest(BASE_GENOME, feed, seed=11)
    assert first.final_equity == second.final_equity
    assert first.closed_trades == second.closed_trades


def test_the_book_ends_flat_and_the_curve_ends_with_it(feed):
    engine = GodBrokerEngine(BASE_GENOME, capital=100_000.0, seed=3)
    result = engine.run(feed)
    assert engine.shares == 0
    assert result.equity_curve[-1] == pytest.approx(engine.cash)


def test_costs_are_charged_and_reported(feed):
    result = backtest(BASE_GENOME, feed, seed=3)
    if result.fills:
        assert result.slippage > 0
        assert result.fees > 0
        assert result.cost_drag_pct > 0


def test_sharpe_is_none_below_a_sample_worth_quoting():
    thin = Result(start_capital=100.0, final_equity=110.0, equity_curve=[100.0, 110.0])
    assert thin.sharpe is None


def test_max_drawdown_is_measured_from_the_peak():
    result = Result(equity_curve=[100.0, 120.0, 60.0, 90.0], start_capital=100.0)
    assert result.max_drawdown_pct == pytest.approx(50.0)


# =========================================================================
# the lock
# =========================================================================
def test_an_unproven_genome_may_not_mutate_at_all():
    lock = WalkForwardLock(LockConfig())
    permit = lock.permit(BASE_GENOME)
    assert not permit
    assert permit.genes == frozenset()
    assert "proved nothing" in permit.reason


@pytest.mark.parametrize("phase", [Phase.GAUNTLET, Phase.STRESS, Phase.PAPER])
def test_every_measuring_phase_is_frozen(phase):
    lock = WalkForwardLock(LockConfig())
    lock.phase = phase
    assert not lock.permit(BASE_GENOME)


def test_training_may_touch_everything_and_real_money_may_not():
    lock = WalkForwardLock(LockConfig())
    lock.phase = Phase.CRUCIBLE
    assert lock.permit(BASE_GENOME).genes == ALL_GENES

    lock.phase = Phase.SUICIDE_FUND
    lock.certificate = strategy_fingerprint(BASE_GENOME)
    permit = lock.permit(BASE_GENOME)
    assert permit and permit.genes == SIZING_GENES
    assert not (permit.genes & STRATEGY_GENES)


def test_a_certificate_covers_a_resize_and_not_a_rethink():
    lock = WalkForwardLock(LockConfig())
    lock.phase = Phase.SUICIDE_FUND
    lock.certificate = strategy_fingerprint(BASE_GENOME)

    resized = replace(BASE_GENOME, position_pct=0.33)
    assert lock.permit(resized), "sizing may move under the certificate"

    rethought = replace(BASE_GENOME, trend_bias=0.05)
    refusal = lock.permit(rethought)
    assert not refusal
    assert "licence is void" in refusal.reason


def test_a_killed_genome_stays_killed_and_cannot_be_resized_back_to_life():
    lock = WalkForwardLock(LockConfig())
    lock.phase = Phase.CRUCIBLE
    lock.kill(BASE_GENOME, "failed the gauntlet")

    assert not lock.permit(BASE_GENOME)
    lock.phase = Phase.CRUCIBLE  # even if something puts it back in training
    resized = replace(BASE_GENOME, position_pct=0.45, leverage_cap=0.5)
    refusal = lock.permit(resized)
    assert not refusal
    assert "graveyard" in refusal.reason


def test_a_killed_genome_cannot_be_put_through_the_pipeline_again(feed):
    lock = WalkForwardLock(LockConfig(stress_runs=2))
    lock.kill(BASE_GENOME, "already failed")
    report = lock.prove(BASE_GENOME, feed, feed, verbose=False)
    assert report.killed
    assert not report.passed
    assert len(report.phases) == 1


# =========================================================================
# what the lock stops the engine doing
# =========================================================================
def test_online_evolution_with_no_lock_attached_refuses_itself(feed):
    engine = GodBrokerEngine(BASE_GENOME, seed=4, online_evolution=True, lock=None)
    engine.run(feed)
    assert engine.mutations == 0
    assert engine.refusals
    assert "no walk-forward lock" in engine.refusals[0]


def test_strict_mode_turns_a_refusal_into_a_stop(feed):
    """Off by default: a live book that halts stops managing what it holds."""
    engine = GodBrokerEngine(
        BASE_GENOME, seed=4, online_evolution=True, lock=None, strict_evolution=True
    )
    with pytest.raises(MutationRefused):
        engine.run(feed)


def test_a_frozen_phase_produces_refusals_and_no_mutations(feed):
    lock = WalkForwardLock(LockConfig())
    lock.phase = Phase.PAPER
    engine = GodBrokerEngine(BASE_GENOME, seed=4, online_evolution=True, lock=lock)
    engine.run(feed)
    assert engine.mutations == 0
    assert engine.genome.fingerprint() == BASE_GENOME.fingerprint()
    assert engine.refusals


def test_on_real_money_the_sizing_moves_and_the_thesis_does_not(feed):
    lock = WalkForwardLock(LockConfig())
    lock.phase = Phase.SUICIDE_FUND
    lock.certificate = strategy_fingerprint(BASE_GENOME)

    engine = GodBrokerEngine(
        BASE_GENOME, capital=100.0, seed=4, online_evolution=True, lock=lock
    )
    engine.run(feed)
    assert engine.mutations > 0, "sizing was supposed to be allowed to move here"
    assert strategy_fingerprint(engine.genome) == strategy_fingerprint(BASE_GENOME)
    for name in STRATEGY_GENES:
        assert getattr(engine.genome, name) == getattr(BASE_GENOME, name)


# =========================================================================
# the pipeline
# =========================================================================
def test_phase_one_never_hands_training_a_bar_from_the_holdout(monkeypatch):
    """The load-bearing test: how many bars the training runs were given."""
    import hive_mind.lock as lock_module

    history = MarketFeed(seed=6, plan=[("calm_bull", 300), ("chop", 200)])
    split = int(len(history) * 0.8)
    seen: list = []

    real = lock_module.backtest

    def spy(genome, feed, **kwargs):
        seen.append(len(list(feed)))
        return real(genome, feed, **kwargs)

    monkeypatch.setattr(lock_module, "backtest", spy)
    lock = WalkForwardLock(LockConfig(generations=2, population=3), seed=6)
    lock._crucible(BASE_GENOME, history, lambda *a, **k: None)

    assert seen, "the crucible ran nothing"
    assert set(seen) == {split}, f"training saw {sorted(set(seen))}, not just {split}"
    assert max(seen) < len(history)


def test_the_gauntlet_reads_only_the_holdout(monkeypatch):
    import hive_mind.lock as lock_module

    history = MarketFeed(seed=6, plan=[("calm_bull", 300), ("chop", 200)])
    split = int(len(history) * 0.8)
    seen: list = []
    real = lock_module.backtest

    def spy(genome, feed, **kwargs):
        seen.append(len(list(feed)))
        return real(genome, feed, **kwargs)

    monkeypatch.setattr(lock_module, "backtest", spy)
    lock = WalkForwardLock(LockConfig(), seed=6)
    lock._gauntlet(BASE_GENOME, history, lambda *a, **k: None)
    assert seen == [len(history) - split]


def test_a_gauntlet_failure_kills_the_genome_permanently():
    history = MarketFeed(seed=8, plan=[("grind_down", 300), ("crash", 60), ("chop", 140)])
    forward = MarketFeed.scenario("bear_2022", seed=99)
    lock = WalkForwardLock(LockConfig(generations=1, population=3, stress_runs=2), seed=8)
    report = lock.prove(BASE_GENOME, history, forward, verbose=False)

    if not report.phases[1].passed:
        assert report.killed
        assert lock.phase is Phase.DEAD
        assert not lock.permit(report.genome_out)
        # And a second attempt does not get a second chance.
        again = lock.prove(report.genome_out, history, forward, verbose=False)
        assert len(again.phases) == 1 and not again.passed


def test_the_stress_plan_is_deterministic_and_covers_every_scenario():
    lock = WalkForwardLock(LockConfig(stress_runs=12))
    plan = lock.stress_plan()
    assert len(plan) == 12
    assert plan == WalkForwardLock(LockConfig(stress_runs=12)).stress_plan()
    from hive_mind.market import SCENARIOS

    assert {name for name, _ in plan} == set(SCENARIOS)
    assert len({seed for _, seed in plan}) == 12  # no scenario is run twice identically


def test_the_paper_phase_checks_that_nothing_moved(monkeypatch):
    """Phase 3 asserts the freeze about the run that just happened."""
    lock = WalkForwardLock(LockConfig(paper_min_return_pct=-100.0, paper_min_sharpe=-99.0))
    lock.phase = Phase.PAPER
    forward = MarketFeed(seed=12, plan=[("calm_bull", 120)])
    phase = lock._paper(BASE_GENOME, forward, lambda *a, **k: None)
    assert phase.passed
    assert "frozen" in phase.detail
