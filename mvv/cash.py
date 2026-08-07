"""Cash Flow Ledger — spec section 7.

"Real stores fail from cash, not P&L." This module tracks *available* cash,
which is not the same as the balance and not remotely the same as profit:

* money held by the processor is on the books but cannot buy anything until
  its release date;
* the emergency buffer is not spendable at all;
* an order costs supplier money on day 0 and pays out on day 3-7.

`available_cash()` is the number every spend decision is checked against.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from .config import CashConfig
from .db import Database, to_datetime, utcnow
from .models import CashCategory, CashFlowEntry
from .money import D, ZERO, money, fmt_money


class InsufficientCash(RuntimeError):
    """A spend that would breach the emergency buffer. Never auto-overridden."""


class CashLedger:
    def __init__(self, db: Database, config: Optional[CashConfig] = None):
        self.db = db
        self.config = config or CashConfig()

    # -- writes -----------------------------------------------------------
    def record(self, entry: CashFlowEntry) -> CashFlowEntry:
        entry.id = self.db.insert("cash_flow", entry.to_row())
        return entry

    def credit(self, description: str, amount, category: str, **kw) -> CashFlowEntry:
        amount = abs(D(amount))
        return self.record(
            CashFlowEntry(description=description, category=category, amount=money(amount), **kw)
        )

    def debit(self, description: str, amount, category: str, **kw) -> CashFlowEntry:
        amount = abs(D(amount))
        return self.record(
            CashFlowEntry(description=description, category=category, amount=money(-amount), **kw)
        )

    def open_account(self, amount=None) -> CashFlowEntry:
        """Seed the ledger with the starting budget (spec 7.1)."""
        amount = self.config.initial_budget if amount is None else amount
        return self.credit(
            "Opening balance", amount, CashCategory.OPENING_BALANCE.value
        )

    def record_customer_payment(
        self,
        amount,
        *,
        experiment_id: Optional[int] = None,
        order_id: Optional[int] = None,
        when: Optional[datetime] = None,
    ) -> list[CashFlowEntry]:
        """Split a customer payment into the settling part and the reserve.

        Day 0 the money is authorised but not spendable. The processor keeps a
        rolling reserve for months. Both facts are recorded as separate held
        entries with their own release dates, which is why the ledger can tell
        you what you can spend *today*.
        """
        when = when or utcnow()
        gross = money(abs(D(amount)))
        fee = money(
            gross * self.config.processor_fee_pct / D(100) + self.config.processor_fee_fixed
        )
        reserve = money(gross * self.config.processor_hold_pct / D(100))
        # The fee is booked as its own debit below so it shows up in
        # `by_category()`; it must not also be netted out of the settling
        # amount or the ledger charges it twice.
        settling = money(gross - reserve)

        entries = [
            self.record(
                CashFlowEntry(
                    description="Customer payment (settling)",
                    category=CashCategory.CUSTOMER_REVENUE.value,
                    amount=settling,
                    date=when,
                    hold=True,
                    release_date=when + timedelta(days=self.config.settlement_days),
                    experiment_id=experiment_id,
                    order_id=order_id,
                )
            ),
            self.record(
                CashFlowEntry(
                    description="Processor rolling reserve",
                    category=CashCategory.PROCESSOR_HOLD.value,
                    amount=reserve,
                    date=when,
                    hold=True,
                    release_date=when + timedelta(days=self.config.processor_hold_days),
                    experiment_id=experiment_id,
                    order_id=order_id,
                )
            ),
            self.record(
                CashFlowEntry(
                    description="Processor fee",
                    category=CashCategory.PROCESSOR_FEE.value,
                    amount=money(-fee),
                    date=when,
                    experiment_id=experiment_id,
                    order_id=order_id,
                )
            ),
        ]
        return entries

    # -- reads ------------------------------------------------------------
    def entries(self, limit: Optional[int] = None) -> list[CashFlowEntry]:
        sql = "SELECT * FROM cash_flow ORDER BY date, id"
        rows = self.db.query(sql + (" LIMIT ?" if limit else ""), (limit,) if limit else ())
        return [CashFlowEntry.from_row(r) for r in rows]

    def total_balance(self) -> Decimal:
        """Everything on the books, held or not. The accounting number."""
        return money(sum((e.amount for e in self.entries()), ZERO))

    def held(self, when: Optional[datetime] = None) -> Decimal:
        """Money that exists but cannot be spent yet."""
        when = when or utcnow()
        return money(
            sum((e.amount for e in self.entries() if not e.is_available_at(when)), ZERO)
        )

    def available_cash(self, when: Optional[datetime] = None) -> Decimal:
        """The only number a spend decision may look at."""
        when = when or utcnow()
        return money(sum((e.amount for e in self.entries() if e.is_available_at(when)), ZERO))

    def spendable(self, when: Optional[datetime] = None) -> Decimal:
        """Available cash minus the untouchable emergency buffer."""
        return money(self.available_cash(when) - self.config.emergency_buffer)

    def can_spend(self, amount, when: Optional[datetime] = None) -> tuple[bool, str]:
        amount = abs(D(amount))
        spendable = self.spendable(when)
        if amount > spendable:
            return False, (
                f"{fmt_money(amount)} requested but only {fmt_money(spendable)} spendable "
                f"({fmt_money(self.available_cash(when))} available less "
                f"{fmt_money(self.config.emergency_buffer)} emergency buffer)"
            )
        return True, f"{fmt_money(spendable)} spendable after buffer"

    def assert_can_spend(self, amount, when: Optional[datetime] = None) -> None:
        ok, why = self.can_spend(amount, when)
        if not ok:
            raise InsufficientCash(why)

    def upcoming_releases(self, days: int = 30, when: Optional[datetime] = None) -> list[dict]:
        """What frees up, and when — the forecast half of the cash picture."""
        when = when or utcnow()
        horizon = when + timedelta(days=days)
        out = []
        for e in self.entries():
            if e.hold and e.release_date is not None:
                release = to_datetime(e.release_date)
                if release and when < release <= horizon:
                    out.append(
                        {"release_date": release, "amount": e.amount, "description": e.description}
                    )
        return sorted(out, key=lambda r: r["release_date"])

    def by_category(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for e in self.entries():
            totals[e.category] = money(totals.get(e.category, ZERO) + e.amount)
        return totals

    def status(self, when: Optional[datetime] = None) -> dict:
        when = when or utcnow()
        available = self.available_cash(when)
        return {
            "as_of": when,
            "total_balance": self.total_balance(),
            "available_cash": available,
            "held": self.held(when),
            "emergency_buffer": money(self.config.emergency_buffer),
            "spendable": self.spendable(when),
            "negative": available < 0,
            "buffer_breached": available < self.config.emergency_buffer,
            "by_category": self.by_category(),
            "upcoming_releases": self.upcoming_releases(when=when),
        }
