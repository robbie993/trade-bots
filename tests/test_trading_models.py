"""Position arithmetic and the Decimal invariants underneath it."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.trading.models import (
    Bar,
    Fill,
    FirmRecord,
    Position,
    Side,
    Signal,
    TradeProposal,
    price,
    qty,
)


def buy(symbol="SPY", quantity="10", at="100", fee="1"):
    return Fill(firm_id=1, symbol=symbol, side=Side.BUY.value, quantity=quantity, price=at, fee=fee)


def sell(symbol="SPY", quantity="10", at="110", fee="1"):
    return Fill(firm_id=1, symbol=symbol, side=Side.SELL.value, quantity=quantity, price=at, fee=fee)


def test_opening_a_position_sets_the_cost_basis():
    position = Position(firm_id=1, symbol="SPY")
    realized = position.apply(buy(quantity="10", at="100"))
    assert realized == Decimal("0")
    assert position.quantity == Decimal("10")
    assert position.avg_price == Decimal("100")


def test_adding_averages_the_cost_basis():
    position = Position(firm_id=1, symbol="SPY")
    position.apply(buy(quantity="10", at="100"))
    position.apply(buy(quantity="10", at="120"))
    assert position.quantity == Decimal("20")
    assert position.avg_price == Decimal("110")


def test_partial_close_realises_only_the_closed_part():
    position = Position(firm_id=1, symbol="SPY")
    position.apply(buy(quantity="10", at="100"))
    realized = position.apply(sell(quantity="4", at="110"))
    assert realized == Decimal("40.00")  # 4 * (110 - 100)
    assert position.quantity == Decimal("6")
    assert position.avg_price == Decimal("100")  # basis unchanged by a sale


def test_full_close_zeroes_the_position_and_keeps_the_pnl():
    position = Position(firm_id=1, symbol="SPY")
    position.apply(buy(quantity="10", at="100"))
    realized = position.apply(sell(quantity="10", at="90"))
    assert realized == Decimal("-100.00")
    assert position.quantity == Decimal("0")
    assert position.avg_price == Decimal("0")
    assert position.realized_pnl == Decimal("-100.00")
    assert position.is_open is False


def test_flipping_through_zero_realises_then_reopens():
    position = Position(firm_id=1, symbol="SPY")
    position.apply(buy(quantity="10", at="100"))
    realized = position.apply(sell(quantity="15", at="110"))
    assert realized == Decimal("100.00")  # only the 10 held were closed
    assert position.quantity == Decimal("-5")
    assert position.avg_price == Decimal("110")  # the remainder opened short


def test_unrealised_pnl_follows_the_mark():
    position = Position(firm_id=1, symbol="SPY")
    position.apply(buy(quantity="10", at="100"))
    assert position.unrealized_pnl(Decimal("105")) == Decimal("50.00")
    assert position.market_value(Decimal("105")) == Decimal("1050.00")


def test_a_flat_position_has_no_unrealised_pnl():
    assert Position(firm_id=1, symbol="SPY").unrealized_pnl(Decimal("999")) == Decimal("0")


def test_money_never_becomes_a_float():
    fill = Fill(firm_id=1, symbol="SPY", side="buy", quantity=0.1, price=0.2, fee=0.3)
    for value in (fill.quantity, fill.price, fill.fee, fill.gross):
        assert isinstance(value, Decimal)
    # 0.1 as a binary float is 0.1000000000000000055511151231257827; coercion
    # goes through str() so what lands in the ledger is what was written.
    assert fill.quantity == Decimal("0.1")


def test_signal_clamps_and_reports_silence():
    loud = Signal("technical", "SPY", score=Decimal("500"), confidence=Decimal("400"))
    assert loud.score == Decimal("100")
    assert loud.confidence == Decimal("100")
    quiet = Signal("technical", "SPY", score=Decimal("80"), confidence=Decimal("0"))
    assert quiet.is_silent
    assert quiet.weighted == Decimal("0")


def test_proposal_is_not_executable_when_blocked():
    proposal = TradeProposal(firm_id=1, symbol="SPY", quantity="10", reference_price="100")
    assert proposal.is_executable
    proposal.ethics_verdict = "block"
    assert not proposal.is_executable
    proposal.ethics_verdict = "allow"
    proposal.risk_verdict = "block"
    assert not proposal.is_executable


def test_proposal_round_trips_through_a_row():
    proposal = TradeProposal(
        firm_id=1,
        symbol="SPY",
        quantity="10",
        reference_price="100.5",
        signals=[Signal("technical", "SPY", Decimal("50"), Decimal("60"), "note")],
    )
    row = proposal.to_row()
    assert row["notional"] == Decimal("1005.00")
    restored = TradeProposal.from_row({**row, "id": 7})
    assert restored.symbol == "SPY"
    assert restored.signals[0]["analyst"] == "technical"


def test_firm_record_parses_json_columns():
    record = FirmRecord.from_row(
        {
            "id": 1,
            "firm_key": "a",
            "genome": '{"trend_bias": 60}',
            "universe": '["SPY", "QQQ"]',
            "allocation": "100",
            "status": "active",
        }
    )
    assert record.genome["trend_bias"] == 60
    assert record.universe == ["SPY", "QQQ"]
    assert record.is_active


def test_bar_typical_price_is_decimal():
    bar = Bar("SPY", None, open=1, high=Decimal("12"), low=Decimal("6"), close=Decimal("9"))
    assert bar.typical_price == Decimal("9")


def test_quantisation_matches_the_columns():
    assert qty("1.123456789") == Decimal("1.12345679")
    assert price("1.123456789") == Decimal("1.12345679")


@pytest.mark.parametrize("side,sign", [(Side.BUY, 1), (Side.SELL, -1)])
def test_side_signs(side, sign):
    assert side.sign == sign
    assert side.opposite is not side
