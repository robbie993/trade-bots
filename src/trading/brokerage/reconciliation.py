"""Reconciliation — the gate every brokerage action passes through.

From the build document: *the ledger must reconcile before anything is
published*. Here that is literal. ``Brokerage`` calls ``check`` first and
refuses to allocate, kill, publish a leaderboard or evolve anything while a
firm's books do not add up, because every one of those actions is a decision
made *from* the books.

Two independent identities are checked per firm. They are derived from
different columns, so a bug that fakes one will not fake the other:

    cash    = allocation + Σ fill.cash_delta
    equity  = allocation + Σ realized_pnl + unrealized_pnl - Σ fees

Tolerance exists because prices carry eight decimals and cash carries two, so
each fill can legitimately quantise by up to a cent. The allowance therefore
grows with the number of fills; anything beyond that is a real break, and a
real break stops the layer rather than being rounded away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ...money import D, ZERO, fmt_money, money
from ..config import BrokerageConfig
from ..data.market_data import MarketData
from ..models import FirmRecord
from ..store import TradingStore


@dataclass
class Break:
    firm_key: str
    identity: str
    expected: Decimal
    actual: Decimal

    @property
    def difference(self) -> Decimal:
        return money(self.actual - self.expected)

    def __str__(self) -> str:
        return (
            f"{self.firm_key}: {self.identity} expected {fmt_money(self.expected)}, "
            f"found {fmt_money(self.actual)} (off by {fmt_money(self.difference)})"
        )


@dataclass
class ReconciliationReport:
    breaks: list = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.breaks

    def summary(self) -> str:
        if self.ok:
            return f"reconciled: {self.checked} firm(s), no breaks"
        return "\n".join([f"RECONCILIATION FAILED ({len(self.breaks)} break(s)):", *map(str, self.breaks)])


class Reconciler:
    def __init__(self, store: TradingStore, config: Optional[BrokerageConfig] = None):
        self.store = store
        self.config = config or BrokerageConfig()

    def tolerance_for(self, fill_count: int) -> Decimal:
        """One cent of quantisation per fill, on top of the configured floor."""
        return money(max(self.config.reconcile_tolerance, D("0.01") * D(fill_count)))

    def check_firm(self, firm: FirmRecord, market: MarketData) -> list:
        fills = self.store.fills(firm.id)
        tolerance = self.tolerance_for(len(fills))
        breaks: list = []

        cash_expected = money(D(firm.allocation) + self.store.cash_delta_total(firm.id))
        if abs(cash_expected - D(firm.cash)) > tolerance:
            breaks.append(Break(firm.firm_key, "cash", cash_expected, D(firm.cash)))

        positions = self.store.positions(firm.id)
        market_value = sum((p.market_value(market.mark(p.symbol)) for p in positions), ZERO)
        unrealized = sum((p.unrealized_pnl(market.mark(p.symbol)) for p in positions), ZERO)
        equity = money(D(firm.cash) + market_value)
        equity_expected = money(
            D(firm.allocation)
            + self.store.realized_pnl(firm.id)
            + unrealized
            - self.store.fees_paid(firm.id)
        )
        if abs(equity_expected - equity) > tolerance:
            breaks.append(Break(firm.firm_key, "equity", equity_expected, equity))

        return breaks

    def check(self, firms, market: MarketData) -> ReconciliationReport:
        report = ReconciliationReport()
        for firm in firms:
            report.checked += 1
            report.breaks.extend(self.check_firm(firm, market))
        return report


class LedgerNotReconciled(RuntimeError):
    """Raised when an action is attempted on books that do not add up."""

    def __init__(self, report: ReconciliationReport):
        super().__init__(report.summary())
        self.report = report


__all__ = ["Break", "LedgerNotReconciled", "ReconciliationReport", "Reconciler"]
