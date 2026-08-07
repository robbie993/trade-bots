"""Domain models — spec sections 3 and 11.1.

`Experiment` is the spec's dataclass with three deliberate changes:

* Money and percentages are ``Decimal``, not ``float`` (see mvv/money.py).
* Derived metrics are quantized to the precision they are stored at, so the
  number that triggers a kill is the number in the ledger.
* `kill_if_necessary()` delegates to mvv/kill_criteria.py rather than
  inlining the thresholds, so there is exactly one copy of the rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, Tuple

from .db import to_datetime, to_iso, utcnow_iso
from .kill_criteria import (
    KillThresholds,
    kill_check_table,
    meets_sample_gate,
    missing_sample,
    should_kill_experiment,
)
from .money import D, ZERO, money, percent, ratio_pct


class Status(str, Enum):
    """Spec section 5 — the experiment lifecycle."""

    DISCOVERED = "discovered"
    SAMPLED = "sampled"
    LAUNCHED = "launched"
    ACTIVE = "active"
    KILLED = "killed"
    SCALING = "scaling"
    SUCCESS = "success"

    @classmethod
    def live(cls) -> tuple:
        """Statuses whose metrics are still moving and worth evaluating."""
        return (cls.LAUNCHED.value, cls.ACTIVE.value, cls.SCALING.value)

    @classmethod
    def terminal(cls) -> tuple:
        return (cls.KILLED.value,)


@dataclass
class Experiment:
    product_id: str
    product_name: str = ""
    source_platform: str = ""
    supplier: str = ""
    product_url: str = ""
    unit_cost: Decimal = ZERO
    selling_price: Decimal = ZERO

    # Raw metrics
    impressions: int = 0
    clicks: int = 0
    sessions: int = 0
    orders: int = 0
    revenue: Decimal = ZERO
    ad_spend: Decimal = ZERO
    refunds: int = 0
    chargebacks: int = 0
    avg_delivery_days: Decimal = ZERO

    # Sample gates (overridable per experiment)
    minimum_orders: int = 50
    minimum_sessions: int = 100
    minimum_impressions: int = 1000

    # Ad state
    ads_paused: bool = True
    daily_ad_budget: Decimal = ZERO

    # Lifecycle
    status: str = Status.DISCOVERED.value
    kill_reason: Optional[str] = None
    pending_kill_reason: Optional[str] = None
    #: The reason a human answered CONTINUE to. The same trigger will not be
    #: re-escalated; a *different* one still will.
    kill_override_reason: Optional[str] = None
    killed_at: Optional[datetime] = None
    scaled_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    id: Optional[int] = None

    def __post_init__(self) -> None:
        self.unit_cost = D(self.unit_cost)
        self.selling_price = D(self.selling_price)
        self.revenue = D(self.revenue)
        self.ad_spend = D(self.ad_spend)
        self.avg_delivery_days = D(self.avg_delivery_days)
        self.daily_ad_budget = D(self.daily_ad_budget)
        for name in ("impressions", "clicks", "sessions", "orders", "refunds", "chargebacks"):
            setattr(self, name, int(getattr(self, name) or 0))
        self.ads_paused = bool(self.ads_paused)

    # -- derived metrics (spec 3.1 "Derived", 11.1) -----------------------
    @property
    def margin_abs(self) -> Decimal:
        """Per-unit gross margin, before ads."""
        return money(self.selling_price - self.unit_cost)

    @property
    def margin_pct(self) -> Decimal:
        return ratio_pct(self.selling_price - self.unit_cost, self.selling_price)

    @property
    def ctr(self) -> Decimal:
        return ratio_pct(self.clicks, self.impressions)

    @property
    def conversion_rate(self) -> Decimal:
        return ratio_pct(self.orders, self.sessions)

    @property
    def cac(self) -> Decimal:
        return money(ZERO) if not self.orders else money(self.ad_spend / self.orders)

    @property
    def aov(self) -> Decimal:
        return money(ZERO) if not self.orders else money(self.revenue / self.orders)

    @property
    def contribution_margin(self) -> Decimal:
        """Revenue minus COGS minus ad spend. The number that decides Phase 1."""
        return money(self.revenue - (self.unit_cost * self.orders) - self.ad_spend)

    @property
    def contribution_margin_pct(self) -> Decimal:
        """Contribution margin as a share of revenue (spec 5.6 scale gate)."""
        return ratio_pct(self.contribution_margin, self.revenue)

    @property
    def refund_rate(self) -> Decimal:
        return ratio_pct(self.refunds, self.orders)

    @property
    def chargeback_rate(self) -> Decimal:
        return ratio_pct(self.chargebacks, self.orders)

    @property
    def meets_sample_gate(self) -> bool:
        return meets_sample_gate(self)

    @property
    def missing_for_gate(self) -> dict:
        return missing_sample(self)

    @property
    def is_live(self) -> bool:
        return self.status in Status.live()

    # -- decisions --------------------------------------------------------
    def kill_if_necessary(
        self, thresholds: Optional[KillThresholds] = None
    ) -> Tuple[bool, Optional[str]]:
        should_kill, reason = should_kill_experiment(self, thresholds)
        return should_kill, reason

    def kill_checks(self, thresholds: Optional[KillThresholds] = None) -> list[dict]:
        return kill_check_table(self, thresholds)

    # -- persistence ------------------------------------------------------
    #: columns written back to `experiments`, derived values included so the
    #: table is queryable by an auditor without re-running Python.
    PERSISTED_DERIVED = (
        "margin_pct",
        "margin_abs",
        "ctr",
        "conversion_rate",
        "cac",
        "aov",
        "contribution_margin",
        "refund_rate",
        "chargeback_rate",
        "meets_sample_gate",
    )

    def to_row(self) -> dict:
        row: dict[str, Any] = {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "source_platform": self.source_platform,
            "supplier": self.supplier,
            "product_url": self.product_url,
            "unit_cost": money(self.unit_cost),
            "selling_price": money(self.selling_price),
            "impressions": self.impressions,
            "clicks": self.clicks,
            "sessions": self.sessions,
            "orders": self.orders,
            "revenue": money(self.revenue),
            "ad_spend": money(self.ad_spend),
            "refunds": self.refunds,
            "chargebacks": self.chargebacks,
            "avg_delivery_days": percent(self.avg_delivery_days),
            "minimum_orders": self.minimum_orders,
            "minimum_sessions": self.minimum_sessions,
            "minimum_impressions": self.minimum_impressions,
            "ads_paused": self.ads_paused,
            "daily_ad_budget": money(self.daily_ad_budget),
            "status": self.status,
            "kill_reason": self.kill_reason,
            "pending_kill_reason": self.pending_kill_reason,
            "kill_override_reason": self.kill_override_reason,
            "killed_at": to_iso(self.killed_at),
            "scaled_at": to_iso(self.scaled_at),
        }
        for name in self.PERSISTED_DERIVED:
            row[name] = getattr(self, name)
        return row

    @classmethod
    def from_row(cls, row: dict) -> "Experiment":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in row.items() if k in known}
        for key in ("killed_at", "scaled_at", "created_at", "updated_at"):
            if key in data:
                data[key] = to_datetime(data[key])
        return cls(**data)

    def summary(self) -> dict:
        """Flat dict of everything a human or notification needs."""
        base = {k: v for k, v in asdict(self).items()}
        base.update(
            {
                "margin_abs": self.margin_abs,
                "margin_pct": self.margin_pct,
                "ctr": self.ctr,
                "conversion_rate": self.conversion_rate,
                "cac": self.cac,
                "aov": self.aov,
                "contribution_margin": self.contribution_margin,
                "contribution_margin_pct": self.contribution_margin_pct,
                "refund_rate": self.refund_rate,
                "chargeback_rate": self.chargeback_rate,
                "meets_sample_gate": self.meets_sample_gate,
            }
        )
        return base


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
