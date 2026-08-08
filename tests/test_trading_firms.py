"""The pod: analysts, the debate, sizing, and the risk manager's refusals."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.money import D, money
from src.trading.config import FirmDefaults
from src.trading.firms.analysts import (
    ANALYSTS,
    MacroAnalyst,
    OnChainAnalyst,
    SentimentAnalyst,
    TechnicalAnalyst,
    build_analysts,
)
from src.trading.firms.firm import Firm
from src.trading.firms.researchers import DebateRoom
from src.trading.firms.risk_manager import RiskManager
from src.trading.firms.spec import FirmSpec, FirmSpecError, load_firm_specs, parse_simple_yaml
from src.trading.firms.trader import Trader
from src.trading.models import FirmRecord, Position, RiskVerdict, Side, Signal


# =========================================================================
# analysts
# =========================================================================
def test_every_analyst_stays_silent_without_enough_history(market):
    market.seek(3)
    for factory in ANALYSTS.values():
        signal = factory().analyse("SPY", market, {})
        assert signal.is_silent, f"{factory.__name__} spoke without data"
        assert "insufficient" in signal.note.lower()


def test_analysts_produce_bounded_scores(market):
    market.seek(150)
    for factory in ANALYSTS.values():
        signal = factory().analyse("SPY", market, {})
        assert D(-100) <= signal.score <= D(100)
        assert D(0) <= signal.confidence <= D(100)


def test_analysts_are_deterministic(market):
    market.seek(120)
    first = TechnicalAnalyst().analyse("SPY", market, {"trend_bias": 60})
    second = TechnicalAnalyst().analyse("SPY", market, {"trend_bias": 60})
    assert (first.score, first.confidence) == (second.score, second.confidence)


def test_the_trend_bias_gene_changes_the_reading(market):
    market.seek(150)
    momentum = TechnicalAnalyst().analyse("SPY", market, {"trend_bias": 100})
    reversion = TechnicalAnalyst().analyse("SPY", market, {"trend_bias": 0})
    assert momentum.score != reversion.score


def test_proxy_analysts_label_themselves_as_proxies(market):
    market.seek(150)
    assert "proxy" in SentimentAnalyst().analyse("SPY", market, {}).note
    assert "proxy" in OnChainAnalyst().analyse("BTC-USD", market, {}).note


def test_macro_reads_the_whole_universe(market):
    market.seek(150)
    signal = MacroAnalyst().analyse("SPY", market, {})
    assert "breadth" in signal.note


def test_unknown_analysts_are_refused_at_config_time():
    with pytest.raises(ValueError) as exc:
        build_analysts(["technical", "astrology"])
    assert "astrology" in str(exc.value)


def test_analyst_names_are_normalised():
    built = build_analysts(["Technical Analyst", "on-chain", "Sentiment_Analyst"])
    assert [type(a).__name__ for a in built] == [
        "TechnicalAnalyst",
        "OnChainAnalyst",
        "SentimentAnalyst",
    ]


# =========================================================================
# the debate
# =========================================================================
def signal(name, score, confidence=100):
    return Signal(name, "SPY", D(score), D(confidence), f"{name} says {score}")


def test_one_sided_evidence_wins_outright():
    result = DebateRoom().debate("SPY", [signal("technical", 80), signal("macro", 60)])
    assert result.winner == "bull"
    assert result.confidence == D("100.00")
    assert result.side is Side.BUY


def test_a_close_debate_produces_low_confidence():
    result = DebateRoom().debate("SPY", [signal("technical", 55), signal("macro", -45)])
    assert result.winner == "bull"
    assert result.confidence < D(20)


def test_a_tie_is_not_a_decision():
    result = DebateRoom().debate("SPY", [signal("technical", 50), signal("macro", -50)])
    assert result.winner == "none"
    assert result.side is None
    assert not result.is_decided


def test_silent_analysts_are_not_votes():
    result = DebateRoom().debate(
        "SPY", [signal("technical", 60), signal("macro", -90, confidence=0)]
    )
    assert result.winner == "bull"
    assert "No bear evidence" in result.bear_case


def test_each_case_cites_only_its_own_side():
    result = DebateRoom().debate("SPY", [signal("technical", 70), signal("macro", -30)])
    assert "technical" in result.bull_case and "macro" not in result.bull_case
    assert "macro" in result.bear_case and "technical" not in result.bear_case


# =========================================================================
# the trader
# =========================================================================
def make_firm(**overrides):
    defaults = dict(
        firm_key="f",
        allocation=Decimal("100000"),
        initial_allocation=Decimal("100000"),
        cash=Decimal("100000"),
        risk_limit=Decimal("0.02"),
        universe=["SPY"],
        id=1,
    )
    defaults.update(overrides)
    return FirmRecord(**defaults)


def test_conviction_sizes_the_position():
    trader = Trader(FirmDefaults())
    firm = make_firm()
    strong = DebateRoom().debate("SPY", [signal("technical", 90)])
    weak = DebateRoom().debate("SPY", [signal("technical", 60), signal("macro", -30)])

    big = trader.propose(firm, strong, [], D(100), [])
    small = trader.propose(firm, weak, [], D(100), [])
    assert big is not None
    if small is not None:
        assert small.quantity < big.quantity


def test_a_narrow_win_does_not_open_risk():
    trader = Trader(FirmDefaults())
    narrow = DebateRoom().debate("SPY", [signal("technical", 52), signal("macro", -48)])
    assert trader.propose(make_firm(), narrow, [], D(100), []) is None


def test_selling_needs_a_position_but_not_conviction():
    trader = Trader(FirmDefaults())
    bear = DebateRoom().debate("SPY", [signal("macro", -80)])
    assert trader.propose(make_firm(), bear, [], D(100), []) is None

    held = Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(90))
    proposal = trader.propose(make_firm(), bear, [], D(100), [held])
    assert proposal is not None
    assert proposal.side == Side.SELL.value
    assert proposal.quantity == D(10)


# =========================================================================
# the risk manager
# =========================================================================
def test_the_risk_limit_caps_a_new_position():
    manager = RiskManager(FirmDefaults())
    firm = make_firm()  # 2% of 100k = 2000
    decision = manager.review(firm, "SPY", Side.BUY, D(100), D(100), [], D(100000))
    assert decision.verdict == RiskVerdict.RESIZE.value
    assert decision.quantity == D(20)  # 2000 / 100
    assert "risk limit" in decision.reason


def test_the_position_cap_counts_what_is_already_held():
    manager = RiskManager(FirmDefaults(max_position_pct=D("0.10")))
    firm = make_firm()
    held = Position(firm_id=1, symbol="SPY", quantity=D(95), avg_price=D(100))
    decision = manager.review(firm, "SPY", Side.BUY, D(20), D(100), [held], D(100000))
    assert decision.verdict == RiskVerdict.RESIZE.value
    assert decision.quantity == D(5)  # cap 10000, already 9500


def test_a_full_position_is_blocked_not_resized():
    manager = RiskManager(FirmDefaults(max_position_pct=D("0.10")))
    held = Position(firm_id=1, symbol="SPY", quantity=D(100), avg_price=D(100))
    decision = manager.review(make_firm(), "SPY", Side.BUY, D(5), D(100), [held], D(100000))
    assert decision.blocked
    assert "position cap" in decision.reason


def test_the_cash_floor_is_never_spent():
    manager = RiskManager(FirmDefaults(cash_floor_pct=D("0.05")))
    firm = make_firm(cash=Decimal("6000"))  # floor is 5000, so 1000 is spendable
    decision = manager.review(firm, "SPY", Side.BUY, D(50), D(100), [], D(100000))
    assert decision.quantity == D(10)
    assert "cash floor" in decision.reason


def test_a_firm_at_its_cash_floor_cannot_buy():
    manager = RiskManager(FirmDefaults(cash_floor_pct=D("0.05")))
    firm = make_firm(cash=Decimal("5000"))
    assert manager.review(firm, "SPY", Side.BUY, D(1), D(100), [], D(100000)).blocked


def test_the_position_count_limit_applies_to_new_names_only():
    manager = RiskManager(FirmDefaults(max_positions=2))
    held = [
        Position(firm_id=1, symbol="QQQ", quantity=D(1), avg_price=D(100)),
        Position(firm_id=1, symbol="IWM", quantity=D(1), avg_price=D(100)),
    ]
    assert manager.review(make_firm(), "SPY", Side.BUY, D(1), D(100), held, D(100000)).blocked
    # Adding to a name already held is not a new position.
    assert not manager.review(make_firm(), "QQQ", Side.BUY, D(1), D(100), held, D(100000)).blocked


def test_reducing_a_position_only_checks_solvency():
    manager = RiskManager(FirmDefaults(cash_floor_pct=D("1.0")))  # no cash at all
    held = Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(100))
    decision = manager.review(make_firm(cash=D(0)), "SPY", Side.SELL, D(10), D(100), [held], D(1000))
    assert decision.verdict == RiskVerdict.ALLOW.value


def test_selling_more_than_held_is_cut_to_the_holding():
    manager = RiskManager(FirmDefaults())
    held = Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(100))
    decision = manager.review(make_firm(), "SPY", Side.SELL, D(50), D(100), [held], D(100000))
    assert decision.verdict == RiskVerdict.RESIZE.value
    assert decision.quantity == D(10)


def test_shorting_is_refused():
    manager = RiskManager(FirmDefaults())
    decision = manager.review(make_firm(), "SPY", Side.SELL, D(10), D(100), [], D(100000))
    assert decision.blocked
    assert "short" in decision.reason


def test_a_firm_with_no_allocation_cannot_trade():
    manager = RiskManager(FirmDefaults())
    firm = make_firm(allocation=Decimal("0"))
    assert manager.review(firm, "SPY", Side.BUY, D(1), D(100), [], D(0)).blocked


# =========================================================================
# the pod
# =========================================================================
def test_a_firm_produces_reviewed_proposals(market, trading_config):
    market.seek(150)
    firm = Firm(
        make_firm(universe=["SPY", "QQQ"]),
        analysts=build_analysts(["technical", "sentiment", "macro"]),
        limits=trading_config.firm,
        kill_config=trading_config.kill,
    )
    proposals = firm.propose(market, [])
    for proposal in proposals:
        assert proposal.risk_verdict in {v.value for v in RiskVerdict}
        assert proposal.symbol in firm.universe


def test_a_paused_firm_may_exit_but_not_enter(market, trading_config):
    market.seek(150)
    record = make_firm(universe=["SPY", "QQQ"], status="paused")
    firm = Firm(record, build_analysts(["technical", "sentiment"]), trading_config.firm)
    held = [Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(1))]
    for proposal in firm.propose(market, held):
        assert proposal.side == Side.SELL.value


def test_a_killed_firm_proposes_nothing(market, trading_config):
    market.seek(150)
    record = make_firm(universe=["SPY"], status="killed")
    firm = Firm(record, build_analysts(["technical"]), trading_config.firm)
    assert firm.propose(market, []) == []


def test_equity_is_cash_plus_marks(market, trading_config):
    market.seek(150)
    firm = Firm(make_firm(cash=Decimal("1000")), limits=trading_config.firm)
    held = [Position(firm_id=1, symbol="SPY", quantity=D(2), avg_price=D(50))]
    # Equity is quantised to cents: it is a ledger figure, not a mid-price.
    assert firm.equity(market, held) == money(D(1000) + D(2) * market.mark("SPY"))


# =========================================================================
# the spec file
# =========================================================================
def test_specs_load_from_yaml(firms_yaml, trading_config):
    specs = load_firm_specs(firms_yaml, trading_config)
    alpha = next(s for s in specs if s.firm_key == "alpha")
    assert alpha.allocation == D(50000)
    assert alpha.universe == ("SPY", "QQQ")
    assert alpha.analysts == ("technical", "sentiment")
    assert alpha.genome["trend_bias"] == 70
    beta = next(s for s in specs if s.firm_key == "beta")
    assert beta.universe == ("BTC-USD",)  # inline list form


def test_a_firm_without_a_universe_is_refused():
    with pytest.raises(FirmSpecError) as exc:
        FirmSpec.from_mapping("x", {"name": "X"})
    assert "universe" in str(exc.value)


def test_researcher_seats_are_not_treated_as_analysts():
    spec = FirmSpec.from_mapping(
        "x",
        {
            "universe": ["SPY"],
            "analysts": ["Technical Analyst", "Bull Researcher", "Trader", "Risk Manager"],
        },
    )
    assert spec.analysts == ("Technical Analyst",)


def test_a_missing_config_says_where_it_looked(tmp_path, trading_config):
    with pytest.raises(FirmSpecError) as exc:
        load_firm_specs(tmp_path / "nope.yaml", trading_config)
    assert "nope.yaml" in str(exc.value)


# =========================================================================
# the fallback YAML parser (used when PyYAML is absent)
# =========================================================================
def test_the_fallback_parser_reads_the_project_config():
    parsed = parse_simple_yaml(
        "firms:\n"
        "  alpha:\n"
        "    name: \"Alpha\"        # a comment\n"
        "    risk_limit: 0.02\n"
        "    live: true\n"
        "    note:\n"
        "    universe:\n"
        "      - SPY\n"
        "      - QQQ\n"
        "    inline: [A, B]\n"
    )
    alpha = parsed["firms"]["alpha"]
    assert alpha["name"] == "Alpha"
    assert alpha["risk_limit"] == "0.02"  # kept exact, never a binary float
    assert alpha["live"] is True
    assert alpha["note"] is None
    assert alpha["universe"] == ["SPY", "QQQ"]
    assert alpha["inline"] == ["A", "B"]


def test_the_fallback_parser_refuses_what_it_cannot_read():
    with pytest.raises(FirmSpecError):
        parse_simple_yaml("firms:\n\tbad: 1\n")
    with pytest.raises(FirmSpecError):
        parse_simple_yaml("- just\n- a list\n")
    with pytest.raises(FirmSpecError):
        parse_simple_yaml("no colon here\n")
