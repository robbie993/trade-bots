"""Selling what you do not own.

The ledger was already short-correct — `Position.apply` handles the flip
through zero, `unrealized_pnl` is signed, and a sell credits cash whether it
closes a long or opens a short. What was missing was permission, and the
guardrails that permission makes necessary.

Those guardrails are the point of this file. A long position's worst case is
losing what you put in. A short's worst case has no floor, and two of the
existing limits quietly stop working once quantities can go negative:

* the position cap measured signed value, so a short *increased* its own
  headroom — the more short you were, the more you could short;
* the cash floor cannot restrain a short at all, because selling adds cash.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from src.money import ZERO
from src.trading.config import FirmDefaults
from src.trading.firms.risk_manager import RiskManager, RiskVerdict
from src.trading.models import Position, Side


@pytest.fixture
def long_only():
    return FirmDefaults(allow_short=False)


@pytest.fixture
def shorting():
    return FirmDefaults(allow_short=True, max_gross_exposure=Decimal("1.5"))


def _review(limits, firm, symbol, side, quantity, price, positions, equity=None):
    manager = RiskManager(limits)
    return manager.review(
        firm, symbol, side, Decimal(quantity), Decimal(price),
        positions, Decimal(equity if equity is not None else firm.allocation),
    )


# =========================================================================
# off by default
# =========================================================================
def test_shorting_is_off_unless_asked_for(monkeypatch):
    """Unbounded risk should not arrive because nobody said otherwise."""
    monkeypatch.delenv("TRADE_ALLOW_SHORT", raising=False)
    assert FirmDefaults().allow_short is False


def test_selling_what_you_do_not_own_is_blocked(long_only, firm_record):
    decision = _review(long_only, firm_record, "SPY", Side.SELL, 10, 100, [])
    assert decision.verdict == RiskVerdict.BLOCK.value
    assert "shorting is disabled" in decision.reason
    assert decision.quantity == ZERO


def test_selling_more_than_you_own_is_cut_to_what_you_own(long_only, firm_record):
    held = [Position(firm_id=firm_record.id, symbol="SPY",
                     quantity=Decimal("4"), avg_price=Decimal("100"))]
    decision = _review(long_only, firm_record, "SPY", Side.SELL, 10, 100, held)
    assert decision.verdict == RiskVerdict.RESIZE.value
    assert decision.quantity == Decimal("4")


# =========================================================================
# on, and still bounded
# =========================================================================
def test_a_short_opens_when_it_is_allowed(shorting, firm_record):
    decision = _review(shorting, firm_record, "SPY", Side.SELL, 10, 100, [])
    assert decision.verdict in (RiskVerdict.ALLOW.value, RiskVerdict.RESIZE.value)
    assert decision.quantity > 0


def test_closing_a_short_is_always_allowed(shorting, firm_record):
    """The system may always stop the bleeding. Buying back is stopping it."""
    short = [Position(firm_id=firm_record.id, symbol="SPY",
                      quantity=Decimal("-10"), avg_price=Decimal("100"))]
    decision = _review(shorting, firm_record, "SPY", Side.BUY, 10, 100, short)
    assert decision.verdict == RiskVerdict.ALLOW.value
    assert decision.quantity == Decimal("10")
    assert "reducing" in decision.reason


def test_closing_a_short_is_allowed_even_with_shorting_switched_off(long_only, firm_record):
    """Turning shorting off must not trap a firm in a short it already has."""
    short = [Position(firm_id=firm_record.id, symbol="SPY",
                      quantity=Decimal("-10"), avg_price=Decimal("100"))]
    decision = _review(long_only, firm_record, "SPY", Side.BUY, 10, 100, short)
    assert decision.verdict == RiskVerdict.ALLOW.value
    assert decision.quantity == Decimal("10")


def test_a_short_does_not_enlarge_its_own_position_cap(shorting, firm_record):
    """The bug shorting introduces: signed value made room grow with size.

    The position cap is a fraction of equity. Measured signed, a short's
    existing value is negative, so `cap - existing` is *more* than the cap —
    and the more short you were the more you could short.
    """
    equity = Decimal("100000")
    cap = shorting.max_position_pct * equity

    big_short = [Position(firm_id=firm_record.id, symbol="SPY",
                          quantity=Decimal("-500"), avg_price=Decimal("100"))]
    decision = _review(shorting, firm_record, "SPY", Side.SELL, 500, 100,
                       big_short, equity=equity)

    # Already 50,000 short against a 25,000 cap: there is no room, not more.
    assert decision.verdict == RiskVerdict.BLOCK.value
    assert decision.quantity == ZERO
    assert cap < Decimal("50000")


def test_gross_exposure_is_capped(shorting, firm_record):
    """Net exposure is the wrong measure: long 100 and short 100 is flat on
    paper and can still lose on both legs at once."""
    firm = replace(firm_record, allocation=Decimal("10000"))
    positions = [
        Position(firm_id=firm.id, symbol="QQQ",
                 quantity=Decimal("100"), avg_price=Decimal("100")),   # long 10,000
        Position(firm_id=firm.id, symbol="IWM",
                 quantity=Decimal("-50"), avg_price=Decimal("100")),   # short 5,000
    ]
    # Gross is already 15,000 against a 1.5x cap of 15,000: nothing left.
    decision = _review(shorting, firm, "SPY", Side.SELL, 10, 100,
                       positions, equity=Decimal("10000"))
    assert decision.verdict == RiskVerdict.BLOCK.value
    assert "gross exposure" in decision.reason


def test_gross_exposure_counts_shorts_as_risk_not_as_offset(shorting, firm_record):
    firm = replace(firm_record, allocation=Decimal("10000"))
    offsetting = [
        Position(firm_id=firm.id, symbol="QQQ",
                 quantity=Decimal("75"), avg_price=Decimal("100")),
        Position(firm_id=firm.id, symbol="IWM",
                 quantity=Decimal("-75"), avg_price=Decimal("100")),
    ]
    # Net zero. Gross 15,000, which is the whole cap.
    decision = _review(shorting, firm, "SPY", Side.SELL, 10, 100,
                       offsetting, equity=Decimal("10000"))
    assert decision.verdict == RiskVerdict.BLOCK.value


# =========================================================================
# the ledger was already right — these are the proofs
# =========================================================================
def test_a_short_gains_when_the_price_falls():
    short = Position(firm_id=1, symbol="SPY",
                     quantity=Decimal("-10"), avg_price=Decimal("100"))
    assert short.unrealized_pnl(Decimal("90")) == Decimal("100.00")
    assert short.unrealized_pnl(Decimal("110")) == Decimal("-100.00")


def test_a_short_is_worth_a_negative_amount():
    short = Position(firm_id=1, symbol="SPY",
                     quantity=Decimal("-10"), avg_price=Decimal("100"))
    assert short.market_value(Decimal("100")) == Decimal("-1000.00")


def test_buying_back_a_short_realises_the_gain():
    from src.trading.models import Fill

    short = Position(firm_id=1, symbol="SPY",
                     quantity=Decimal("-10"), avg_price=Decimal("100"))
    realized = short.apply(Fill(firm_id=1, symbol="SPY", side=Side.BUY.value,
                                quantity=Decimal("10"), price=Decimal("90")))
    assert realized == Decimal("100.00")     # sold at 100, bought at 90
    assert short.quantity == 0


def test_selling_through_a_long_flips_it_short():
    from src.trading.models import Fill

    position = Position(firm_id=1, symbol="SPY",
                        quantity=Decimal("5"), avg_price=Decimal("100"))
    realized = position.apply(Fill(firm_id=1, symbol="SPY", side=Side.SELL.value,
                                   quantity=Decimal("8"), price=Decimal("110")))
    assert realized == Decimal("50.00")      # the 5 held, at +10 each
    assert position.quantity == Decimal("-3")
    assert position.avg_price == Decimal("110")


# =========================================================================
# borrow
# =========================================================================
def _bear_firm(db, tmp_path, firms_yaml, notifier, allocation=50000):
    from src.config import Config
    from src.trading.config import DataConfig, TradingConfig
    from src.trading.ecosystem import Ecosystem

    bot = tmp_path / "bear.py"
    bot.write_text(
        "def propose(context):\n"
        "    return [{'symbol': s, 'side': 'sell', 'notional': 3000,\n"
        "             'rationale': 'short it'}\n"
        "            for s in context.universe\n"
        "            if context.quantity(s) == 0 and context.price(s)]\n"
    )
    firms_yaml.write_text(
        "firms:\n"
        "  bear:\n"
        "    name: Bear Desk\n"
        "    asset_class: Equities\n"
        f"    capital_allocation: {allocation}\n"
        "    risk_limit: 0.10\n"
        "    universe: [SPY, QQQ]\n"
        "    analysts: [technical]\n"
        f"    bot: {bot}\n"
    )
    config = TradingConfig(
        firms_config=firms_yaml, audit_vault=tmp_path / "v", vendor_dir=tmp_path / "d",
        data=DataConfig(source="synthetic", seed=12345, history_days=180),
        firm=FirmDefaults(allow_short=True),
    )
    eco = Ecosystem(
        db, config,
        Config(database_url=db.url, notification_log=tmp_path / "n.log"),
        notifier,
    )
    eco.init_firms()
    return eco


def test_a_short_book_actually_opens(db, tmp_path, firms_yaml, notifier):
    eco = _bear_firm(db, tmp_path, firms_yaml, notifier)
    eco.simulate(15)

    firm = eco.store.firms()[0]
    positions = eco.store.positions(firm.id)
    assert positions, "it should be short something"
    assert any(p.quantity < 0 for p in positions)


def test_borrow_is_charged_on_what_is_short(db, tmp_path, firms_yaml, notifier):
    """A backtest that treats shorting as free is a backtest of a strategy
    nobody can run."""
    eco = _bear_firm(db, tmp_path, firms_yaml, notifier)
    eco.simulate(15)

    firm = eco.store.firms()[0]
    charges = [f for f in eco.store.fills(firm.id) if f.quantity == 0 and f.fee > 0]
    assert charges, "no borrow was ever charged"
    assert all(f.cash_delta == -f.fee for f in charges)


def test_the_books_still_reconcile_with_shorts_and_borrow(db, tmp_path, firms_yaml, notifier):
    """The identity this whole system rests on, with negative quantities in it."""
    eco = _bear_firm(db, tmp_path, firms_yaml, notifier)
    reports = eco.simulate(15)

    assert not [e for r in reports for e in r.errors]
    report = eco.brokerage.reconcile(eco.market())
    assert report.ok, report.breaks


def test_a_firm_that_cannot_pay_borrow_is_not_overdrawn(db, tmp_path, firms_yaml, notifier):
    """The kill switch says a firm is in trouble. An overdraft is not how."""
    eco = _bear_firm(db, tmp_path, firms_yaml, notifier)
    eco.simulate(5)

    firm = eco.store.firms()[0]
    eco.db.update("firms", firm.id, {"cash": "0.00"})

    report = eco.tick()
    assert eco.store.get_firm("bear").cash >= 0
    assert not any("overdraw" in e for e in report.errors)
