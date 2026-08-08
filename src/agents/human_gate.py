"""Human Approval Gate — spec component 4 and section 6.

The hard boundary. Not an agent, not a heuristic: a row in a table that a
human has to change before anything irreversible happens.

Requires approval (spec 6):
    spending money on ads, ordering samples, listing a product, killing a
    product, scaling ad spend beyond $50/day, adding a new platform.

Autonomous (spec 6):
    scraping, calculating, monitoring, *pausing* ads, writing the ledger.

The asymmetry is deliberate — the system may always stop the bleeding, and
may never start it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from ..config import Config, GateConfig
from ..db.connection import Database, to_datetime, utcnow_iso
from ..money import D, fmt_money, money
from ..notifications import Notifier, build_notifier


class ApprovalAction(str, Enum):
    AD_SPEND = "ad_spend"
    ORDER_SAMPLE = "sample_order"
    LIST_PRODUCT = "launch"
    KILL_EXPERIMENT = "kill"
    SCALE_AD_SPEND = "scale"
    ADD_PLATFORM = "add_platform"
    KILL_ALL = "kill_all"
    # Trading Edition (src/trading). Same gate, same table, same asymmetry:
    # capital may always be taken back, and may never be handed out, without
    # one of these being approved first.
    ALLOCATE_CAPITAL = "allocate_capital"
    KILL_FIRM = "kill_firm"
    LIVE_TRADING = "live_trading"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequired(RuntimeError):
    """Raised when code tries to act without an approved request."""

    def __init__(self, message: str, approval_id: Optional[int] = None):
        super().__init__(message)
        self.approval_id = approval_id


@dataclass
class Approval:
    id: int
    action: str
    experiment_id: Optional[int]
    amount: Optional[Decimal]
    reason: str
    details: dict
    status: str
    requested_at: Optional[datetime]
    approved_at: Optional[datetime]
    approved_by: Optional[str]
    notes: Optional[str]

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING.value

    @property
    def is_approved(self) -> bool:
        return self.status == ApprovalStatus.APPROVED.value

    @classmethod
    def from_row(cls, row: dict) -> "Approval":
        raw_details = row.get("details")
        try:
            details = json.loads(raw_details) if raw_details else {}
        except (TypeError, ValueError):
            details = {"raw": raw_details}
        return cls(
            id=int(row["id"]),
            action=row.get("action") or "",
            experiment_id=row.get("experiment_id"),
            amount=D(row["amount"]) if row.get("amount") is not None else None,
            reason=row.get("reason") or "",
            details=details,
            status=row.get("status") or ApprovalStatus.PENDING.value,
            requested_at=to_datetime(row.get("requested_at")),
            approved_at=to_datetime(row.get("approved_at")),
            approved_by=row.get("approved_by"),
            notes=row.get("notes"),
        )


KILL_NOTIFICATION_TEMPLATE = """🚨 EXPERIMENT KILL TRIGGERED

Product: {product_name}
Reason: {kill_reason}
Data:
  - Impressions: {impressions}
  - Sessions: {sessions}
  - Orders: {orders}
  - CTR: {ctr}%
  - Conversion: {conversion_rate}%
  - CAC: {cac}
  - Contribution Margin: {contribution_margin}
  - Refund Rate: {refund_rate}%

Ads have been paused automatically. The experiment is NOT killed yet.

Recommended action: KILL or CONTINUE?

  python -m src.main approve {approval_id} --by you      # KILL
  python -m src.main reject  {approval_id} --by you      # CONTINUE
  python -m src.main show {experiment_id}                # REVIEW_DATA
"""

SPEND_NOTIFICATION_TEMPLATE = """💸 APPROVAL REQUIRED — {action}

{reason}
Amount: {amount}
{detail_lines}
Nothing has been spent. This request expires nothing and does nothing until
you decide:

  python -m src.main approve {approval_id} --by you
  python -m src.main reject  {approval_id} --by you
