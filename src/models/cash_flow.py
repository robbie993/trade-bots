"""Cash flow model — spec section 3.3.

A signed cash movement. Credits positive, debits negative. The `hold` flag
and `release_date` are what let the ledger distinguish money that exists from
money you can actually spend today.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from ..db.connection import to_datetime, to_iso, utcnow_iso
from ..money import D, money


class CashCategory(str, Enum):
    AD_SPEND = "ad_spend"
    SUPPLIER_PAYMENT = "supplier_payment"
    CUSTOMER_REVENUE = "customer_revenue"
    PROCESSOR_HOLD = "processor_hold"
    PROCESSOR_FEE = "processor_fee"
    REFUND = "refund"
    SAMPLE_PURCHASE = "sample_purchase"
    OPENING_BALANCE = "opening_balance"
    OTHER = "other"


@dataclass
class CashFlowEntry:
    """Spec 3.3 — signed cash movement. Credits positive, debits negative."""

    description: str
    category: str
    amount: Decimal
    date: Optional[datetime] = None
    hold: bool = False
    release_date: Optional[datetime] = None
    experiment_id: Optional[int] = None
    order_id: Optional[int] = None
    id: Optional[int] = None

    def __post_init__(self) -> None:
        self.amount = D(self.amount)
        self.hold = bool(self.hold)

    def is_available_at(self, when: datetime) -> bool:
        """Held money counts as spendable only once its release date passes."""
        if not self.hold:
            return True
        if self.release_date is None:
            return False
        return to_datetime(self.release_date) <= when

    def to_row(self) -> dict:
        return {
            "description": self.description,
            "category": self.category,
            "amount": money(self.amount),
            "date": to_iso(self.date) or utcnow_iso(),
            "hold": self.hold,
            "release_date": to_iso(self.release_date),
            "experiment_id": self.experiment_id,
            "order_id": self.order_id,
        }

    @classmethod
    def from_row(cls, row: dict) -> "CashFlowEntry":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in row.items() if k in known}
        for key in ("date", "release_date"):
            if key in data:
                data[key] = to_datetime(data[key])
        return cls(**data)
