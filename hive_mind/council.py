"""The village council — five proposals in, one decision out.

Voting is weighted by each scout's confidence, and the winner has to clear the
genome's ``conviction_floor``. That floor is the difference between a council
and a coin: a 3–2 split among five uncertain scouts is not a signal, and a
system that trades on it will trade every single day, which is the same as
having no strategy and paying the spread for the privilege.

What the council may not do:

* **It may not invent an action.** It picks among what the scouts proposed.
* **It may not exceed the genome.** Leverage is capped after the vote as well
  as before it, because the winning bucket is an average of proposals and an
  average can drift over a cap that each input respected.
* **It may not change the genome.** The council decides today's trade. Only
  the evolver changes what tomorrow's trade would be, and only where the
  walk-forward lock allows.

The abstention rule is deliberate: when nobody proposes anything, the answer is
``hold`` at zero confidence, not a random action. A quiet day is information.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from src.money import D, ZERO

from .evolver import Genome
from .market import Bar
from .memory import ObsidianMemory
from .scouts import ScoutAI


@dataclass
class Decision:
    action: str = "hold"
    leverage: Decimal = ZERO
    confidence: Decimal = ZERO
    votes: dict = field(default_factory=dict)
    proposals: list = field(default_factory=list)
    reason: str = ""
    blocked_by_conviction: bool = False

    @property
    def is_trade(self) -> bool:
        return self.action != "hold" and self.leverage > 0

    @property
    def backers(self) -> list:
        """The scouts who voted for what won — the ones an outcome settles."""
        return [p for p in self.proposals if p.key == (self.action, self.leverage)]

    def __str__(self) -> str:
        return (
            f"{{'action': '{self.action}', 'leverage': {self.leverage}, "
            f"'confidence': {self.confidence}}}"
        )


class VillageCouncil:
    def __init__(
        self,
        memory: ObsidianMemory,
        scouts: int = 5,
        seed: int = 0,
    ):
        self.memory = memory
        self.scouts = [ScoutAI(id=i + 1, memory=memory, seed=seed) for i in range(scouts)]

    def debate(
        self,
        bar: Bar,
        history: Sequence[Bar],
        genome: Genome,
        asset: str = "SPY",
    ) -> Decision:
        proposals: list = []
        for scout in self.scouts:
            proposal = scout.propose(bar, history, genome, asset)
            if proposal is not None:
                proposals.append(proposal)

        if not proposals:
            return Decision(reason="no scout had a proposal that survived its own fact-check")

        votes: dict = defaultdict(Decimal)
        for proposal in proposals:
            votes[proposal.key] += D(str(proposal.confidence))

        winner = max(votes, key=lambda k: (votes[k], k[1]))
        total = sum(votes.values(), ZERO)
        share = (votes[winner] / total).quantize(D("0.0001")) if total else ZERO

        action, leverage = winner
        leverage = min(leverage, genome.leverage_cap)

        if share < genome.conviction_floor:
            return Decision(
                action="hold",
                confidence=share,
                votes=dict(votes),
                proposals=proposals,
                reason=(
                    f"{share:.0%} of the weighted vote for {action} — under the "
                    f"{genome.conviction_floor:.0%} conviction floor"
                ),
                blocked_by_conviction=True,
            )

        backing = [p.reason for p in proposals if p.key == winner]
        return Decision(
            action=action,
            leverage=leverage,
            confidence=share,
            votes=dict(votes),
            proposals=proposals,
            reason=backing[0] if backing else "",
        )

    def settle(self, decision: Decision, was_right: bool) -> None:
        """Tell the scouts who backed this call how it went."""
        for proposal in decision.backers:
            self.scouts[proposal.scout_id - 1].settle(was_right)

    def standings(self) -> str:
        return "\n".join(f"  {scout}" for scout in self.scouts)


__all__ = ["Decision", "VillageCouncil"]
