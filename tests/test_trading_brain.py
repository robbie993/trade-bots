"""The brain: memory, evolution, learning — and the backtester underneath."""

from __future__ import annotations

import json
from decimal import Decimal

from src.money import D
from src.trading.backtest import Backtester
from src.trading.brain.evolver import BASE_GENOME, GENES, Evolver
from src.trading.brain.learning import Learner, Lesson
from src.trading.brain.memory import AgentMemory
from src.trading.brokerage.evaluator import Scorecard
from src.trading.data.market_data import MarketData
from src.trading.models import Fill, Side, TradeProposal


def fill(firm_id, symbol="SPY", pnl="0", side=Side.BUY):
    f = Fill(firm_id=firm_id, symbol=symbol, side=side.value, quantity="10", price="100", fee="1")
    f.realized_pnl = D(pnl)
    return f


# =========================================================================
# memory
# =========================================================================
def test_a_fill_is_remembered_with_its_outcome(store, firm_record):
    memory = AgentMemory(store)
    memory.remember_fill(fill(firm_record.id, pnl="50"))
    memory.remember_fill(fill(firm_record.id, pnl="-20"))
    memory.remember_fill(fill(firm_record.id, pnl="0"))
    outcomes = [m.outcome for m in memory.recall(symbol="SPY")]
    assert sorted(outcomes) == ["flat", "loss", "win"]


def test_the_rationale_is_stored_with_the_trade(store, firm_record):
    memory = AgentMemory(store)
    proposal = TradeProposal(
        firm_id=firm_record.id, symbol="SPY", rationale="Bull case won 9 to 1"
    )
    memory.remember_fill(fill(firm_record.id), proposal)
    assert "Bull case won 9 to 1" in memory.recall(symbol="SPY")[0].summary


def test_recall_filters_by_symbol_firm_and_outcome(store, firm_record):
    memory = AgentMemory(store)
    memory.remember_fill(fill(firm_record.id, symbol="SPY", pnl="10"))
    memory.remember_fill(fill(firm_record.id, symbol="QQQ", pnl="-10"))
    assert len(memory.recall(symbol="SPY")) == 1
    assert len(memory.recall(firm_id=firm_record.id)) == 2
    assert len(memory.recall(outcome="loss")) == 1


def test_search_is_keyword_and_therefore_quotable(store, firm_record):
    memory = AgentMemory(store)
    memory.remember("Momentum failed badly in choppy markets", firm_id=firm_record.id)
    assert memory.search("choppy")
    assert not memory.search("nonexistent phrase")


def test_the_track_record_totals_a_symbol(store, firm_record):
    memory = AgentMemory(store)
    for pnl in ("100", "-40", "60"):
        memory.remember_fill(fill(firm_record.id, pnl=pnl))
    record = memory.track_record("SPY", firm_record.id)
    assert record["trades"] == 3
    assert record["wins"] == 2 and record["losses"] == 1
    assert record["net"] == Decimal("120.00")
    assert record["worst"] == Decimal("-40.00")


# =========================================================================
# learning
# =========================================================================
def test_a_losing_symbol_produces_a_lesson(store, firm_record, trading_config):
    memory = AgentMemory(store)
    for _ in range(6):
        memory.remember_fill(fill(firm_record.id, pnl="-50"))
    lessons = Learner(store, memory, trading_config).lessons()
    assert any("has lost money" in lesson.statement for lesson in lessons)


def test_a_symbol_below_five_trades_says_nothing(store, firm_record, trading_config):
    memory = AgentMemory(store)
    for _ in range(4):
        memory.remember_fill(fill(firm_record.id, pnl="-50"))
    assert Learner(store, memory, trading_config).lessons() == []


def test_a_lesson_is_recorded_once_even_as_its_evidence_changes(
    store, firm_record, trading_config
):
    memory = AgentMemory(store)
    learner = Learner(store, memory, trading_config)
    for _ in range(6):
        memory.remember_fill(fill(firm_record.id, pnl="-50"))
    assert learner.record(learner.lessons()) == 1
    # More trades: same conclusion, new numbers. It must not be written again.
    for _ in range(4):
        memory.remember_fill(fill(firm_record.id, pnl="-50"))
    assert learner.record(learner.lessons()) == 0


def test_firm_lessons_only_apply_above_the_sample_gate(store, trading_config):
    learner = Learner(store, AgentMemory(store), trading_config)
    unmeasured = Scorecard(
        firm_key="a", firm_id=1, drawdown_pct=D("30"), return_pct=D("5"), sufficient_data=False
    )
    measured = Scorecard(
        firm_key="a", firm_id=1, drawdown_pct=D("30"), return_pct=D("5"), sufficient_data=True
    )
    assert learner._firm_lessons([unmeasured]) == []
    assert learner._firm_lessons([measured])


def test_lessons_carry_their_evidence():
    lesson = Lesson("SPY", "has lost money", "win rate 20%", kind="loser")
    assert "win rate 20%" in str(lesson)
    assert lesson.key == "SPY:loser"


