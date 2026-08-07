"""Orchestrator — spec component 5 and section 11.2.

A plain Python loop. No agent framework: there is nothing here that needs a
language model, and a scheduler you can read top-to-bottom is worth more in
Phase 1 than one you can extend.

One `tick()` is the whole business:

    1. DISCOVER  scout -> economic filter -> ledger          (autonomous)
    2. ADVANCE   act on approvals a human has granted        (gated)
    3. MEASURE   pull metrics, recompute, book the cash      (autonomous)
    4. EVALUATE  kill logic -> pause ads -> ask a human      (pause autonomous,
                                                              kill gated)
    5. SCALE     propose budget increases                    (gated)
    6. REPORT    health check + stop condition               (autonomous)

Every step that moves money goes through `HumanGate`. The orchestrator never
calls `ledger.kill()` on its own authority — only in response to an approval
row a human changed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .cash import CashLedger, InsufficientCash
from .config import Config
from .db import Database, utcnow
from .economics import EconomicCalculator
from .human_gate import ApprovalAction, ApprovalStatus, HumanGate
from .kill_criteria import should_kill_experiment
from .ledger import ExperimentLedger
from .metrics import ManualMetricsProvider, MetricsProvider
from .models import CashCategory, Experiment, Status
from .monitoring import HealthMonitor, format_alerts
from .money import D, fmt_money, money
from .notifications import Notifier, build_notifier
from .scout import Scout, build_scout


@dataclass
class TickReport:
    """What one pass of the loop did. Printed by the CLI, asserted by tests."""

    started_at: datetime
    discovered: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    launched: list = field(default_factory=list)
    sampled: list = field(default_factory=list)
    metrics_updated: list = field(default_factory=list)
    kill_triggered: list = field(default_factory=list)
    killed: list = field(default_factory=list)
    continued: list = field(default_factory=list)
    scale_requests: list = field(default_factory=list)
    approvals_requested: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    stop_triggered: bool = False
    stop_reason: str = ""

    def summary_lines(self) -> list[str]:
        return [
            f"tick @ {self.started_at:%Y-%m-%d %H:%M:%SZ}",
            f"  discovered:      {len(self.discovered)} ({len(self.skipped)} skipped)",
            f"  samples ordered: {len(self.sampled)}",
            f"  launched:        {len(self.launched)}",
            f"  metrics updated: {len(self.metrics_updated)}",
            f"  kill triggers:   {len(self.kill_triggered)}",
            f"  killed:          {len(self.killed)}",
            f"  continued:       {len(self.continued)}",
            f"  scale proposals: {len(self.scale_requests)}",
            f"  approvals asked: {len(self.approvals_requested)}",
        ]


class Orchestrator:
    def __init__(
        self,
        db: Database,
        config: Optional[Config] = None,
        scout: Optional[Scout] = None,
        metrics_provider: Optional[MetricsProvider] = None,
        notifier: Optional[Notifier] = None,
        gate: Optional[HumanGate] = None,
    ):
        self.db = db
        self.config = config or Config()
        self.notifier = notifier if notifier is not None else build_notifier(self.config)
        self.ledger = ExperimentLedger(db)
        self.cash = CashLedger(db, self.config.cash)
        self.econ = EconomicCalculator(self.config.cash, self.config.discovery)
        self.gate = gate or HumanGate(db, self.notifier, self.config.gate, self.config)
        self.scout = scout if scout is not None else build_scout(self.config)
        self.metrics = metrics_provider or ManualMetricsProvider()
        self.monitor = HealthMonitor(self.ledger, self.cash, self.config)

    # =====================================================================
    # 1. DISCOVER — autonomous, costs nothing
    # =====================================================================
    def discover(self, report: TickReport, limit: int = 20) -> None:
        candidates = self.scout.discover(limit=limit)
        created = 0
        for candidate in candidates:
            if created >= self.config.discovery.max_new_experiments_per_tick:
                break

            platform = (candidate.source_platform or "").lower()
            if platform not in self.config.gate.approved_platforms:
                # Adding a platform is a human decision (spec section 6).
                report.skipped.append((candidate.product_id, f"platform '{platform}' not approved"))
                approval = self.gate.request_platform(platform)
                if approval.is_pending:
                    report.approvals_requested.append(approval.id)
                continue

            if self.ledger.exists(candidate.product_id):
                continue

            worth_it, why = self.econ.is_worth_testing(candidate)
            if not worth_it:
                report.skipped.append((candidate.product_id, why))
                continue

            price = candidate.selling_price or self.econ.suggest_price(
                candidate.unit_cost, candidate.shipping_cost
            )
            experiment = Experiment(
                product_id=candidate.product_id,
                product_name=candidate.product_name,
                source_platform=candidate.source_platform,
                supplier=candidate.supplier,
                product_url=candidate.url,
                # Landed cost is the COGS the kill logic must use; shipping is
                # not free just because the supplier lists it separately.
                unit_cost=self.econ.landed_cost(candidate.unit_cost, candidate.shipping_cost),
                selling_price=price,
                status=Status.DISCOVERED.value,
            )
            stored = self.ledger.create_experiment(experiment, note=why)
            report.discovered.append(stored.id)
            created += 1

    # =====================================================================
    # 2. ADVANCE — only ever acts on approvals a human already granted
    # =====================================================================
    def advance_lifecycle(self, report: TickReport) -> None:
        for exp in self.ledger.by_status(Status.DISCOVERED.value):
            self._advance_discovered(exp, report)
        for exp in self.ledger.by_status(Status.SAMPLED.value):
            self._advance_sampled(exp, report)
        self._apply_kill_decisions(report)
        self._apply_scale_decisions(report)

    def _advance_discovered(self, exp: Experiment, report: TickReport) -> None:
        """DISCOVER -> SAMPLE. Ordering a sample is real cash."""
        units = 1
        cost = money(D(exp.unit_cost) * units)
        if self.gate.has_approval(ApprovalAction.ORDER_SAMPLE.value, exp.id, cost):
            approval = self.gate.require_approval(
                ApprovalAction.ORDER_SAMPLE.value, exp.id, cost
            )
            try:
                self.cash.assert_can_spend(cost)
            except InsufficientCash as exc:
                report.skipped.append((exp.product_id, f"sample approved but {exc}"))
                return
            self.cash.debit(
                f"Sample: {exp.product_name}",
                cost,
                CashCategory.SAMPLE_PURCHASE.value,
                experiment_id=exp.id,
            )
            self.ledger.mark_sampled(exp.id, f"sample ordered under approval {approval.id}")
            report.sampled.append(exp.id)
            return

        approval = self.gate.request_sample(exp, units, exp.unit_cost)
        if approval.is_pending:
            report.approvals_requested.append(approval.id)

    def _advance_sampled(self, exp: Experiment, report: TickReport) -> None:
        """SAMPLE -> LAUNCH. Needs *both* a listing approval (brand) and an ad
        spend approval (cash). Neither substitutes for the other."""
        listing_ok = self.gate.has_approval(ApprovalAction.LIST_PRODUCT.value, exp.id)
        if not listing_ok:
            approval = self.gate.request_listing(exp, exp.selling_price)
            if approval.is_pending:
                report.approvals_requested.append(approval.id)
            return

        spend_approval = self.gate.latest_approved(ApprovalAction.AD_SPEND.value, exp.id)
        if spend_approval is None:
            approval = self.gate.request_ad_spend(
                exp, self._starting_budget(), "Launch listing and start ads at low budget"
            )
            if approval.is_pending:
                report.approvals_requested.append(approval.id)
            return

        budget = spend_approval.amount or self._starting_budget()
        ok, why = self.cash.can_spend(budget)
        if not ok:
            report.skipped.append((exp.product_id, f"launch approved but {why}"))
            return

        self.ledger.mark_launched(exp.id, budget, f"launched under approval {spend_approval.id}")
        report.launched.append(exp.id)

    def _starting_budget(self) -> Decimal:
        """Spec 5.3 — ads start low ($5-10/day)."""
        return money(D("10.00"))

    def _apply_kill_decisions(self, report: TickReport) -> None:
        """Execute the human's KILL/CONTINUE answers. This is the only path to
        a killed experiment."""
        for approval in self.db.query(
            "SELECT * FROM approvals WHERE action = ? AND status IN (?, ?) "
            "ORDER BY id",
            (
                ApprovalAction.KILL_EXPERIMENT.value,
                ApprovalStatus.APPROVED.value,
                ApprovalStatus.REJECTED.value,
            ),
        ):
            exp_id = approval["experiment_id"]
            if exp_id is None:
                continue
            exp = self.ledger.get(exp_id)
            if exp is None or exp.status == Status.KILLED.value:
                continue
            if not exp.pending_kill_reason:
                continue  # decision already applied
            if approval["status"] == ApprovalStatus.APPROVED.value:
                self.ledger.kill(
                    exp_id, exp.pending_kill_reason, decided_by=approval.get("decided_by") or "operator"
                )
                report.killed.append(exp_id)
            else:
                self.ledger.continue_experiment(
                    exp_id, f"human chose CONTINUE on approval {approval['id']}"
                )
                report.continued.append(exp_id)

    def _spend_action_for(self, budget) -> str:
        """Below the threshold it is ordinary ad spend; above it, a scale-up
        that needs its own approval (spec section 6)."""
        return (
            ApprovalAction.SCALE_AD_SPEND.value
            if D(budget) > self.config.gate.scale_approval_daily_threshold
            else ApprovalAction.AD_SPEND.value
        )

    def _apply_scale_decisions(self, report: TickReport) -> None:
        """Raise budgets that a human approved. Both spend actions count: a
        $20/day increase is approved as `ad_spend`, an $80/day one as
        `scale_ad_spend`, and either must actually take effect."""
        for approval in self.db.query(
            "SELECT * FROM approvals WHERE action IN (?, ?) AND status = ? ORDER BY id",
            (
                ApprovalAction.SCALE_AD_SPEND.value,
                ApprovalAction.AD_SPEND.value,
                ApprovalStatus.APPROVED.value,
            ),
        ):
            exp_id = approval["experiment_id"]
            exp = self.ledger.get(exp_id) if exp_id else None
            if exp is None or not exp.is_live or exp.pending_kill_reason:
                continue
            if exp.ads_paused:
                # This path raises a *running* budget. It must never restart
                # ads the system paused — otherwise a stale launch approval
                # silently undoes a kill trigger's pause on the next tick.
                # Restarting spend is an explicit act: `mvv resume`.
                continue
            budget = D(approval["amount"] or 0)
            if budget <= 0 or D(exp.daily_ad_budget) >= budget:
                continue
            ok, why = self.cash.can_spend(budget)
            if not ok:
                report.skipped.append((exp.product_id, f"scale approved but {why}"))
                continue
            self.ledger.mark_scaling(exp_id, budget, approval["id"])

    # =====================================================================
    # 3. MEASURE — autonomous
    # =====================================================================
    def sync_metrics(self, report: TickReport, record_cash: bool = True) -> None:
        for exp in self.ledger.get_active():
            snapshot = self.metrics.fetch(exp)
            if snapshot.is_empty():
                continue
            before_ad_spend = D(exp.ad_spend)
            before_revenue = D(exp.revenue)
            updated = self.ledger.record_metrics(
                exp.id,
                impressions=snapshot.impressions,
                clicks=snapshot.clicks,
                sessions=snapshot.sessions,
                orders=snapshot.orders,
                revenue=snapshot.revenue,
                ad_spend=snapshot.ad_spend,
                refunds=snapshot.refunds,
                chargebacks=snapshot.chargebacks,
                avg_delivery_days=snapshot.avg_delivery_days,
                mode="set",
            )
            report.metrics_updated.append(exp.id)
            if record_cash:
                self._book_cash_delta(updated, before_ad_spend, before_revenue)

    def _book_cash_delta(
        self, exp: Experiment, before_ad_spend: Decimal, before_revenue: Decimal
    ) -> None:
        """Money the platforms already moved. Recording is not spending — the
        ad account has already been charged by the time we read the number."""
        ad_delta = D(exp.ad_spend) - before_ad_spend
        if ad_delta > 0:
            self.cash.debit(
                f"Ad spend: {exp.product_name}",
                ad_delta,
                CashCategory.AD_SPEND.value,
                experiment_id=exp.id,
            )
        revenue_delta = D(exp.revenue) - before_revenue
        if revenue_delta > 0:
            self.cash.record_customer_payment(revenue_delta, experiment_id=exp.id)

    # =====================================================================
    # 4. EVALUATE — pausing is autonomous, killing is not
    # =====================================================================
    def evaluate(self, report: TickReport) -> None:
        for exp in self.ledger.get_active():
            if exp.pending_kill_reason:
                continue  # already waiting on a human
            should_kill, reason = should_kill_experiment(exp)
            if not should_kill:
                continue
            if reason == exp.kill_override_reason:
                continue  # a human already answered CONTINUE to this exact trigger

            # Stop the bleeding first, then ask. Never the other way round.
            self.ledger.flag_pending_kill(exp.id, reason)
            approval = self.gate.request_kill(self.ledger.require(exp.id), reason)
            report.kill_triggered.append({"experiment_id": exp.id, "reason": reason})
            report.approvals_requested.append(approval.id)

            if self.config.gate.block_on_approval:
                decided = self.gate.wait_for_decision(approval.id)
                if decided.is_approved:
                    self.ledger.kill(exp.id, reason, decided_by=decided.decided_by or "operator")
                    report.killed.append(exp.id)
                elif decided.status == ApprovalStatus.REJECTED.value:
                    self.ledger.continue_experiment(exp.id)
                    report.continued.append(exp.id)

    # =====================================================================
    # 5. SCALE — proposals only
    # =====================================================================
    def propose_scaling(self, report: TickReport) -> None:
        for candidate in self.monitor.scale_candidates():
            exp = self.ledger.require(candidate["experiment_id"])
            budget = candidate["suggested_budget"] or self._starting_budget()
            if self.gate.has_approval(self._spend_action_for(budget), exp.id, budget):
                continue
            ok, why = self.cash.can_spend(budget)
            if not ok:
                report.skipped.append((exp.product_id, f"scale not proposed: {why}"))
                continue
            approval = self.gate.request_ad_spend(
                exp,
                budget,
                f"Scale {exp.product_name}: contribution margin "
                f"{candidate['contribution_margin_pct']}% of revenue, "
                f"{fmt_money(candidate['current_budget'])}/day -> {fmt_money(budget)}/day",
            )
            report.scale_requests.append({"experiment_id": exp.id, "approval_id": approval.id})
            if approval.is_pending:
                report.approvals_requested.append(approval.id)

    # =====================================================================
    # 6. REPORT + STOP
    # =====================================================================
    def check_stop(self, report: TickReport) -> None:
        triggered, reason = self.monitor.check_stop_condition()
        report.stop_triggered = triggered
        report.stop_reason = reason
        if not triggered:
            return
        for exp in self.ledger.get_active():
            self.ledger.pause_ads(exp.id, "KILL_ALL stop condition")
        approval = self.gate.request_kill_all(
            reason, details={"experiments": self.ledger.count()}
        )
        if approval.is_pending:
            report.approvals_requested.append(approval.id)
            self.notifier.send("🛑 KILL_ALL STOP CONDITION", reason)

    # =====================================================================
    def tick(self, discover_limit: int = 20) -> TickReport:
        report = TickReport(started_at=utcnow())
        self.discover(report, limit=discover_limit)
        self.advance_lifecycle(report)
        self.sync_metrics(report)
        self.evaluate(report)
        self.propose_scaling(report)
        self.check_stop(report)
        report.alerts = self.monitor.daily_health_check()
        return report

    def run_forever(self, interval_s: Optional[int] = None) -> None:  # pragma: no cover
        interval = interval_s or self.config.tick_interval_s
        while True:
            report = self.tick()
            print("\n".join(report.summary_lines()))
            print(format_alerts(report.alerts))
            if report.stop_triggered:
                print(f"\n{report.stop_reason}\nHalting the loop.")
                return
            time.sleep(interval)
