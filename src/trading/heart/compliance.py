"""Compliance — the record, not the judgement.

The conscience decides. This module makes the decision *inspectable*: it
keeps the per-session order log the fairness foundation reads, writes every
warn and block to ``brokerage_events``, and produces the compliance section
of the audit report.

Separating them matters. A conscience that also owns its own audit trail can
quietly stop writing the entries that would embarrass it; here, the log is
written by the layer that did not make the decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import EthicsVerdict, TradeProposal
from ..store import TradingStore
from .conscience import EthicsReview


@dataclass
class ComplianceLog:
    """This session's orders, per firm — the input to the fairness check."""

    by_firm: dict = field(default_factory=dict)

    def record(self, proposal: TradeProposal) -> None:
        self.by_firm.setdefault(proposal.firm_id, []).append(proposal)

    def recent(self, firm_id: Optional[int]) -> list:
        return list(self.by_firm.get(firm_id, []))

    def clear(self) -> None:
        self.by_firm.clear()


class ComplianceOfficer:
    def __init__(self, store: TradingStore):
        self.store = store
        self.log = ComplianceLog()

    def record_review(
        self, proposal: TradeProposal, review: EthicsReview, firm_key: str = ""
    ) -> None:
        """Log the order, and persist anything that was not a clean allow."""
        self.log.record(proposal)
        if review.verdict == EthicsVerdict.ALLOW.value:
            return
        self.store.record_event(
            "ethics",
            f"{firm_key or proposal.firm_id} {proposal.side} {proposal.quantity} "
            f"{proposal.symbol}: {review.verdict.upper()} — {review.reason}",
            firm_id=proposal.firm_id,
            payload={
                "symbol": proposal.symbol,
                "side": proposal.side,
                "quantity": str(proposal.quantity),
                "notional": str(proposal.notional),
                "verdict": review.verdict,
                "findings": review.table(),
            },
        )

    def report(self, limit: int = 100) -> dict:
        events = self.store.events(limit=limit, event_type="ethics")
        blocks = [e for e in events if "BLOCK" in (e.get("detail") or "")]
        warns = [e for e in events if "WARN" in (e.get("detail") or "")]
        return {
            "reviewed": len(events),
            "blocked": len(blocks),
            "warned": len(warns),
            "recent": [e.get("detail") for e in events[:10]],
        }

    def render(self, limit: int = 100) -> str:
        report = self.report(limit)
        lines = [
            f"ethics events: {report['reviewed']} "
            f"({report['blocked']} blocked, {report['warned']} warned)"
        ]
        lines.extend(f"  - {detail}" for detail in report["recent"])
        return "\n".join(lines)


__all__ = ["ComplianceLog", "ComplianceOfficer"]