# =========================================================================
# the backtester
# =========================================================================
def test_a_backtest_is_reproducible(feed, trading_config):
    backtester = Backtester(trading_config)
    runs = [
        backtester.run(
            "x", ["SPY", "QQQ"], MarketData(feed, ["SPY", "QQQ"]), genome=BASE_GENOME
        )
        for _ in range(2)
    ]
    assert runs[0].final_equity == runs[1].final_equity
    assert runs[0].trades == runs[1].trades


def test_a_backtest_charges_fees(feed, trading_config):
    result = Backtester(trading_config).run(
        "x", ["SPY", "QQQ"], MarketData(feed, ["SPY", "QQQ"]), genome=BASE_GENOME
    )
    assert result.trades > 0
    assert result.fees > 0


def test_the_backtest_warms_up_before_trading(feed, trading_config):
    backtester = Backtester(trading_config, warmup=90)
    result = backtester.run("x", ["SPY"], MarketData(feed, ["SPY"]), genome=BASE_GENOME)
    assert result.bars == 180 - 90


def test_fitness_discounts_a_tiny_sample(feed, trading_config):
    from src.trading.backtest import BacktestResult

    many = BacktestResult(
        firm_key="a", return_pct=D("20"), max_drawdown_pct=D("4"), closed_trades=40
    )
    few = BacktestResult(
        firm_key="b", return_pct=D("20"), max_drawdown_pct=D("4"), closed_trades=2
    )
    assert many.fitness > few.fitness


def test_fitness_penalises_drawdown(feed):
    from src.trading.backtest import BacktestResult

    calm = BacktestResult(firm_key="a", return_pct=D("10"), max_drawdown_pct=D("2"), closed_trades=20)
    wild = BacktestResult(firm_key="b", return_pct=D("10"), max_drawdown_pct=D("40"), closed_trades=20)
    assert calm.fitness > wild.fitness


# =========================================================================
# evolution
# =========================================================================
def test_mutation_stays_inside_the_gene_ranges(store, trading_config):
    import random

    evolver = Evolver(store, trading_config)
    rng = random.Random(1)
    genome = dict(BASE_GENOME)
    for _ in range(200):
        genome = evolver.mutate(genome, rng)
        for name, (low, high, _) in GENES.items():
            assert low <= D(genome[name]) <= high


def test_normalise_keeps_the_windows_ordered(store, trading_config):
    evolver = Evolver(store, trading_config)
    fixed = evolver.normalise({"fast_window": 90, "slow_window": 30})
    assert int(D(fixed["fast_window"])) < int(D(fixed["slow_window"]))


def test_a_population_always_contains_the_incumbent(store, trading_config):
    population = Evolver(store, trading_config).population(BASE_GENOME, 1)
    assert sum(1 for c in population if c.is_incumbent) == 1
    assert population[0].genome == Evolver(store, trading_config).normalise(BASE_GENOME)


def test_evolution_is_deterministic(store, firm_record, feed, trading_config):
    market = MarketData(feed, ["SPY", "QQQ"])
    first = Evolver(store, trading_config).evolve(firm_record, market, generation=1)
    firm = store.require_firm_by_id(firm_record.id)
    store.update_firm_fields(firm.id, genome=json.dumps(firm_record.genome))
    second = Evolver(store, trading_config).evolve(
        store.require_firm_by_id(firm_record.id), market, generation=1
    )
    assert [c.fitness for c in first.candidates] == [c.fitness for c in second.candidates]


def test_every_candidate_is_written_to_the_genome_table(store, firm_record, feed, trading_config):
    market = MarketData(feed, ["SPY", "QQQ"])
    generation = Evolver(store, trading_config).evolve(firm_record, market, generation=1)
    rows = store.db.query("SELECT * FROM strategy_genomes WHERE firm_id = ?", (firm_record.id,))
    assert len(rows) == len(generation.candidates) == trading_config.brain.population


def test_a_genome_is_promoted_only_when_it_beats_the_incumbent(
    store, firm_record, feed, trading_config
):
    market = MarketData(feed, ["SPY", "QQQ"])
    generation = Evolver(store, trading_config).evolve(firm_record, market, generation=1)
    incumbent = next(c for c in generation.candidates if c.is_incumbent)
    promoted_genome = store.require_firm_by_id(firm_record.id).genome
    if generation.promoted:
        assert generation.winner.fitness > incumbent.fitness
        assert promoted_genome == generation.winner.genome
    else:
        assert generation.winner.fitness <= incumbent.fitness


def test_promotion_can_be_switched_off(store, firm_record, feed, trading_config):
    from dataclasses import replace

    from src.trading.config import BrainConfig

    config = replace(trading_config, brain=BrainConfig(promote_winners=False))
    before = store.require_firm_by_id(firm_record.id).genome
    Evolver(store, config).evolve(firm_record, MarketData(feed, ["SPY"]), generation=1)
    assert store.require_firm_by_id(firm_record.id).genome == before
