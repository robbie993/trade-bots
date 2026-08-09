"""The conscience — six moral foundations, applied to a trade.

The build document points at ``lex-conscience`` for this. That repository is
not reachable (the GitHub path 404s), so the foundation model it describes is
implemented here directly, in the same shape: six foundations, each returning
allow / warn / block with a reason, aggregated into one verdict.

Implementing it natively is also the better outcome for this particular job.
Every foundation below is a *specific* refusal about a *specific* trade, and
each one is a pure function of the proposal, the book and the market — no
model call, no network, and reproducible from the row that gets stored. An
ethics check that can be unavailable is not an ethics check.

The six, mapped onto a trading desk:

    care        don't destroy the capital you were entrusted with
    fairness    don't trade in ways that work by disadvantaging others
    loyalty     stay inside the mandate the operator actually gave you
    authority   respect the gates: approvals, venues, restricted lists
    sanctity    keep out of what the operator has declared off-limits
    liberty     never take a position the operator cannot get out of

A ``warn`` is recorded on the proposal and the trade continues. A ``block``
stops it. Set ``TRADE_ETHICS_STRICT=1`` and warns become blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from ...money import D, ZERO, fmt_money, money, percent
from ..config import HeartConfig
from ..data.market_data import MarketData
from ..models import EthicsVerdict, FirmRecord, Position, Side, TradeProposal
from .restrictions import AssetRestrictions

FOUNDATIONS = ("care", "fairness", "loyalty", "authority", "sanctity", "liberty")


@dataclass
class FoundationFinding:
    foundation: str
    verdict: str
    reason: str

    @property
    def blocks(self) -> bool:
        return self.verdict == EthicsVerdict.BLOCK.value

    @property
    def warns(self) -> bool:
        return self.verdict == EthicsVerdict.WARN.value


@dataclass
class EthicsReview:
    verdict: str = EthicsVerdict.ALLOW.value
    findings: list = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.verdict == EthicsVerdict.BLOCK.value

    @property
    def reason(self) -> str:
        notable = [f for f in self.findings if f.blocks] or [f for f in self.findings if f.warns]
        if not notable:
            return "no foundation objected"
        return "; ".join(f"{f.foundation}: {f.reason}" for f in notable)

    def table(self) -> list:
        return [
            {"foundation": f.foundation, "verdict": f.verdict, "reason": f.reason}
            for f in self.findings
        ]


class Conscience:
    def __init__(
        self,
        config: Optional[HeartConfig] = None,
        restrictions: Optional[AssetRestrictions] = None,
    ):
        self.config = config or HeartConfig()
        self.restrictions = (
            restrictions
            if restrictions is not None
            else AssetRestrictions.load(self.config.restrictions_file)
        )

    def review(
        self,
        proposal: TradeProposal,
        firm: FirmRecord,
        positions: Sequence[Position],
        market: MarketData,
        recent_proposals: Sequence[TradeProposal] = (),
    ) -> EthicsReview:
        findings = [
            self._care(proposal, firm, positions, market),
            self._fairness(proposal, recent_proposals),
            self._loyalty(proposal, firm),
            self._authority(proposal, firm),
            self._sanctity(proposal),
            self._liberty(proposal, market),
        ]
        review = EthicsReview(findings=findings)
        if any(f.blocks for f in findings):
            review.verdict = EthicsVerdict.BLOCK.value
        elif any(f.warns for f in findings):
            review.verdict = (
                EthicsVerdict.BLOCK.value
                if self.config.block_on_warn
                else EthicsVerdict.WARN.value
            )
        return review

    # -- the six ----------------------------------------------------------
    def _care(
        self,
        proposal: TradeProposal,
        firm: FirmRecord,
        positions: Sequence[Position],
        market: MarketData,
    ) -> FoundationFinding:
        """Harm to the person whose money this is."""
        if proposal.side_enum is Side.SELL:
            return _ok("care", "reducing exposure cannot harm the capital")

        equity = money(
            D(firm.cash)
            + sum((p.market_value(market.mark(p.symbol)) for p in positions if p.is_open), ZERO)
        )
        if equity <= 0:
            return _block("care", "the firm has no equity left to risk")

        held = next(
            (p.market_value(market.mark(p.symbol)) for p in positions
             if p.symbol == proposal.symbol and p.is_open),
            ZERO,
        )
        # Ordered hardest-first: a trade larger than the whole firm is a block
        # whatever the concentration rule would have said about it.
        if proposal.notional > equity:
            return _block(
                "care",
                f"trade notional {fmt_money(proposal.notional)} exceeds equity "
                f"{fmt_money(equity)}",
            )
        after = money(held + proposal.notional)
        share = percent(after / equity * D(100))
        if share > self.config.concentration_warn_pct:
            return _warn(
                "care",
                f"{proposal.symbol} would be {share}% of equity "
                f"(warn above {self.config.concentration_warn_pct}%)",
            )
        return _ok("care", f"{proposal.symbol} would be {share}% of equity")

    def _fairness(
        self, proposal: TradeProposal, recent: Sequence[TradeProposal]
    ) -> FoundationFinding:
        """Patterns that make money by disadvantaging other participants."""
        same_symbol = [p for p in recent if p.symbol == proposal.symbol]
        flips = 0
        previous = None
        for entry in same_symbol:
            if previous is not None and entry.side != previous:
                flips += 1
            previous = entry.side
        if flips > self.config.max_round_trips_per_session:
            return _block(
                "fairness",
                f"{flips} reversals in {proposal.symbol} this session reads as churn, "
                f"not trading (limit {self.config.max_round_trips_per_session})",
            )
        # Buying and selling the same name on the same timestamp moves no risk
        # and creates volume that misleads everyone reading the tape.
        for entry in same_symbol:
            if (
                entry.as_of is not None
                and proposal.as_of is not None
                and entry.as_of == proposal.as_of
                and entry.side != proposal.side
                and entry.quantity == proposal.quantity
            ):
                return _block(
                    "fairness",
                    f"offsetting {proposal.symbol} order at the same timestamp is a wash trade",
                )
        if flips == self.config.max_round_trips_per_session:
            return _warn("fairness", f"{flips} reversals in {proposal.symbol} this session")
        return _ok("fairness", "no manipulative pattern in this session's orders")

    def _loyalty(self, proposal: TradeProposal, firm: FirmRecord) -> FoundationFinding:
        """The mandate the operator actually gave this firm."""
        universe = [s.upper() for s in (firm.universe or [])]
        if universe and proposal.symbol.upper() not in universe:
            return _block(
                "loyalty",
                f"{proposal.symbol} is outside {firm.firm_key}'s mandated universe "
                f"({', '.join(universe)})",
            )
        return _ok("loyalty", "inside the firm's mandate")

    def _authority(self, proposal: TradeProposal, firm: FirmRecord) -> FoundationFinding:
        """The gates. A paused firm may exit and may not enter."""
        if firm.is_killed:
            return _block("authority", f"{firm.firm_key} has been killed by a human")
        if firm.status == "paused" and proposal.side_enum is Side.BUY:
            return _block("authority", f"{firm.firm_key} is paused; it may exit but not enter")
        if firm.venue != "paper" and proposal.side_enum is Side.BUY:
            return _warn(
                "authority",
                f"{firm.firm_key} trades on {firm.venue}; the venue itself must be "
                "separately approved before any order is sent",
            )
        return _ok("authority", "no gate stands in the way of this order")

    def _sanctity(self, proposal: TradeProposal) -> FoundationFinding:
        """What the operator has declared off-limits, for whatever reason.

        Two layers: the symbol list, then the categories the operator has
        classified this symbol into. Both come from the operator — nothing
        here knows what any company does, and a symbol with no classification
        is reported as unclassified rather than as clean.
        """
        symbol = proposal.symbol.upper()
        if symbol in set(self.config.restricted_symbols):
            return _block("sanctity", f"{proposal.symbol} is on the operator's restricted list")

        violations = self.restrictions.violations(symbol, self.config.restricted_categories)
        if violations:
            return _block(
                "sanctity",
                f"{proposal.symbol} is classified {', '.join(violations)}, which the "
                "operator has restricted",
            )

        if not self.config.restricted_categories:
            return _ok("sanctity", "not on the restricted list")
        known = self.restrictions.categories_for(symbol)
        if known:
            return _ok("sanctity", f"classified {', '.join(known)}; none restricted")
        return _ok(
            "sanctity",
            f"not on the restricted list ({proposal.symbol} is unclassified — category "
            "rules cannot be applied to it)",
        )

    def _liberty(self, proposal: TradeProposal, market: MarketData) -> FoundationFinding:
        """A position the operator cannot exit is a position that owns them."""
        bars = market.history(proposal.symbol, 20)
        volumes = [b.volume for b in bars if b.volume > 0]
        if not volumes:
            return _warn("liberty", f"no volume history for {proposal.symbol}; exit untested")
        typical = sum(volumes, ZERO) / D(len(volumes))
        if typical <= 0:
            return _warn("liberty", f"{proposal.symbol} shows no traded volume")
        share = percent(D(proposal.quantity) / typical * D(100))
        if share > D(10):
            return _block(
                "liberty",
                f"order is {share}% of {proposal.symbol}'s typical daily volume; "
                "the position could not be exited quickly",
            )
        if share > D(2):
            return _warn(
                "liberty", f"order is {share}% of typical daily volume in {proposal.symbol}"
            )
        return _ok("liberty", f"{share}% of typical daily volume")


def _ok(foundation: str, reason: str) -> FoundationFinding:
    return FoundationFinding(foundation, EthicsVerdict.ALLOW.value, reason)


def _warn(foundation: str, reason: str) -> FoundationFinding:
    return FoundationFinding(foundation, EthicsVerdict.WARN.value, reason)


def _block(foundation: str, reason: str) -> FoundationFinding:
    return FoundationFinding(foundation, EthicsVerdict.BLOCK.value, reason)


__all__ = ["Conscience", "EthicsReview", "FOUNDATIONS", "FoundationFinding"]
