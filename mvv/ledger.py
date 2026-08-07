"""Experiment Ledger — spec component 3, the single source of truth.

Every state change goes through this class and every state change writes an
`experiment_events` row. If it is not in the ledger it did not happen.

The ledger deliberately does *not* decide anything. It refuses transitions
that would violate a hard boundary (killing without a reason, resurrecting a
killed experiment) and records what it is told; deciding is kill_criteria.py's
job and approving is human_gate.py's.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .db import Database, utcnow_iso
from .models import Experiment, Order, Status
from .money import D, money


class LedgerError(RuntimeError):
    """A transition the ledger will not record."""


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class ExperimentLedger:
    def __init__(self, db: Database):
        self.db = db

    # -- events -----------------------------------------------------------
    def log_event(
        self,
        experiment_id: Optional[int],
        event_type: str,
        detail: str = "",
        payload: Optional[dict] = None,
    ) -> int:
        return self.db.insert(
            "experiment_events",
            {
                "experiment_id": experiment_id,
                "event_type": event_type,
                "detail": detail,
                "payload": json.dumps(payload or {}, default=_json_default),
                "created_at": utcnow_iso(),
            },
        )

    def events(self, experiment_id: int, limit: int = 50) -> list[dict]:
        return self.db.query(
            "SELECT * FROM experiment_events WHERE experiment_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (experiment_id, limit),
        )

    # -- reads ------------------------------------------------------------
    def exists(self, product_id: str) -> bool:
        return (
            self.db.query_one("SELECT id FROM experiments WHERE product_id = ?", (product_id,))
            is not None
        )

    def get(self, experiment_id: int) -> Optional[Experiment]:
        row = self.db.query_one("SELECT * FROM experiments WHERE id = ?", (experiment_id,))
        return Experiment.from_row(row) if row else None

    def get_by_product_id(self, product_id: str) -> Optional[Experiment]:
        row = self.db.query_one("SELECT * FROM experiments WHERE product_id = ?", (product_id,))
        return Experiment.from_row(row) if row else None

    def require(self, experiment_id: int) -> Experiment:
        exp = self.get(experiment_id)
        if exp is None:
            raise LedgerError(f"experiment {experiment_id} not found")
        return exp

    def all(self) -> list[Experiment]:
        return [
            Experiment.from_row(r)
            for r in self.db.query("SELECT * FROM experiments ORDER BY id")
        ]

    def by_status(self, *statuses: str) -> list[Experiment]:
        if not statuses:
            return self.all()
        placeholders = ", ".join("?" for _ in statuses)
        rows = self.db.query(
            f"SELECT * FROM experiments WHERE status IN ({placeholders}) ORDER BY id",
            tuple(statuses),
        )
        return [Experiment.from_row(r) for r in rows]

    def get_active(self) -> list[Experiment]:
        """Experiments whose numbers are still moving (launched/active/scaling)."""
        return self.by_status(*Status.live())

    def count(self, *statuses: str) -> int:
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            row = self.db.query_one(
                f"SELECT COUNT(*) AS n FROM experiments WHERE status IN ({placeholders})",
                tuple(statuses),
            )
        else:
            row = self.db.query_one("SELECT COUNT(*) AS n FROM experiments")
        return int(row["n"]) if row else 0

    # -- writes -----------------------------------------------------------
    def create_experiment(self, experiment: Experiment, note: str = "") -> Experiment:
        if self.exists(experiment.product_id):
            raise LedgerError(f"experiment for product_id {experiment.product_id!r} already exists")
        row = experiment.to_row()
        row["created_at"] = utcnow_iso()
        row["updated_at"] = utcnow_iso()
        new_id = self.db.insert("experiments", row)
        experiment.id = new_id
        self.log_event(
            new_id,
            "created",
            note or f"Discovered on {experiment.source_platform}",
            {
                "unit_cost": experiment.unit_cost,
                "selling_price": experiment.selling_price,
                "margin_pct": experiment.margin_pct,
            },
        )
        return self.require(new_id)

    def update(self, experiment: Experiment, note: str = "metrics updated") -> Experiment:
        """Persist raw metrics and recompute every derived column."""
        if experiment.id is None:
            raise LedgerError("cannot update an experiment with no id")
        row = experiment.to_row()
        row["updated_at"] = utcnow_iso()
        self.db.update("experiments", experiment.id, row)
        self.log_event(
            experiment.id,
            "metrics_updated",
            note,
            {
                "impressions": experiment.impressions,
                "sessions": experiment.sessions,
                "orders": experiment.orders,
                "revenue": experiment.revenue,
                "ad_spend": experiment.ad_spend,
                "ctr": experiment.ctr,
                "conversion_rate": experiment.conversion_rate,
                "cac": experiment.cac,
                "contribution_margin": experiment.contribution_margin,
                "meets_sample_gate": experiment.meets_sample_gate,
            },
        )
        return self.require(experiment.id)

    def record_metrics(
        self,
        experiment_id: int,
        *,
        impressions: Optional[int] = None,
        clicks: Optional[int] = None,
        sessions: Optional[int] = None,
        orders: Optional[int] = None,
        revenue=None,
        ad_spend=None,
        refunds: Optional[int] = None,
        chargebacks: Optional[int] = None,
        avg_delivery_days=None,
        mode: str = "set",
    ) -> Experiment:
        """Update raw metrics. ``mode='set'`` replaces, ``mode='add'`` increments."""
        exp = self.require(experiment_id)
        counters = {
            "impressions": impressions,
            "clicks": clicks,
            "sessions": sessions,
            "orders": orders,
            "refunds": refunds,
            "chargebacks": chargebacks,
        }
        for name, value in counters.items():
            if value is None:
                continue
            current = getattr(exp, name)
            setattr(exp, name, int(current + value) if mode == "add" else int(value))
        for name, value in (("revenue", revenue), ("ad_spend", ad_spend)):
            if value is None:
                continue
            current = D(getattr(exp, name))
            setattr(exp, name, money(current + D(value)) if mode == "add" else money(D(value)))
        if avg_delivery_days is not None:
            # An average is never additive.
            exp.avg_delivery_days = D(avg_delivery_days)
        return self.update(exp, note=f"metrics {mode}")

    def set_status(
        self, experiment_id: int, status: str, note: str = "", **extra
    ) -> Experiment:
        exp = self.require(experiment_id)
        if exp.status == Status.KILLED.value and status != Status.KILLED.value:
            raise LedgerError(
                f"experiment {experiment_id} is killed; killed experiments are not resurrected"
            )
        values = {"status": status, "updated_at": utcnow_iso()}
        values.update(extra)
        self.db.update("experiments", experiment_id, values)
        self.log_event(experiment_id, f"status:{status}", note, {"from": exp.status, "to": status})
        return self.require(experiment_id)

    # -- lifecycle (spec section 5) ---------------------------------------
    def mark_sampled(self, experiment_id: int, note: str = "") -> Experiment:
        return self.set_status(experiment_id, Status.SAMPLED.value, note or "Sample ordered")

    def mark_launched(self, experiment_id: int, daily_budget, note: str = "") -> Experiment:
        """Listing is live and ads are running. Requires prior approval —
        enforced by the orchestrator/human gate, recorded here."""
        return self.set_status(
            experiment_id,
            Status.LAUNCHED.value,
            note or "Listing live, ads running",
            ads_paused=False,
            daily_ad_budget=money(daily_budget),
        )

    def mark_active(self, experiment_id: int, note: str = "") -> Experiment:
        return self.set_status(experiment_id, Status.ACTIVE.value, note or "Collecting data")

    def pause_ads(self, experiment_id: int, reason: str) -> Experiment:
        """Autonomous — stopping the bleeding never needs permission (spec 6)."""
        exp = self.require(experiment_id)
        if exp.ads_paused:
            return exp
        self.db.update(
            "experiments",
            experiment_id,
            {"ads_paused": True, "daily_ad_budget": money(0), "updated_at": utcnow_iso()},
        )
        self.log_event(experiment_id, "ads_paused", reason, {"previous_budget": exp.daily_ad_budget})
        return self.require(experiment_id)

    def resume_ads(self, experiment_id: int, daily_budget, approval_id: int) -> Experiment:
        """Resuming spend always carries the id of the approval that allowed it."""
        if approval_id is None:
            raise LedgerError("resuming ad spend requires an approval id")
        self.db.update(
            "experiments",
            experiment_id,
            {
                "ads_paused": False,
                "daily_ad_budget": money(daily_budget),
                "updated_at": utcnow_iso(),
            },
        )
        self.log_event(
            experiment_id,
            "ads_resumed",
            f"budget set to {money(daily_budget)}",
            {"approval_id": approval_id},
        )
        return self.require(experiment_id)

    def flag_pending_kill(self, experiment_id: int, reason: str) -> Experiment:
        """A kill trigger fired. Ads stop now; the kill itself waits for a human."""
        self.pause_ads(experiment_id, f"kill trigger: {reason}")
        self.db.update(
            "experiments",
            experiment_id,
            {"pending_kill_reason": reason, "updated_at": utcnow_iso()},
        )
        self.log_event(experiment_id, "kill_triggered", reason)
        return self.require(experiment_id)

    def kill(self, experiment_id: int, reason: str, decided_by: str = "human") -> Experiment:
        if not reason:
            raise LedgerError("a kill must carry a reason")
        exp = self.require(experiment_id)
        if exp.status == Status.KILLED.value:
            return exp
        self.db.update(
            "experiments",
            experiment_id,
            {
                "status": Status.KILLED.value,
                "kill_reason": reason,
                "pending_kill_reason": None,
                "ads_paused": True,
                "daily_ad_budget": money(0),
                "killed_at": utcnow_iso(),
                "updated_at": utcnow_iso(),
            },
        )
        self.log_event(
            experiment_id,
            "killed",
            reason,
            {
                "decided_by": decided_by,
                "final_contribution_margin": exp.contribution_margin,
                "orders": exp.orders,
                "ad_spend": exp.ad_spend,
            },
        )
        return self.require(experiment_id)

    def continue_experiment(self, experiment_id: int, note: str = "human override") -> Experiment:
        """Human said CONTINUE.

        Clears the pending flag and remembers *which* condition was overridden,
        so the next tick does not immediately re-escalate the same trigger on
        the same data — that would turn CONTINUE into a one-hour snooze and
        train the operator to ignore the alerts. Any *different* kill condition
        still escalates, the daily health check still reports the condition as
        failing, and ads stay paused until a separate spend approval resumes
        them.
        """
        exp = self.require(experiment_id)
        override = exp.pending_kill_reason or exp.kill_override_reason
        self.db.update(
            "experiments",
            experiment_id,
            {
                "pending_kill_reason": None,
                "kill_override_reason": override,
                "updated_at": utcnow_iso(),
            },
        )
        self.log_event(experiment_id, "kill_overridden", note, {"overridden_reason": override})
        return self.require(experiment_id)

    def mark_scaling(self, experiment_id: int, daily_budget, approval_id: int) -> Experiment:
        exp = self.set_status(
            experiment_id,
            Status.SCALING.value,
            f"scaling to {money(daily_budget)}/day",
            scaled_at=utcnow_iso(),
        )
        self.resume_ads(experiment_id, daily_budget, approval_id)
        return self.require(exp.id)

    def mark_success(self, experiment_id: int, note: str = "") -> Experiment:
        return self.set_status(
            experiment_id, Status.SUCCESS.value, note or "Stable, profitable, repeatable"
        )

    # -- orders -----------------------------------------------------------
    def record_order(self, order: Order) -> Order:
        row = order.to_row()
        order.id = self.db.insert("orders", row)
        self.log_event(
            order.experiment_id,
            "order_recorded",
            f"order {order.external_id or order.id}",
            {"customer_paid": order.customer_paid, "net_contribution": order.net_contribution},
        )
        return order

    def orders_for(self, experiment_id: int) -> list[Order]:
        rows = self.db.query(
            "SELECT * FROM orders WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        )
        return [Order.from_row(r) for r in rows]

    def recompute_from_orders(
        self, experiment_id: int, allow_decrease: bool = False
    ) -> Experiment:
        """Rebuild order-derived metrics from the orders table.

        There are two ways order data can reach the ledger: row-by-row through
        `record_order`, or as running totals through `record_metrics` (what
        `mvv metrics` does when the operator reads them off a dashboard). They
        are alternatives, not layers. Recomputing while the totals came from
        somewhere else would silently overwrite 55 orders with the 1 order that
        happens to be in the table, so a recompute that would *reduce* the
        recorded count refuses instead. Pass `allow_decrease=True` to declare
        the orders table authoritative and overwrite deliberately.

        Traffic (impressions/clicks/sessions) comes from the ad platform and is
        not derivable here, so it is left untouched. Neither is `ad_spend`:
        orders only carry *attributed* ad cost, which by construction excludes
        spend on traffic that never converted. Taking it from the orders table
        would flatter every experiment's CAC.
        """
        orders = self.orders_for(experiment_id)
        exp = self.require(experiment_id)
        if len(orders) < exp.orders and not allow_decrease:
            raise LedgerError(
                f"experiment {experiment_id} records {exp.orders} orders but the orders table "
                f"holds {len(orders)}; recomputing would discard metrics entered another way. "
                f"Use `mvv recompute {experiment_id} --force` if the orders table is the "
                f"source of truth."
            )
        exp.orders = len(orders)
        exp.revenue = money(sum((o.customer_paid for o in orders), D(0)))
        exp.refunds = sum(1 for o in orders if o.refunded)
        exp.chargebacks = sum(1 for o in orders if o.chargeback)
        delivered = [o.days_to_delivery for o in orders if o.delivered and o.days_to_delivery is not None]
        exp.avg_delivery_days = (
            D(sum(delivered)) / D(len(delivered)) if delivered else exp.avg_delivery_days
        )
        return self.update(exp, note="recomputed from orders")
