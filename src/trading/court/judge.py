"""The judge — three rulings, and what each one actually means.

    REJECT   this file does not come near the system
    MODIFY   the idea may be sound, the submission is not usable as it stands
    ACCEPT   admitted as a *candidate* genome

ACCEPT is deliberately weaker than it sounds, and that is the whole design.
An accepted strategy is written to ``strategy_genomes`` unselected. It reaches
a firm only by beating that firm's incumbent in the evolver, on the same data,
or by a human deploying it by hand. Nothing a court approves starts trading
because the court approved it — a file that arrived this morning has no track
record, and a ruling is not one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ...money import D, ZERO, percent
from .advocates import Case
from .jury import Finding

ACCEPT = "accept"
MODIFY = "modify"
REJECT = "reject"


@dataclass
class Ruling:
    verdict: str
    reason: str
    confidence: Decimal = ZERO
    prosecution_weight: Decimal = ZERO
    defence_weight: Decimal = ZERO
    vetoes: tuple = ()

    @property
    def accepted(self) -> bool:
        return self.verdict == ACCEPT

    def __str__(self) -> str:
        return (
            f"{self.verdict.upper()} (confidence {self.confidence}) — {self.reason}\n"
            f"  defence {self.defence_weight} vs prosecution {self.prosecution_weight}"
        )


class Judge:
    def __init__(self, accept_margin: Decimal = D("20"), reject_margin: Decimal = D("20")):
        # Between the two margins the ruling is MODIFY: the court is saying it
        # does not know, which is a more useful answer than a coin-flip.
        self.accept_margin = D(accept_margin)
        self.reject_margin = D(reject_margin)

    def rule(
        self, findings: Sequence[Finding], prosecution: Case, defence: Case
    ) -> Ruling:
        vetoes = tuple(f.juror for f in findings if f.veto and f.is_against)
        if vetoes:
            reasons = "; ".join(f.reason for f in findings if f.veto and f.is_against)
            return Ruling(
                REJECT,
                f"veto: {reasons}",
                confidence=D(100),
                prosecution_weight=prosecution.weight,
                defence_weight=defence.weight,
                vetoes=vetoes,
            )

        total = prosecution.weight + defence.weight
        if total == 0:
            return Ruling(
                MODIFY,
                "no juror found anything to say; there is nothing here to rule on",
                confidence=ZERO,
            )

        margin = defence.weight - prosecution.weight
        share = (max(prosecution.weight, defence.weight) / total) * D(100)
        confidence = percent(max(ZERO, min(D(100), (share - D(50)) * D(2))))

        if margin >= self.accept_margin:
            verdict, reason = ACCEPT, (
                f"the defence carried it {defence.weight} to {prosecution.weight}; "
                "admitted as a candidate genome, not deployed"
            )
        elif margin <= -self.reject_margin:
            verdict, reason = REJECT, (
                f"the prosecution carried it {prosecution.weight} to {defence.weight}"
            )
        else:
            verdict, reason = MODIFY, (
                f"neither side carried it ({defence.weight} to {prosecution.weight}); "
                "the submission is not clearly good or clearly bad as it stands"
            )

        return Ruling(
            verdict,
            reason,
            confidence=confidence,
            prosecution_weight=prosecution.weight,
            defence_weight=defence.weight,
        )


__all__ = ["ACCEPT", "Judge", "MODIFY", "REJECT", "Ruling"]
