"""The brain: memory, evolution, learning — and the backtester underneath."""

from __future__ import annotations

import json
from decimal import Decimal

from src.money import D, ZERO
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
# the hurdle — was it worth doing, not merely did it go up
# =========================================================================
def test_a_firm_that_loses_to_its_own_universe_scores_negative(feed):
    """The gap this closes.

    `return_pct - maxDD/2` scored a firm that made +0.5% while the thing it
    traded made +10% as a *positive* result, and the evolver selected for it.
    Every fitness this repository reported measured "did it go up", never "was
    it worth doing".
    """
    from src.trading.backtest import BacktestResult

    lagging = BacktestResult(
        firm_key="a", return_pct=D("0.5"), max_drawdown_pct=ZERO,
        closed_trades=40, benchmark_pct=D("10"),
    )
    assert lagging.return_pct > 0
    assert lagging.excess_pct == D("-9.50")
    assert lagging.fitness < 0


def test_both_hurdles_not_either(feed):
    """Taken from hive_mind/lock.py:670 — beating the index alone is cleared
    by sitting in cash through a falling market, and beating cash alone was
    never enough to justify the risk of being here."""
    from src.trading.backtest import BacktestResult

    # Beat a falling universe, still lost money. Cash is the binding hurdle.
    beat_the_market_lost_money = BacktestResult(
        firm_key="a", return_pct=D("-5"), max_drawdown_pct=ZERO,
        closed_trades=40, benchmark_pct=D("-20"), cash_hurdle_pct=ZERO,
    )
    assert beat_the_market_lost_money.hurdle_pct == ZERO
    assert beat_the_market_lost_money.fitness < 0

    # Beat cash, lost to the universe. Now the benchmark is the binding one.
    made_money_lost_to_the_market = BacktestResult(
        firm_key="b", return_pct=D("5"), max_drawdown_pct=ZERO,
        closed_trades=40, benchmark_pct=D("20"), cash_hurdle_pct=ZERO,
    )
    assert made_money_lost_to_the_market.hurdle_pct == D("20")
    assert made_money_lost_to_the_market.fitness < 0

    # Clearing both is the only way through.
    cleared = BacktestResult(
        firm_key="c", return_pct=D("25"), max_drawdown_pct=ZERO,
        closed_trades=40, benchmark_pct=D("20"), cash_hurdle_pct=D("2"),
    )
    assert cleared.fitness > 0


def test_an_unpriceable_benchmark_falls_back_to_cash_and_never_to_zero(feed):
    """A benchmark nobody could measure must not quietly become a benchmark of
    nothing — that is the bug the hurdle exists to close, reintroduced by the
    back door."""
    from src.trading.backtest import BacktestResult

    result = BacktestResult(
        firm_key="a", return_pct=D("3"), max_drawdown_pct=ZERO, closed_trades=40,
        benchmark_pct=None, cash_hurdle_pct=D("4"), hurdle_note="could not price WIF",
    )
    assert result.hurdle_pct == D("4")        # cash, not zero
    assert result.fitness < 0
    assert "could not price" in result.summary()


def test_a_universe_that_only_partly_prices_is_refused_not_averaged(feed, trading_config):
    """Pricing two legs of four and calling it the benchmark is a survivorship
    filter, and it hands the genome a lower bar than the thing it traded."""
    from src.trading.backtest import Backtester

    bt = Backtester(trading_config)
    pct, note = bt._hold_pct({"SPY": D(100)}, {"SPY": D(110)}, ["SPY", "GHOST"])
    assert pct is None
    assert "GHOST" in note


def test_two_results_with_different_hurdles_refuse_to_be_subtracted(feed):
    """One scored against a priced benchmark and one against the cash
    fallback are measured off different bars; the difference would read the
    missing benchmark as performance."""
    import pytest

    from src.trading.backtest import BacktestResult

    priced = BacktestResult(firm_key="a", bars=100, return_pct=D("5"),
                            closed_trades=40, benchmark_pct=D("3"))
    unpriced = BacktestResult(firm_key="b", bars=100, return_pct=D("5"),
                              closed_trades=40, benchmark_pct=None)
    assert not priced.comparable_with(unpriced)
    with pytest.raises(ValueError, match="cash fallback"):
        priced.minus(unpriced)
    # Same provenance still subtracts fine.
    other = BacktestResult(firm_key="c", bars=100, return_pct=D("9"),
                           closed_trades=40, benchmark_pct=D("3"))
    assert other.minus(priced) > 0


def test_the_benchmark_is_the_firms_own_universe_not_spy(feed, trading_config):
    """A crypto desk that made 5% while BTC made 50% is a bad genome, and
    measuring it against an equity index would answer a question nobody
    asked."""
    from src.trading.backtest import Backtester

    bt = Backtester(trading_config)
    result = bt.run("crypto", ["BTC-USD"], MarketData(feed, ["BTC-USD"]),
                    genome=BASE_GENOME)
    assert result.benchmark_pct is not None, result.hurdle_note

    def move(symbol):
        """That symbol's own move across the scored window, entry costs and
        all — the warmup is 90 bars, so the hold starts at bar 90."""
        closes = MarketData(feed, [symbol]).closes(symbol)
        entry = closes[90] * (D(1) + D(trading_config.data.slippage_bps) / D(10_000))
        shares = (D(1) - D(trading_config.data.fee_bps) / D(10_000)) / entry
        return (shares * closes[-1] - D(1)) * D(100)

    btc, spy = move("BTC-USD"), move("SPY")
    assert abs(result.benchmark_pct - btc) < D("0.01"), "it did not hold BTC"
    # And the two really are different, or the test proves nothing.
    assert abs(btc - spy) > D(5)
    assert abs(result.benchmark_pct - btc) < abs(result.benchmark_pct - spy)


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
        # Winning the fit is necessary and not sufficient. A mutant can beat
        # the incumbent on the fitted bars and still not be adopted, and the
        # commonest reason on a short feed is that there is no holdout left to
        # check it against: 180 daily bars cannot spare a 90-bar warmup, a
        # 150-bar purge gap and a tail as well. Asserting the winner must have
        # lost made this test a claim about the fixture's length rather than
        # about promotion, and it broke the moment the gap became real.
        assert (generation.winner.fitness <= incumbent.fitness
                or generation.refused), \
            "a winning mutant was not promoted and no reason was recorded"


def test_promotion_can_be_switched_off(store, firm_record, feed, trading_config):
    from dataclasses import replace

    from src.trading.config import BrainConfig

    config = replace(trading_config, brain=BrainConfig(promote_winners=False))
    before = store.require_firm_by_id(firm_record.id).genome
    Evolver(store, config).evolve(firm_record, MarketData(feed, ["SPY"]), generation=1)
    assert store.require_firm_by_id(firm_record.id).genome == before
