"""Execution: what paper fills cost, and what a live venue refuses to do."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.human_gate import ApprovalAction
from src.money import D
from src.trading.config import DataConfig
from src.trading.execution import build_venue
from src.trading.execution.live import (
    AlpacaVenue,
    BinanceVenue,
    LiveTradingNotApproved,
    VenueNotConfigured,
    WebullVenue,
)
from src.trading.execution.paper import PaperVenue
from src.trading.models import Side, TradeProposal


def proposal(**overrides):
    defaults = dict(
        firm_id=1, symbol="SPY", side=Side.BUY.value, quantity="10", reference_price="100"
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


# =========================================================================
# paper
# =========================================================================
def test_slippage_always_works_against_the_trader():
    venue = PaperVenue(DataConfig(slippage_bps=D("100")))  # 1%
    buy = venue.quote(Side.BUY, D(100), D(10))
    sell = venue.quote(Side.SELL, D(100), D(10))
    assert buy.fill_price == Decimal("101")
    assert sell.fill_price == Decimal("99")


def test_fees_are_charged_on_both_sides():
    venue = PaperVenue(DataConfig(slippage_bps=D("0"), fee_bps=D("10")))  # 0.1%
    assert venue.quote(Side.BUY, D(100), D(10)).fee == Decimal("1.00")
    assert venue.quote(Side.SELL, D(100), D(10)).fee == Decimal("1.00")


def test_a_paper_fill_carries_its_costs_onto_the_fill():
    venue = PaperVenue(DataConfig(slippage_bps=D("50"), fee_bps=D("20")))
    fill = venue.execute(proposal(), D(100))
    assert fill.venue == "paper"
    assert fill.price > Decimal("100")
    assert fill.fee > 0
    assert fill.slippage > 0


def test_paper_is_the_default_venue(trading_config):
    assert build_venue("paper", trading_config).name == "paper"
    assert build_venue("", trading_config).name == "paper"


def test_an_unknown_venue_is_refused_rather_than_falling_back(trading_config):
    with pytest.raises(VenueNotConfigured) as exc:
        build_venue("alpacca", trading_config)
    assert "unknown venue" in str(exc.value)


# =========================================================================
# live
# =========================================================================
def test_a_live_venue_refuses_without_an_approval(gate):
    venue = AlpacaVenue(gate=gate)
    with pytest.raises(LiveTradingNotApproved) as exc:
        venue.execute(proposal())
    assert "no approved live_trading request" in str(exc.value)


def test_requesting_approval_sends_no_order(gate):
    venue = AlpacaVenue(gate=gate)
    approval = venue.request_approval()
    assert approval.action == ApprovalAction.LIVE_TRADING.value
    assert approval.details["venue"] == "alpaca"
    # Still refused: requesting is not being granted.
    with pytest.raises(LiveTradingNotApproved):
        venue.execute(proposal())


def test_an_approval_covers_one_venue_only(gate):
    alpaca, binance = AlpacaVenue(gate=gate), BinanceVenue(gate=gate)
    approval = alpaca.request_approval()
    gate.approve(approval.id, approved_by="robbie")
    assert alpaca.approval() is not None
    assert binance.approval() is None
    with pytest.raises(LiveTradingNotApproved):
        binance.execute(proposal())


def test_an_approved_venue_still_needs_credentials(gate, monkeypatch):
    venue = AlpacaVenue(gate=gate)
    gate.approve(venue.request_approval().id, approved_by="robbie")
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    with pytest.raises(VenueNotConfigured) as exc:
        venue.execute(proposal())
    assert "ALPACA_API_KEY_ID" in str(exc.value)


def test_the_request_reports_missing_credentials(gate, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    approval = AlpacaVenue(gate=gate).request_approval()
    assert approval.details["credentials_present"] is False
    assert "ALPACA_API_KEY_ID" in approval.details["missing_env"]


def test_live_venues_default_to_their_paper_endpoints(gate):
    assert "paper-api" in AlpacaVenue(gate=gate).base_url
    assert "testnet" in BinanceVenue(gate=gate).base_url


def test_webull_says_it_has_no_public_api(gate, monkeypatch):
    venue = WebullVenue(gate=gate)
    gate.approve(venue.request_approval().id, approved_by="robbie")
    monkeypatch.setenv("WEBULL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("WEBULL_ACCOUNT_ID", "y")
    with pytest.raises(VenueNotConfigured) as exc:
        venue.execute(proposal())
    assert "no supported public trading API" in str(exc.value)


def test_a_venue_without_a_gate_cannot_be_approved():
    with pytest.raises(LiveTradingNotApproved):
        AlpacaVenue(gate=None).request_approval()
