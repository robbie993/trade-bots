"""Spec section 7 — working capital.

The point of these tests: available cash is not the balance, and the balance
is not profit. A store with $1,000 of "revenue" and $30 of spendable cash
cannot buy stock, and the ledger has to say so.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from mvv.cash import CashLedger, InsufficientCash
from mvv.config import CashConfig
from mvv.db import utcnow
from mvv.models import CashCategory


def test_opening_balance(cash):
    cash.open_account()
    assert cash.total_balance() == Decimal("1000.00")
    assert cash.available_cash() == Decimal("1000.00")
    assert cash.spendable() == Decimal("850.00")  # less the $150 buffer


def test_debits_and_credits_are_signed(cash):
    cash.open_account(Decimal("100"))
    cash.debit("ads", Decimal("30"), CashCategory.AD_SPEND.value)
    cash.credit("refund reversal", Decimal("5"), CashCategory.OTHER.value)
    assert cash.total_balance() == Decimal("75.00")


def test_debit_normalises_a_negative_amount(cash):
    """Passing -30 to debit() must not credit 30."""
    cash.open_account(Decimal("100"))
    cash.debit("ads", Decimal("-30"), CashCategory.AD_SPEND.value)
    assert cash.total_balance() == Decimal("70.00")


def test_customer_payment_splits_into_settling_reserve_and_fee(cash):
    cash.record_customer_payment(Decimal("50.00"))
    # 2.9% + $0.30 = $1.75 fee; 7.5% = $3.75 reserve; $46.25 settling.
    assert cash.total_balance() == Decimal("48.25")
    # None of it is spendable on day 0.
    assert cash.available_cash() == Decimal("-1.75")


def test_settling_money_becomes_available_after_the_settlement_window(cash):
    now = utcnow()
    cash.open_account(Decimal("100"))
    cash.record_customer_payment(Decimal("50.00"), when=now)
    assert cash.available_cash(now) == Decimal("98.25")  # only the fee has landed
    later = now + timedelta(days=8)
    assert cash.available_cash(later) == Decimal("144.50")  # + $46.25 settled
    assert cash.held(later) == Decimal("3.75")  # reserve still held


def test_reserve_is_held_for_the_full_hold_period(cash):
    now = utcnow()
    cash.record_customer_payment(Decimal("100.00"), when=now)
    assert cash.held(now + timedelta(days=30)) > 0
    assert cash.held(now + timedelta(days=100)) == Decimal("0.00")


def test_the_day_zero_squeeze(cash):
    """Spec 7.2: you front the supplier before the customer's money lands."""
    now = utcnow()
    cash.open_account(Decimal("1000"))
    cash.record_customer_payment(Decimal("50.00"), when=now)
    cash.debit("supplier", Decimal("15"), CashCategory.SUPPLIER_PAYMENT.value)
    # Books say ~$1,033. Wallet says $983.25.
    assert cash.total_balance() == Decimal("1033.25")
    assert cash.available_cash(now) == Decimal("983.25")


def test_can_spend_respects_the_emergency_buffer(cash):
    cash.open_account(Decimal("200"))
    ok, why = cash.can_spend(Decimal("40"))
    assert ok is True
    ok, why = cash.can_spend(Decimal("60"))  # would leave $140 < $150 buffer
    assert ok is False
    assert "emergency buffer" in why


def test_assert_can_spend_raises(cash):
    cash.open_account(Decimal("160"))
    with pytest.raises(InsufficientCash):
        cash.assert_can_spend(Decimal("50"))


def test_held_money_cannot_be_spent(cash):
    """The failure mode this whole module exists to prevent."""
    now = utcnow()
    cash.open_account(Decimal("160"))
    cash.record_customer_payment(Decimal("500.00"), when=now)
    assert cash.total_balance() > Decimal("600")
    ok, _ = cash.can_spend(Decimal("100"), now)
    assert ok is False


def test_status_flags_a_negative_balance(cash):
    cash.debit("ads", Decimal("500"), CashCategory.AD_SPEND.value)
    status = cash.status()
    assert status["negative"] is True
    assert status["buffer_breached"] is True


def test_upcoming_releases_are_ordered(cash):
    now = utcnow()
    cash.record_customer_payment(Decimal("50"), when=now)
    cash.record_customer_payment(Decimal("60"), when=now + timedelta(days=2))
    releases = cash.upcoming_releases(days=30, when=now)
    assert [r["release_date"] for r in releases] == sorted(r["release_date"] for r in releases)
    assert len(releases) == 2  # only the settling halves fall inside 30 days


def test_by_category_totals(cash):
    cash.open_account(Decimal("100"))
    cash.debit("ads a", Decimal("10"), CashCategory.AD_SPEND.value)
    cash.debit("ads b", Decimal("15"), CashCategory.AD_SPEND.value)
    assert cash.by_category()[CashCategory.AD_SPEND.value] == Decimal("-25.00")


def test_hold_settings_are_configurable(db):
    ledger = CashLedger(db, CashConfig(processor_hold_pct=Decimal("0"), settlement_days=0))
    now = utcnow()
    ledger.record_customer_payment(Decimal("50"), when=now)
    assert ledger.held(now + timedelta(seconds=1)) == Decimal("0.00")
