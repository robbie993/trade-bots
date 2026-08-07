"""Kill criteria — spec section 4.

Pure functions over a metrics snapshot. No database, no network, no clock.
This module is the one piece of the system that decides whether real money
stops moving, so it is kept small enough to read in a single sitting and is
covered directly by tests/test_kill_criteria.py.

Two rules the rest of the codebase depends on:

1. No decision is made until *all* sample-size gates are met. Below the gates
   the answer is "insufficient data", never "kill".
2. Kill conditions are evaluated in the fixed order given in the spec, so the
   reason attached to a kill is reproducible from the stored metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Protocol, Tuple

from .money import D


@dataclass(frozen=True)
class SampleGate:
    """Spec 4.1 — thresholds below which the data cannot support a decision."""

    minimum_impressions: int = 1000  # CTR is stable
    minimum_sessions: int = 100  # conversion rate is stable
    minimum_orders: int = 50  # CAC and margin are meaningful


@dataclass(frozen=True)
class KillThresholds:
    """Spec 4.2 — concrete kill triggers, in evaluation order."""

    min_ctr_pct: Decimal = field(default_factory=lambda: D("0.5"))
    min_conversion_rate_pct: Decimal = field(default_factory=lambda: D("1.0"))
    max_cac_as_fraction_of_price: Decimal = field(default_factory=lambda: D("0.5"))
    min_contribution_margin: Decimal = field(default_factory=lambda: D("0"))
    max_refund_rate_pct: Decimal = field(default_factory=lambda: D("8.0"))
    max_chargeback_rate_pct: Decimal = field(default_factory=lambda: D("1.0"))
    max_avg_delivery_days: Decimal = field(default_factory=lambda: D("10"))


DEFAULT_GATE = SampleGate()
DEFAULT_THRESHOLDS = KillThresholds()

INSUFFICIENT_DATA = "Insufficient data"


class MetricsLike(Protocol):
    """The read-only surface the kill logic needs. `Experiment` satisfies it."""

    impressions: int
    sessions: int
    orders: int
    selling_price: Decimal

    @property
    def ctr(self) -> Decimal: ...
    @property
    def conversion_rate(self) -> Decimal: ...
    @property
    def cac(self) -> Decimal: ...
    @property
    def contribution_margin(self) -> Decimal: ...
    @property
    def refund_rate(self) -> Decimal: ...
    @property
    def chargeback_rate(self) -> Decimal: ...
    @property
    def avg_delivery_days(self) -> Decimal: ...


def gate_for(experiment) -> SampleGate:
    """Per-experiment gates, falling back to the spec defaults."""
    return SampleGate(
        minimum_impressions=int(getattr(experiment, "minimum_impressions", None) or DEFAULT_GATE.minimum_impressions),
        minimum_sessions=int(getattr(experiment, "minimum_sessions", None) or DEFAULT_GATE.minimum_sessions),
        minimum_orders=int(getattr(experiment, "minimum_orders", None) or DEFAULT_GATE.minimum_orders),
    )


def meets_sample_gate(experiment, gate: Optional[SampleGate] = None) -> bool:
    """Spec 4.1. All three gates, no partial credit."""
    g = gate or gate_for(experiment)
    return (
        experiment.impressions >= g.minimum_impressions
        and experiment.sessions >= g.minimum_sessions
        and experiment.orders >= g.minimum_orders
    )


def missing_sample(experiment, gate: Optional[SampleGate] = None) -> dict:
    """How far each gate still has to go — used by the health check."""
    g = gate or gate_for(experiment)
    return {
        "impressions": max(0, g.minimum_impressions - experiment.impressions),
        "sessions": max(0, g.minimum_sessions - experiment.sessions),
        "orders": max(0, g.minimum_orders - experiment.orders),
    }


def should_kill_experiment(
    experiment,
    thresholds: Optional[KillThresholds] = None,
    gate: Optional[SampleGate] = None,
) -> Tuple[bool, Optional[str]]:
    """Spec 4.2 — returns (should_kill, reason).

    Returns ``(False, "Insufficient data")`` below the sample gates: not a
    kill, and not a clean bill of health either.
    """
    t = thresholds or DEFAULT_THRESHOLDS

    if not meets_sample_gate(experiment, gate):
        return False, INSUFFICIENT_DATA

    if experiment.ctr < t.min_ctr_pct:
        return True, "CTR too low"
    if experiment.conversion_rate < t.min_conversion_rate_pct:
        return True, "Conversion rate too low"
    if experiment.cac > D(experiment.selling_price) * t.max_cac_as_fraction_of_price:
        return True, "CAC exceeds 50% of price"
    if experiment.contribution_margin <= t.min_contribution_margin:
        return True, "No positive contribution margin"
    if experiment.refund_rate > t.max_refund_rate_pct:
        return True, "Refund rate too high"
    if experiment.chargeback_rate > t.max_chargeback_rate_pct:
        return True, "Chargeback rate too high"
    if experiment.avg_delivery_days > t.max_avg_delivery_days:
        return True, "Delivery too slow"

    return False, None


def kill_check_table(experiment, thresholds: Optional[KillThresholds] = None) -> list[dict]:
    """Every condition with its actual value — the audit view of a decision.

    Rendered by `mvv show` so a human approving a kill sees which conditions
    fired and which did not, not just the first one that tripped.
    """
    t = thresholds or DEFAULT_THRESHOLDS
    price = D(experiment.selling_price)
    checks = [
        ("CTR", experiment.ctr, f"< {t.min_ctr_pct}%", experiment.ctr < t.min_ctr_pct),
        (
            "Conversion rate",
            experiment.conversion_rate,
            f"< {t.min_conversion_rate_pct}%",
            experiment.conversion_rate < t.min_conversion_rate_pct,
        ),
        (
            "CAC",
            experiment.cac,
            f"> {price * t.max_cac_as_fraction_of_price}",
            experiment.cac > price * t.max_cac_as_fraction_of_price,
        ),
        (
            "Contribution margin",
            experiment.contribution_margin,
            f"<= {t.min_contribution_margin}",
            experiment.contribution_margin <= t.min_contribution_margin,
        ),
        (
            "Refund rate",
            experiment.refund_rate,
            f"> {t.max_refund_rate_pct}%",
            experiment.refund_rate > t.max_refund_rate_pct,
        ),
        (
            "Chargeback rate",
            experiment.chargeback_rate,
            f"> {t.max_chargeback_rate_pct}%",
            experiment.chargeback_rate > t.max_chargeback_rate_pct,
        ),
        (
            "Avg delivery days",
            experiment.avg_delivery_days,
            f"> {t.max_avg_delivery_days}",
            experiment.avg_delivery_days > t.max_avg_delivery_days,
        ),
    ]
    return [
        {"condition": name, "value": value, "kill_when": rule, "triggered": bool(fired)}
        for name, value, rule, fired in checks
    ]
