"""What the council is allowed to know before it rules.

Every fact here is read from the ledger the rest of the system reads. Nothing
is passed in from the caller and nothing is inferred from the request text: a
body that decides on the strength of what it was *told* is a rubber stamp with
extra steps.

The gathering is deliberately separate from the ruling. It means a ruling can
be reproduced months later from stored evidence, and it means the jurors are
pure functions of a dataclass — which is why they are testable at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ...money import D, ZERO, money


@dataclass
class CouncilEvidence:
    """The state of the village at the moment a decision was asked for."""

    action: str
    firm_key: str = ""
    kind: str = ""

    # The one precondition that outranks every other consideration.
    books_reconcile: bool = True
    reconciliation: str = ""

    # The firm, as scored.
    firm_exists: bool = False
    status: str = ""
    sufficient_data: bool = False
    closed_trades: int = 0
    score: Decimal = ZERO
    return_pct: Decimal = ZERO
    drawdown_pct: Decimal = ZERO
    win_rate_pct: Decimal = ZERO
    equity: Decimal = ZERO

    # Capital.
    allocation: Decimal = ZERO
    initial_allocation: Decimal = ZERO
    new_allocation: Decimal = ZERO
    headroom: Decimal = ZERO
    withdrawable: Decimal = ZERO

    # The counterparty on a transfer.
    from_firm: str = ""
    from_firm_withdrawable: Decimal = ZERO

    # History: what the village has already done about this firm.
    kill_conditions: tuple = ()
    tripped: tuple = ()
    pause_reason: str = ""
    recent_council_grants: int = 0
    open_positions: int = 0

    # Configured bands, carried so a stored ruling can be read back without
    # guessing which thresholds were in force at the time.
    good_score: Decimal = ZERO
    poor_score: Decimal = ZERO
    kill_score: Decimal = ZERO
    max_allocation: Decimal = ZERO
    notes: dict = field(default_factory=dict)

    @property
    def delta(self) -> Decimal:
        return money(D(self.new_allocation) - D(self.allocation))

    def to_dict(self) -> dict:
        out = {}
        for key, value in self.__dict__.items():
            out[key] = str(value) if isinstance(value, Decimal) else value
        return out


def gather(eco, approval, market=None) -> CouncilEvidence:
    """Read the ledger for everything a ruling on ``approval`` depends on."""
    from ...agents.human_gate import ApprovalAction

    details = approval.details or {}
    evidence = CouncilEvidence(
        action=approval.action,
        firm_key=details.get("firm", "") or "",
        kind=details.get("kind", "") or "",
        from_firm=details.get("from_firm", "") or "",
        pause_reason=details.get("reason", "") or "",
    )

    market = market if market is not None else eco.market()
    reconciliation = eco.brokerage.reconcile(market)
    evidence.books_reconcile = reconciliation.ok
    evidence.reconciliation = reconciliation.summary()

    config = eco.config.brokerage
    evidence.good_score = D(config.good_score)
    evidence.poor_score = D(config.poor_score)
    evidence.kill_score = D(config.kill_score)

    firms = eco.store.firms()
    deployed = sum((D(f.allocation) for f in firms if not f.is_killed), ZERO)
    evidence.headroom = money(D(config.total_capital) - deployed)

    firm = eco.store.get_firm(evidence.firm_key) if evidence.firm_key else None
    if firm is None:
        return evidence

    evidence.firm_exists = True
    evidence.status = firm.status
    evidence.allocation = D(firm.allocation)
    evidence.initial_allocation = D(firm.initial_allocation)
    evidence.max_allocation = money(
        D(firm.initial_allocation) * D(config.max_allocation_multiple)
    )
    purse = eco.store.cash_view(firm, cash_floor_pct=eco.config.firm.cash_floor_pct)
    evidence.withdrawable = purse.withdrawable
    evidence.open_positions = len(eco.store.positions(firm.id, open_only=True))

    if details.get("new_allocation"):
        evidence.new_allocation = D(details["new_allocation"])
    elif details.get("amount") and approval.action == ApprovalAction.ALLOCATE_CAPITAL.value:
        evidence.new_allocation = money(D(firm.allocation) + D(details["amount"]))

    card = eco.brokerage.evaluator.evaluate(firm, market)
    if card is not None:
        evidence.sufficient_data = card.sufficient_data
        evidence.closed_trades = card.closed_trades
        evidence.score = D(card.score)
        evidence.return_pct = D(card.return_pct)
        evidence.drawdown_pct = D(card.drawdown_pct)
        evidence.win_rate_pct = D(card.win_rate_pct)
        evidence.equity = D(card.equity)

    if card is not None:
        from ..firms.kill_switch import INSUFFICIENT_DATA, kill_check_table, should_kill_firm

        metrics = card.to_metrics()
        table = kill_check_table(metrics, eco.config.kill)
        evidence.kill_conditions = tuple(row["condition"] for row in table)
        evidence.tripped = tuple(row["condition"] for row in table if row["triggered"])
        should, reason = should_kill_firm(metrics, eco.config.kill)
        evidence.notes["kill_reason"] = reason or ""
        evidence.notes["kill_condition_met"] = bool(
            should and reason != INSUFFICIENT_DATA
        )

    if evidence.from_firm:
        seller = eco.store.get_firm(evidence.from_firm)
        if seller is not None:
            evidence.from_firm_withdrawable = eco.store.cash_view(
                seller, cash_floor_pct=eco.config.firm.cash_floor_pct
            ).withdrawable

    evidence.recent_council_grants = _recent_grants(eco.db, evidence.firm_key, approval.action)
    return evidence


def _recent_grants(db, firm_key: str, action: str, window: int = 5) -> int:
    """How many of the last few rulings on this firm and action were granted.

    A council that grants the same firm a raise every tick is compounding, and
    compounding is exactly the failure the human gate was there to prevent.
    """
    try:
        rows = db.query(
            "SELECT verdict FROM council_rulings WHERE firm_key = ? AND action = ? "
            "ORDER BY id DESC LIMIT ?",
            (firm_key, action, window),
        )
    except Exception:  # noqa: BLE001 - an unmigrated database has no history
        return 0
    return sum(1 for r in rows if (r.get("verdict") or "") == "grant")


__all__ = ["CouncilEvidence", "gather"]