"""


class HumanGate:
    def __init__(
        self,
        db: Database,
        notifier: Optional[Notifier] = None,
        config: Optional[GateConfig] = None,
        app_config: Optional[Config] = None,
    ):
        self.db = db
        self.config = config or GateConfig()
        self.notifier = notifier if notifier is not None else build_notifier(app_config)

    # -- requests ---------------------------------------------------------
    def request(
        self,
        action: str,
        reason: str,
        *,
        experiment_id: Optional[int] = None,
        amount=None,
        details: Optional[dict] = None,
        notify: bool = True,
        dedupe_key: Optional[str] = None,
    ) -> Approval:
        """Record a pending request and tell the human. Spends nothing.

        Requests with no experiment behind them collapse onto one pending row
        per action, which is right for KILL_ALL and wrong for anything that
        can be pending for several *subjects* at once — three firms each
        wanting more capital are three decisions, not one. Those callers pass
        a ``dedupe_key`` (the firm, the venue) and get one pending row each.
        """
        existing = (
            self.find_pending_by_key(action, dedupe_key)
            if dedupe_key is not None
            else self.find_pending(action, experiment_id)
        )
        if existing is not None:
            return existing  # never spam the operator with duplicates
        if dedupe_key is not None:
            details = {**(details or {}), "dedupe_key": dedupe_key}

        approval_id = self.db.insert(
            "human_approvals",
            {
                "action": action,
                "experiment_id": experiment_id,
                "amount": money(amount) if amount is not None else None,
                "reason": reason,
                "details": json.dumps(details or {}, default=str),
                "status": ApprovalStatus.PENDING.value,
                "requested_at": utcnow_iso(),
            },
        )
        approval = self.get(approval_id)
        if notify:
            self._notify_request(approval)
        return approval

    def request_kill(self, experiment, reason: str) -> Approval:
        """Spec 4.3 — a kill trigger notifies and waits; it never kills."""
        approval = self.request(
            ApprovalAction.KILL_EXPERIMENT.value,
            reason,
            experiment_id=experiment.id,
            details={
                "product_name": experiment.product_name,
                "impressions": experiment.impressions,
                "sessions": experiment.sessions,
                "orders": experiment.orders,
                "ctr": str(experiment.ctr),
                "conversion_rate": str(experiment.conversion_rate),
                "cac": str(experiment.cac),
                "contribution_margin": str(experiment.contribution_margin),
                "refund_rate": str(experiment.refund_rate),
            },
            notify=False,
        )
        if approval.is_pending and approval.approved_at is None:
            self.notifier.send(
                f"KILL TRIGGERED: {experiment.product_name}",
                KILL_NOTIFICATION_TEMPLATE.format(
                    product_name=experiment.product_name,
                    kill_reason=reason,
                    impressions=experiment.impressions,
                    sessions=experiment.sessions,
                    orders=experiment.orders,
                    ctr=experiment.ctr,
                    conversion_rate=experiment.conversion_rate,
                    # fmt_money carries the currency symbol so a loss reads
                    # "-$562.00" rather than the spec template's "$-562.00".
                    cac=fmt_money(experiment.cac),
                    contribution_margin=fmt_money(experiment.contribution_margin),
                    refund_rate=experiment.refund_rate,
                    approval_id=approval.id,
                    experiment_id=experiment.id,
                ),
            )
        return approval

    def request_ad_spend(self, experiment, daily_budget, reason: str = "") -> Approval:
        """Ad spend needs approval; above the scale threshold it is a *separate*
        kind of approval so a routine launch can never smuggle in a scale-up."""
        budget = D(daily_budget)
        action = (
            ApprovalAction.SCALE_AD_SPEND.value
            if budget > self.config.scale_approval_daily_threshold
            else ApprovalAction.AD_SPEND.value
        )
        return self.request(
            action,
            reason or f"Run ads for {experiment.product_name} at {fmt_money(budget)}/day",
            experiment_id=experiment.id,
            amount=budget,
            details={
                "product_name": experiment.product_name,
                "daily_budget": str(money(budget)),
                "current_status": experiment.status,
                "contribution_margin": str(experiment.contribution_margin),
            },
        )

    def request_sample(self, experiment, units: int, unit_cost) -> Approval:
        total = money(D(unit_cost) * units)
        return self.request(
            ApprovalAction.ORDER_SAMPLE.value,
            f"Order {units} sample unit(s) of {experiment.product_name}",
            experiment_id=experiment.id,
            amount=total,
            details={"units": units, "unit_cost": str(money(unit_cost)), "total": str(total)},
        )

    def request_listing(self, experiment, selling_price) -> Approval:
        return self.request(
            ApprovalAction.LIST_PRODUCT.value,
            f"List {experiment.product_name} at {fmt_money(selling_price)}",
            experiment_id=experiment.id,
            amount=money(selling_price),
            details={"supplier": experiment.supplier, "platform": experiment.source_platform},
        )

    def request_platform(self, platform: str) -> Approval:
        return self.request(
            ApprovalAction.ADD_PLATFORM.value,
            f"Add source platform: {platform}",
            details={"platform": platform},
        )

    def request_kill_all(self, reason: str, details: Optional[dict] = None) -> Approval:
        return self.request(ApprovalAction.KILL_ALL.value, reason, details=details or {})

    # -- reads ------------------------------------------------------------
    def get(self, approval_id: int) -> Approval:
        row = self.db.query_one("SELECT * FROM human_approvals WHERE id = ?", (approval_id,))
        if row is None:
            raise ApprovalRequired(f"approval {approval_id} not found")
        return Approval.from_row(row)

    def find_pending(self, action: str, experiment_id: Optional[int]) -> Optional[Approval]:
        if experiment_id is None:
            row = self.db.query_one(
                "SELECT * FROM human_approvals WHERE action = ? AND experiment_id IS NULL "
                "AND status = ? ORDER BY id DESC LIMIT 1",
                (action, ApprovalStatus.PENDING.value),
            )
        else:
            row = self.db.query_one(
                "SELECT * FROM human_approvals WHERE action = ? AND experiment_id = ? "
                "AND status = ? ORDER BY id DESC LIMIT 1",
                (action, experiment_id, ApprovalStatus.PENDING.value),
            )
        return Approval.from_row(row) if row else None

    def find_pending_by_key(self, action: str, dedupe_key: str) -> Optional[Approval]:
        """The pending request for this action *and this subject*, if any."""
        rows = self.db.query(
            "SELECT * FROM human_approvals WHERE action = ? AND status = ? ORDER BY id DESC",
            (action, ApprovalStatus.PENDING.value),
        )
        for row in rows:
            approval = Approval.from_row(row)
            if approval.details.get("dedupe_key") == dedupe_key:
                return approval
        return None

    def pending(self) -> list[Approval]:
        rows = self.db.query(
            "SELECT * FROM human_approvals WHERE status = ? ORDER BY id",
            (ApprovalStatus.PENDING.value,),
        )
        return [Approval.from_row(r) for r in rows]

    def history(self, limit: int = 50) -> list[Approval]:
        rows = self.db.query("SELECT * FROM human_approvals ORDER BY id DESC LIMIT ?", (limit,))
        return [Approval.from_row(r) for r in rows]

    def has_approval(self, action: str, experiment_id: Optional[int], amount=None) -> bool:
        """Is there a live approval covering this exact act?

        An approval for $10/day does not authorise $80/day — the stored amount
        is a ceiling, not a suggestion.
        """
        approval = self.latest_approved(action, experiment_id)
        if approval is None:
            return False
        if amount is not None and approval.amount is not None:
            return D(amount) <= approval.amount
        return True

    def latest_approved(self, action: str, experiment_id: Optional[int]) -> Optional[Approval]:
        if experiment_id is None:
            row = self.db.query_one(
                "SELECT * FROM human_approvals WHERE action = ? AND experiment_id IS NULL "
                "AND status = ? ORDER BY id DESC LIMIT 1",
                (action, ApprovalStatus.APPROVED.value),
            )
        else:
            row = self.db.query_one(
                "SELECT * FROM human_approvals WHERE action = ? AND experiment_id = ? "
                "AND status = ? ORDER BY id DESC LIMIT 1",
                (action, experiment_id, ApprovalStatus.APPROVED.value),
            )
        return Approval.from_row(row) if row else None

    def require_approval(self, action: str, experiment_id: Optional[int], amount=None) -> Approval:
        """Call this immediately before doing the irreversible thing."""
        approval = self.latest_approved(action, experiment_id)
        if approval is None:
            raise ApprovalRequired(
                f"no approved '{action}' for experiment {experiment_id}; request one first"
            )
        if amount is not None and approval.amount is not None and D(amount) > approval.amount:
            raise ApprovalRequired(
                f"approval {approval.id} covers up to {fmt_money(approval.amount)}, "
                f"attempted {fmt_money(amount)}",
                approval.id,
            )
        return approval

    # -- decisions --------------------------------------------------------
    def decide(
        self, approval_id: int, decision: str, approved_by: str = "operator", notes: str = ""
    ) -> Approval:
        decision = decision.strip().lower()
        mapping = {
            "approve": ApprovalStatus.APPROVED.value,
            "approved": ApprovalStatus.APPROVED.value,
            "kill": ApprovalStatus.APPROVED.value,  # KILL button on a kill request
            "yes": ApprovalStatus.APPROVED.value,
            "reject": ApprovalStatus.REJECTED.value,
            "rejected": ApprovalStatus.REJECTED.value,
            "continue": ApprovalStatus.REJECTED.value,  # CONTINUE button
            "no": ApprovalStatus.REJECTED.value,
        }
        if decision not in mapping:
            raise ValueError(f"unknown decision {decision!r}")
        approval = self.get(approval_id)
        if not approval.is_pending:
            raise ValueError(f"approval {approval_id} was already {approval.status}")
        self.db.update(
            "human_approvals",
            approval_id,
            {
                "status": mapping[decision],
                "approved_at": utcnow_iso(),
                "approved_by": approved_by,
                "notes": notes,
            },
        )
        return self.get(approval_id)

    def approve(self, approval_id: int, approved_by: str = "operator", notes: str = "") -> Approval:
        return self.decide(approval_id, "approve", approved_by, notes)

    def reject(self, approval_id: int, approved_by: str = "operator", notes: str = "") -> Approval:
        return self.decide(approval_id, "reject", approved_by, notes)

    def wait_for_decision(
        self, approval_id: int, timeout_s: Optional[int] = None, poll_s: int = 5
    ) -> Approval:
        """Block until a human decides (spec 11.2's blocking gate).

        Off by default — see `GateConfig.block_on_approval`. A loop that blocks
        on an unanswered notification stops monitoring every *other*
        experiment, which is a worse failure than a delayed kill.
        """
        deadline = time.monotonic() + (
            self.config.approval_wait_timeout_s if timeout_s is None else timeout_s
        )
        while True:
            approval = self.get(approval_id)
            if not approval.is_pending:
                return approval
            if time.monotonic() >= deadline:
                return approval
            time.sleep(poll_s)

    # -- notification -----------------------------------------------------
    def _notify_request(self, approval: Approval) -> None:
        detail_lines = "\n".join(f"  - {k}: {v}" for k, v in sorted(approval.details.items()))
        self.notifier.send(
            f"APPROVAL REQUIRED: {approval.action}",
            SPEND_NOTIFICATION_TEMPLATE.format(
                action=approval.action,
                reason=approval.reason,
                amount=fmt_money(approval.amount) if approval.amount is not None else "n/a",
                detail_lines=detail_lines,
                approval_id=approval.id,
            ),
        )
