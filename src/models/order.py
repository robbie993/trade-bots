"""Order model — spec section 3.2.

One fulfilled order: the unit of real cash. `net_contribution` is what the
order actually left behind after every cost, which is not the same as the
customer's payment and not the same as the margin on the listing.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..db.connection import to_datetime, to_iso, utcnow_iso
from ..money import D, ZERO, money


@dataclass
class Order:
    """Spec 3.2 — one fulfilled order, the unit of real cash."""

    experiment_id: int
    external_id: Optional[str] = None
    order_date: Optional[datetime] = None
    customer_paid: Decimal = ZERO
    supplier_cost: Decimal = ZERO
    shipping_cost: Decimal = ZERO
    ad_cost: Decimal = ZERO
    payment_processor_fee: Decimal = ZERO
    refunded: bool = False
    refund_date: Optional[datetime] = None
    chargeback: bool = False
    delivered: bool = False
    delivery_date: Optional[datetime] = None
    days_to_delivery: Optional[int] = None
    id: Optional[int] = None

    def __post_init__(self) -> None:
        for name in (
            "customer_paid",
            "supplier_cost",
            "shipping_cost",
            "ad_cost",
            "payment_processor_fee",
        ):
            setattr(self, name, D(getattr(self, name)))
        self.refunded = bool(self.refunded)
        self.chargeback = bool(self.chargeback)
        self.delivered = bool(self.delivered)

    @property
    def net_contribution(self) -> Decimal:
        """What this order actually left behind after every cost."""
        if self.refunded or self.chargeback:
            # Refunds return the customer's money but not the costs already sunk.
            return money(
                -(self.supplier_cost + self.shipping_cost + self.ad_cost + self.payment_processor_fee)
            )
        return money(
            self.customer_paid
            - self.supplier_cost
            - self.shipping_cost
            - self.ad_cost
            - self.payment_processor_fee
        )

    def to_row(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "external_id": self.external_id,
            "order_date": to_iso(self.order_date) or utcnow_iso(),
            "customer_paid": money(self.customer_paid),
            "supplier_cost": money(self.supplier_cost),
            "shipping_cost": money(self.shipping_cost),
            "ad_cost": money(self.ad_cost),
            "payment_processor_fee": money(self.payment_processor_fee),
            "refunded": self.refunded,
            "refund_date": to_iso(self.refund_date),
            "chargeback": self.chargeback,
            "delivered": self.delivered,
            "delivery_date": to_iso(self.delivery_date),
            "days_to_delivery": self.days_to_delivery,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Order":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in row.items() if k in known}
        for key in ("order_date", "refund_date", "delivery_date"):
            if key in data:
                data[key] = to_datetime(data[key])
        return cls(**data)


