"""The Heart — one object the ecosystem asks before every fill.

``Heart.consider`` runs the conscience over a proposal, stamps the verdict and
its reason onto the proposal itself, and hands the compliance officer the
record. The proposal carries the verdict into the database whether it was
allowed, warned or blocked, so the ethics decision is part of the trade's
permanent record rather than a line in a log file that rotates away.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..config import HeartConfig, TradingConfig
from ..data.market_data import MarketData
from ..models import EthicsVerdict, FirmRecord, Position, ProposalStatus, TradeProposal
from ..store import TradingStore
from .compliance import ComplianceOfficer
from .conscience import Conscience, EthicsReview


class Heart:
    def __init__(
        self,
        store: TradingStore,
        config: Optional[TradingConfig] = None,
    ):
        self.store = store
        self.config = config or TradingConfig()
        self.conscience = Conscience(self.config.heart)
        self.compliance = ComplianceOfficer(store)

    @property
    def heart_config(self) -> HeartConfig:
        return self.config.heart

    def consider(
        self,
        proposal: TradeProposal,
        firm: FirmRecord,
        positions: Sequence[Position],
        market: MarketData,
    ) -> EthicsReview:
        review = self.conscience.review(
            proposal,
            firm,
            positions,
            market,
            recent_proposals=self.compliance.log.recent(firm.id),
        )
        proposal.ethics_verdict = review.verdict
        proposal.ethics_reason = review.reason
        if review.blocked:
            proposal.status = ProposalStatus.BLOCKED.value
        self.compliance.record_review(proposal, review, firm.firm_key)
        return review

    def new_session(self) -> None:
        """Start a fresh session for the churn and wash-trade checks."""
        self.compliance.log.clear()


__all__ = ["EthicsVerdict", "Heart"]
