"""Monitoring & alerts — spec section 8.

Read-only. Produces alerts and reports; changes nothing. Monitoring that can
also act is monitoring you cannot trust to tell you the truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .cash import CashLedger
from .config import Config, StopConfig
from .db import to_datetime, utcnow
from .economics import EconomicCalculator
from .kill_criteria import missing_sample, should_kill_experiment
from .ledger import ExperimentLedger
from .models import Status
from .money import D, ZERO, fmt_money, fmt_pct, money, ratio_pct


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

    # -- spec section 9 ---------------------------------------------------
    def success_criteria(self) -> dict:
        """Where Phase 1 stands against its own pass/fail bar."""
        experiments = self.ledger.all()
        decided = [e for e in experiments if e.meets_sample_gate]
        killed = [e for e in experiments if e.status == Status.KILLED.value]
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
            "store_profitable": {
                "value": pnl["contribution_margin"],
                "pass": pnl["contribution_margin"] > 0,
            },
        }

    def check_stop_condition(self, stop: Optional[StopConfig] = None) -> tuple[bool, str]:
        """Spec 9 — KILL_ALL if the store is not cash-flow positive after N runs.

        The system must be able to conclude that it does not work. This is the
        check that stops it from voting itself alive.
        """
        stop = stop or self.config.stop
        experiments = self.ledger.all()
        if len(experiments) < stop.kill_all_after_experiments:
            return False, (
                f"{len(experiments)}/{stop.kill_all_after_experiments} experiments run"
            )
        pnl = self.econ.store_pnl(experiments)
        if pnl["contribution_margin"] > 0:
            return False, (
                f"{len(experiments)} experiments, store contribution "
                f"{fmt_money(pnl['contribution_margin'])} — positive"
            )
        return True, (
            f"KILL_ALL: {len(experiments)} experiments run and store contribution margin is "
            f"{fmt_money(pnl['contribution_margin'])}. The system has not found a profitable "
            f"product. Stop and start over."
        )

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
