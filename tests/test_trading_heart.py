"""The conscience — six foundations, and what each one actually refuses."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.money import D
from src.trading.config import HeartConfig
from src.trading.heart.conscience import FOUNDATIONS, Conscience
from src.trading.heart.ethics import Heart
from src.trading.models import (
    EthicsVerdict,
    FirmRecord,
    Position,
    ProposalStatus,
    Side,
    TradeProposal,
)


def firm(**overrides):
    defaults = dict(
        firm_key="f",
        allocation=Decimal("100000"),
        initial_allocation=Decimal("100000"),
        cash=Decimal("100000"),
        universe=["SPY", "QQQ"],
        venue="paper",
        id=1,
    )
    defaults.update(overrides)
    return FirmRecord(**defaults)


def proposal(**overrides):
    defaults = dict(
        firm_id=1,
        symbol="SPY",
        side=Side.BUY.value,
        quantity="10",
        reference_price="100",
        as_of=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


def finding(review, name):
    return next(f for f in review.findings if f.foundation == name)


def test_every_foundation_reports_on_every_trade(market):
    market.seek(150)
    review = Conscience().review(proposal(), firm(), [], market)
    assert [f.foundation for f in review.findings] == list(FOUNDATIONS)
    assert review.verdict in {v.value for v in EthicsVerdict}


def test_a_normal_trade_is_allowed(market):
    market.seek(150)
    review = Conscience().review(proposal(), firm(), [], market)
    assert review.verdict == EthicsVerdict.ALLOW.value
    assert review.reason == "no foundation objected"


# -- care -----------------------------------------------------------------
def test_care_warns_on_concentration(market):
    market.seek(150)
    review = Conscience(HeartConfig(concentration_warn_pct=D("5"))).review(
        proposal(quantity="100"), firm(), [], market
    )
    assert finding(review, "care").warns
    assert review.verdict == EthicsVerdict.WARN.value


def test_care_blocks_a_trade_bigger_than_the_whole_firm(market):
    market.seek(150)
    review = Conscience().review(
        proposal(quantity="100"), firm(cash=Decimal("500")), [], market
    )
    assert finding(review, "care").blocks
    assert review.verdict == EthicsVerdict.BLOCK.value


def test_care_never_objects_to_an_exit(market):
    market.seek(150)
    held = [Position(firm_id=1, symbol="SPY", quantity=D(1000), avg_price=D(1))]
    review = Conscience().review(
        proposal(side=Side.SELL.value, quantity="1000"), firm(), held, market
    )
    assert not finding(review, "care").blocks
    assert not finding(review, "care").warns


# -- fairness -------------------------------------------------------------
def test_fairness_blocks_a_wash_trade(market):
    market.seek(150)
    earlier = proposal(side=Side.SELL.value)
    review = Conscience().review(proposal(), firm(), [], market, recent_proposals=[earlier])
    assert finding(review, "fairness").blocks
    assert "wash trade" in review.reason


def test_fairness_blocks_churn(market):
    market.seek(150)
    history = []
    for index in range(12):
        history.append(
            proposal(
                side=Side.BUY.value if index % 2 == 0 else Side.SELL.value,
                as_of=datetime(2026, 4, index + 1, tzinfo=timezone.utc),
            )
        )
    review = Conscience().review(proposal(), firm(), [], market, recent_proposals=history)
    assert finding(review, "fairness").blocks
    assert "churn" in review.reason


# -- loyalty --------------------------------------------------------------
def test_loyalty_blocks_a_symbol_outside_the_mandate(market):
    market.seek(150)
    review = Conscience().review(proposal(symbol="BTC-USD"), firm(), [], market)
    assert finding(review, "loyalty").blocks
    assert "mandated universe" in review.reason


# -- authority ------------------------------------------------------------
def test_authority_blocks_a_paused_firm_from_buying(market):
    market.seek(150)
    review = Conscience().review(proposal(), firm(status="paused"), [], market)
    assert finding(review, "authority").blocks


def test_authority_lets_a_paused_firm_sell(market):
    market.seek(150)
    held = [Position(firm_id=1, symbol="SPY", quantity=D(10), avg_price=D(1))]
    review = Conscience().review(
        proposal(side=Side.SELL.value), firm(status="paused"), held, market
    )
    assert not finding(review, "authority").blocks


def test_authority_blocks_a_killed_firm_entirely(market):
    market.seek(150)
    review = Conscience().review(
        proposal(side=Side.SELL.value), firm(status="killed"), [], market
    )
    assert finding(review, "authority").blocks


def test_authority_warns_on_a_live_venue(market):
    market.seek(150)
    review = Conscience().review(proposal(), firm(venue="alpaca"), [], market)
    assert finding(review, "authority").warns


# -- sanctity -------------------------------------------------------------
def test_sanctity_blocks_a_restricted_symbol(market):
    market.seek(150)
    config = HeartConfig(restricted_symbols=("SPY",))
    review = Conscience(config).review(proposal(), firm(), [], market)
    assert finding(review, "sanctity").blocks
    assert "restricted list" in review.reason


# -- liberty --------------------------------------------------------------
def test_liberty_blocks_a_position_that_cannot_be_exited(market):
    market.seek(150)
    review = Conscience().review(proposal(quantity="10000000"), firm(), [], market)
    assert finding(review, "liberty").blocks
    assert "exited quickly" in review.reason


def test_liberty_warns_before_it_blocks(market):
    market.seek(150)
    typical = sum((b.volume for b in market.history("SPY", 20)), D(0)) / D(20)
    review = Conscience().review(
        proposal(quantity=str(typical * D("0.05"))), firm(), [], market
    )
    assert finding(review, "liberty").warns


# -- aggregation ----------------------------------------------------------
def test_strict_mode_turns_warnings_into_blocks(market):
    market.seek(150)
    config = HeartConfig(concentration_warn_pct=D("1"), block_on_warn=True)
    review = Conscience(config).review(proposal(quantity="100"), firm(), [], market)
    assert review.verdict == EthicsVerdict.BLOCK.value


def test_a_block_beats_a_warning(market):
    market.seek(150)
    config = HeartConfig(concentration_warn_pct=D("1"), restricted_symbols=("SPY",))
    review = Conscience(config).review(proposal(), firm(), [], market)
    assert review.verdict == EthicsVerdict.BLOCK.value


# -- the heart ------------------------------------------------------------
def test_the_verdict_is_stamped_on_the_proposal(store, firm_record, market, trading_config):
    market.seek(150)
    heart = Heart(store, trading_config)
    p = proposal(firm_id=firm_record.id, symbol="BTC-USD")
    heart.consider(p, firm(id=firm_record.id), [], market)
    assert p.ethics_verdict == EthicsVerdict.BLOCK.value
    assert p.status == ProposalStatus.BLOCKED.value
    assert "loyalty" in p.ethics_reason


def test_non_allow_verdicts_are_recorded_by_compliance(store, firm_record, market, trading_config):
    market.seek(150)
    heart = Heart(store, trading_config)
    heart.consider(
        proposal(firm_id=firm_record.id, symbol="BTC-USD"), firm(id=firm_record.id), [], market
    )
    events = store.events(event_type="ethics")
    assert events and "BLOCK" in events[0]["detail"]
    assert heart.compliance.report()["blocked"] == 1


def test_clean_trades_do_not_fill_the_event_log(store, firm_record, market, trading_config):
    market.seek(150)
    heart = Heart(store, trading_config)
    heart.consider(proposal(firm_id=firm_record.id), firm(id=firm_record.id), [], market)
    assert store.events(event_type="ethics") == []


def test_the_session_log_feeds_the_fairness_check(store, firm_record, market, trading_config):
    market.seek(150)
    heart = Heart(store, trading_config)
    held = [Position(firm_id=firm_record.id, symbol="SPY", quantity=D(50), avg_price=D(1))]
    heart.consider(
        proposal(firm_id=firm_record.id, side=Side.SELL.value),
        firm(id=firm_record.id),
        held,
        market,
    )
    second = proposal(firm_id=firm_record.id)
    heart.consider(second, firm(id=firm_record.id), held, market)
    assert second.ethics_verdict == EthicsVerdict.BLOCK.value  # wash trade

    heart.new_session()
    third = proposal(firm_id=firm_record.id)
    heart.consider(third, firm(id=firm_record.id), held, market)
    assert third.ethics_verdict == EthicsVerdict.ALLOW.value
