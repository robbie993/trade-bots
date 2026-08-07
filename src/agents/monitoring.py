"""Monitoring & alerts — spec section 8.

Read-only. Produces alerts and reports; changes nothing. Monitoring that can
also act is monitoring you cannot trust to tell you the truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..agents.cash_ledger import CashLedger
from ..config import Config, StopConfig
from ..db.connection import to_datetime, utcnow
from ..agents.economic_calculator import EconomicCalculator
from ..kill_criteria import missing_sample, should_kill_experiment
from ..agents.experiment_ledger import ExperimentLedger
from ..models import CashCategory, Status
from ..money import D, ZERO, fmt_money, fmt_pct, money, ratio_pct


@dataclass
class Alert:
    level: str  # 'info' | 'warn' | 'critical'
    experiment_id: Optional[int]
    message: str

    ICONS = {"info": "✅", "warn": "⚠️", "critical": "🚨"}

    def __str__(self) -> str:
        return f"{self.ICONS.get(self.level, '·')} {self.message}"


class HealthMonitor:
    def __init__(
        self,
        ledger: ExperimentLedger,
        cash: CashLedger,
        config: Optional[Config] = None,
    ):
        self.ledger = ledger
        self.cash = cash
        self.config = config or Config()
        self.econ = EconomicCalculator(self.config.cash, self.config.discovery)

    # -- spec 8.1 ---------------------------------------------------------
    def daily_health_check(self, when: Optional[datetime] = None) -> list[Alert]:
        when = when or utcnow()
        alerts: list[Alert] = []

        cash = self.cash.status(when)
        if cash["negative"]:
            alerts.append(
                Alert("critical", None, f"CASH NEGATIVE: {fmt_money(cash['available_cash'])} available")
            )
        elif cash["buffer_breached"]:
            alerts.append(
                Alert(
                    "critical",
                    None,
                    f"Emergency buffer breached: {fmt_money(cash['available_cash'])} available, "
                    f"buffer is {fmt_money(cash['emergency_buffer'])}",
                )
            )
        else:
            alerts.append(
                Alert(
                    "info",
                    None,
                    f"Cash: {fmt_money(cash['available_cash'])} available "
                    f"({fmt_money(cash['held'])} held, {fmt_money(cash['spendable'])} spendable)",
                )
            )

        for exp in self.ledger.get_active():
            if exp.pending_kill_reason:
                alerts.append(
                    Alert(
                        "critical",
                        exp.id,
                        f"{exp.product_name}: awaiting human decision — {exp.pending_kill_reason}",
                    )
                )
                continue

            if not exp.meets_sample_gate:
                gap = missing_sample(exp)
                if exp.orders >= self.config.stop.early_warning_orders:
                    alerts.append(
                        Alert(
                            "warn",
                            exp.id,
                            f"{exp.product_name}: {exp.orders} orders, need {exp.minimum_orders} "
                            f"for a decision ({gap['orders']} to go)",
                        )
                    )
                else:
                    alerts.append(
                        Alert(
                            "info",
                            exp.id,
                            f"{exp.product_name}: collecting data — need "
                            f"{gap['impressions']} impressions, {gap['sessions']} sessions, "
                            f"{gap['orders']} orders",
                        )
                    )
                continue

            should_kill, reason = should_kill_experiment(exp)
            if should_kill:
                alerts.append(Alert("critical", exp.id, f"{exp.product_name}: KILL trigger — {reason}"))
            else:
                alerts.append(
                    Alert(
                        "info",
                        exp.id,
                        f"{exp.product_name}: healthy — contribution "
                        f"{fmt_money(exp.contribution_margin)} ({fmt_pct(exp.contribution_margin_pct)} "
                        f"of revenue), CAC {fmt_money(exp.cac)}",
                    )
                )

        if not self.ledger.get_active():
            alerts.append(Alert("info", None, "No live experiments"))

        return alerts

    # -- spec 8.2 ---------------------------------------------------------
    def weekly_summary(self, when: Optional[datetime] = None) -> dict:
        when = when or utcnow()
        week_ago = when - timedelta(days=7)
        all_experiments = self.ledger.all()
        killed = [e for e in all_experiments if e.status == Status.KILLED.value]
        killed_this_week = [
            e for e in killed if e.killed_at and to_datetime(e.killed_at) >= week_ago
        ]
        active = [e for e in all_experiments if e.is_live]
        scaling = [e for e in all_experiments if e.status == Status.SCALING.value]

        cash = self.cash.status(when)
        pnl = self.econ.store_pnl(all_experiments)

        next_decisions = []
        for exp in active:
            if exp.meets_sample_gate:
                next_decisions.append(
                    {"experiment_id": exp.id, "product": exp.product_name, "due": "now — gates met"}
                )
            else:
                gap = missing_sample(exp)
                next_decisions.append(
                    {
                        "experiment_id": exp.id,
                        "product": exp.product_name,
                        "due": f"needs {gap['orders']} more orders",
                    }
                )

        return {
            "as_of": when,
            "total_experiments": len(all_experiments),
            "active": len(active),
            "scaling": len(scaling),
            "killed_total": len(killed),
            "killed_this_week": [
                {"product": e.product_name, "reason": e.kill_reason} for e in killed_this_week
            ],
            "kill_reasons": _tally(e.kill_reason for e in killed),
            "store_pnl": pnl,
            "cash": cash,
            "next_decisions": next_decisions,
            "success_criteria": self.success_criteria(),
        }

    # -- spec sections 1.2 and 14.1 ---------------------------------------
    def success_criteria(self) -> dict:
        """Where Phase 1 stands against its own pass/fail bar."""
        experiments = self.ledger.all()
        decided = [e for e in experiments if e.meets_sample_gate]
        killed = [e for e in experiments if e.status == Status.KILLED.value]
        profitable = [e for e in experiments if e.contribution_margin > 0]
        # "Kill decision accuracy": of the killed experiments that had enough
        # data to judge, how many were genuinely losing money.
        judged = [e for e in killed if e.meets_sample_gate]
        correct = [e for e in judged if e.contribution_margin <= 0]
        pnl = self.econ.store_pnl(experiments)
        cash_ever_negative = self.cash.available_cash() < 0

        return {
            "experiments_run": {
                "value": len(experiments),
                "target": 10,
                "pass": len(experiments) >= 10,
            },
            "data_quality": {
                "value": ratio_pct(len(decided), len(experiments)) if experiments else ZERO,
                "target": D("80"),
                "pass": bool(experiments) and ratio_pct(len(decided), len(experiments)) >= 80,
            },
            "kill_accuracy": {
                "value": ratio_pct(len(correct), len(judged)) if judged else ZERO,
                "target": D("70"),
                "pass": bool(judged) and ratio_pct(len(correct), len(judged)) > 70,
                "sample": len(judged),
            },
            "cash_survival": {"value": not cash_ever_negative, "pass": not cash_ever_negative},
            # Spec 1.2 / 14.1: "Profitable Products Found — at least 1".
            # This is the criterion Phase 1 actually turns on: one product that
            # makes money after all costs is the whole thesis.
            "profitable_products": {
                "value": len(profitable),
                "target": 1,
                "pass": len(profitable) >= 1,
                "products": [e.product_name for e in profitable],
            },
            "store_profitable": {
                "value": pnl["contribution_margin"],
                "pass": pnl["contribution_margin"] > 0,
            },
        }

    # -- stop conditions (spec 1.3 and 14.3) ------------------------------
    def stop_conditions(
        self, stop: Optional[StopConfig] = None, when: Optional[datetime] = None
    ) -> list[dict]:
        """Evaluate all four KILL_ALL conditions.

        Returns one dict per condition — triggered or not — so the operator
        sees the whole board, not just the first thing that fired. This is the
        machinery that stops the system from voting itself alive.
        """
        stop = stop or self.config.stop
        when = when or utcnow()
        return [
            self._stop_no_profitable_products(stop),
            self._stop_cash_negative(stop, when),
            self._stop_stalled_kill_approval(stop, when),
            self._stop_ad_spend_outruns_revenue(stop, when),
        ]

    def check_stop_condition(
        self, stop: Optional[StopConfig] = None, when: Optional[datetime] = None
    ) -> tuple[bool, str]:
        """(triggered, reason) across every stop condition. Any one ends it."""
        conditions = self.stop_conditions(stop, when)
        fired = [c for c in conditions if c["triggered"]]
        if fired:
            return True, "KILL_ALL: " + " | ".join(c["detail"] for c in fired)
        return False, "; ".join(c["detail"] for c in conditions)

    def _stop_no_profitable_products(self, stop: StopConfig) -> dict:
        """1. After N experiments, not one has a positive contribution margin.

        Note this is per *experiment*, not the store total (spec 14.3): one
        genuine winner among twenty means the system works and the losers were
        the cost of finding it.
        """
        experiments = self.ledger.all()
        profitable = [e for e in experiments if e.contribution_margin > 0]
        name = "no_profitable_products"
        if len(experiments) < stop.kill_all_after_experiments:
            return {
                "condition": name,
                "triggered": False,
                "detail": f"{len(experiments)}/{stop.kill_all_after_experiments} experiments run",
            }
        if profitable:
            return {
                "condition": name,
                "triggered": False,
                "detail": (
                    f"{len(profitable)} of {len(experiments)} experiments profitable"
                ),
            }
        return {
            "condition": name,
            "triggered": True,
            "detail": (
                f"{len(experiments)} experiments run and not one has a positive contribution "
                f"margin. The system has not found a profitable product."
            ),
        }

    def _stop_cash_negative(self, stop: StopConfig, when: datetime) -> dict:
        """2. Cash flow negative for N consecutive days."""
        streak = self.cash.consecutive_negative_days(when)
        triggered = streak >= stop.cash_negative_days
        return {
            "condition": "cash_negative_streak",
            "triggered": triggered,
            "detail": (
                f"cash negative {streak} consecutive day(s), limit {stop.cash_negative_days}"
                if streak
                else "cash not negative"
            ),
        }

    def _stop_stalled_kill_approval(self, stop: StopConfig, when: datetime) -> dict:
        """3. A kill trigger fired and nobody answered within N hours.

        An unanswered kill request is the human gate failing. The gate is the
        whole safety model, so a gate that nobody is watching stops the system
        rather than quietly becoming decoration.
        """
        deadline = when - timedelta(hours=stop.kill_approval_timeout_hours)
        stalled = []
        for row in self.ledger.db.query(
            "SELECT id, experiment_id, requested_at FROM human_approvals "
            "WHERE action = ? AND status = ? ORDER BY id",
            ("kill", "pending"),
        ):
            requested = to_datetime(row["requested_at"])
            if requested is not None and requested < deadline:
                stalled.append(row)
        if not stalled:
            return {
                "condition": "stalled_kill_approval",
                "triggered": False,
                "detail": "no kill decision overdue",
            }
        ids = ", ".join(f"#{r['experiment_id']}" for r in stalled)
        return {
            "condition": "stalled_kill_approval",
            "triggered": True,
            "detail": (
                f"{len(stalled)} kill decision(s) unanswered for more than "
                f"{stop.kill_approval_timeout_hours}h ({ids})"
            ),
        }

    def _stop_ad_spend_outruns_revenue(self, stop: StopConfig, when: datetime) -> dict:
        """4. Ad costs exceeded revenue by Nx over the trailing window.

        Read as: over the trailing 30 days, total ad spend > 2x total revenue.
        Measured from the cash ledger, which is where both actually land, and
        only once the window has that much history — otherwise a store three
        days old would trip it before making its first sale.
        """
        window_start = when - timedelta(days=stop.ad_revenue_window_days)
        name = "ad_spend_outruns_revenue"
        entries = self.cash.entries()
        earliest = min((to_datetime(e.date) for e in entries if e.date), default=None)
        if earliest is None or earliest > window_start:
            return {
                "condition": name,
                "triggered": False,
                "detail": f"less than {stop.ad_revenue_window_days} days of cash history",
            }

        ad_spend = self.cash.flow_between(CashCategory.AD_SPEND.value, window_start, when)
        revenue = self.cash.flow_between(CashCategory.CUSTOMER_REVENUE.value, window_start, when)
        limit = revenue * stop.ad_revenue_ratio_limit
        triggered = ad_spend > limit and ad_spend > 0
        return {
            "condition": name,
            "triggered": triggered,
            "detail": (
                f"trailing {stop.ad_revenue_window_days}d: ads {fmt_money(ad_spend)} vs revenue "
                f"{fmt_money(revenue)} (limit {fmt_money(limit)})"
            ),
        }

    def scale_candidates(self, stop: Optional[StopConfig] = None) -> list[dict]:
        """Spec 5.6 — experiments healthy enough to deserve more budget.

        Returns candidates only. Raising spend is a human decision.
        """
        stop = stop or self.config.stop
        out = []
        for exp in self.ledger.get_active():
            if not exp.meets_sample_gate:
                continue
            should_kill, _ = should_kill_experiment(exp)
            if should_kill or exp.pending_kill_reason:
                continue
            if exp.contribution_margin_pct >= stop.scale_min_contribution_margin_pct:
                out.append(
                    {
                        "experiment_id": exp.id,
                        "product": exp.product_name,
                        "contribution_margin": exp.contribution_margin,
                        "contribution_margin_pct": exp.contribution_margin_pct,
                        "current_budget": exp.daily_ad_budget,
                        "suggested_budget": money(D(exp.daily_ad_budget) * 2),
                    }
                )
        return out


def _tally(values) -> dict:
    counts: dict = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def format_alerts(alerts: list) -> str:
    return "\n".join(str(a) for a in alerts) if alerts else "no alerts"


def format_weekly_summary(summary: dict) -> str:
    cash = summary["cash"]
    pnl = summary["store_pnl"]
    lines = [
        f"WEEKLY SUMMARY — {summary['as_of']:%Y-%m-%d}",
        "",
        f"Experiments: {summary['total_experiments']} total, {summary['active']} active, "
        f"{summary['scaling']} scaling, {summary['killed_total']} killed",
    ]
    if summary["killed_this_week"]:
        lines.append("Killed this week:")
        lines += [f"  - {k['product']}: {k['reason']}" for k in summary["killed_this_week"]]
    if summary["kill_reasons"]:
        lines.append("Kill reasons to date: " + ", ".join(f"{k} ({v})" for k, v in summary["kill_reasons"].items()))
    lines += [
        "",
        f"Store P&L: revenue {fmt_money(pnl['revenue'])}, COGS {fmt_money(pnl['cogs'])}, "
        f"ads {fmt_money(pnl['ad_spend'])}",
        f"Contribution margin: {fmt_money(pnl['contribution_margin'])} "
        f"({fmt_pct(pnl['contribution_margin_pct'])})",
        "",
        f"Cash: {fmt_money(cash['available_cash'])} available, {fmt_money(cash['held'])} held, "
        f"{fmt_money(cash['spendable'])} spendable after buffer",
    ]
    if cash["upcoming_releases"]:
        lines.append("Releases in the next 30 days:")
        lines += [
            f"  - {r['release_date']:%Y-%m-%d}: {fmt_money(r['amount'])} ({r['description']})"
            for r in cash["upcoming_releases"][:10]
        ]
    if summary["next_decisions"]:
        lines.append("")
        lines.append("Next decision points:")
        lines += [f"  - #{d['experiment_id']} {d['product']}: {d['due']}" for d in summary["next_decisions"]]

    lines += ["", "Phase 1 success criteria:"]
    for name, crit in summary["success_criteria"].items():
        mark = "PASS" if crit["pass"] else "not yet"
        lines.append(f"  - {name}: {crit['value']} [{mark}]")
    return "\n".join(lines)
