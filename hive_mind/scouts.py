"""The scouts — fresh minds, parachuted into today's tape.

A scout reads one bar plus the recent past, consults the Obsidian brain about
how this regime has gone before, and proposes one action. Five of them run
each day and the council weighs their votes.

The two rules that make a scout more than a random number:

**It fact-checks itself before it speaks.** A proposal that would exceed the
genome's leverage cap, or that reads an indicator off too little history, is
dropped by the scout rather than argued down by the council. A bad idea that
never enters the debate cannot win it on a quiet day when three scouts abstain.

**Its confidence is earned and lost.** The council weighs votes by confidence,
so a scout whose confidence never moved would make that weighting decorative —
five equal voices wearing different numbers. Confidence here is a running
record of whether this scout's own calls made money, seeded at 0.5 and moved
by outcomes, floored and capped so that no single scout can ever carry a vote
alone and none is ever silenced for good.

Scouts do not learn *strategy*. They cannot mutate the genome, and they share
one. Five scouts are five samples of the same beliefs against a noisy tape,
which is the honest thing five agents on one genome can be. Anything more
would be five strategies pretending to be a consensus.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional, Sequence

from .evolver import Genome
from .market import Bar
from .memory import ObsidianMemory, Recall

# What a scout may ask for. `hedge` is a short exposure, not a put: this
# engine models linear exposure only. Real puts have a strike, an expiry and a
# vol surface, none of which exist here, and pretending otherwise would be the
# most expensive kind of wrong.
ACTIONS = ("buy", "sell", "hedge", "hold")


@dataclass
class Proposal:
    scout_id: int
    action: str
    leverage: float
    reason: str
    confidence: float

    @property
    def key(self) -> tuple:
        """What a vote is counted under.

        A tuple, not ``f"{action}_{leverage}"`` — string keys split back apart
        on the underscore, and the first action with an underscore in its name
        silently becomes a different action.
        """
        return (self.action, round(self.leverage, 2))

    def __str__(self) -> str:
        return (
            f"scout {self.scout_id}: {self.action} @ {self.leverage:.2f}x "
            f"(conf {self.confidence:.2f}) — {self.reason}"
        )


@dataclass
class ScoutAI:
    id: int
    memory: ObsidianMemory
    confidence: float = 0.5
    calls: int = 0
    right: int = 0
    seed: int = 0

    MIN_CONFIDENCE = 0.15
    MAX_CONFIDENCE = 0.95

    def __post_init__(self) -> None:
        self._rng = random.Random(f"scout:{self.seed}:{self.id}")

    # -- proposing --------------------------------------------------------
    def propose(
        self,
        bar: Bar,
        history: Sequence[Bar],
        genome: Genome,
        asset: str = "SPY",
    ) -> Optional[Proposal]:
        window = int(genome.momentum_window)
        if len(history) < window + 1:
            return None  # no reading off half an indicator

        closes = [b.close for b in history[-(window + 1) :]]
        momentum = (closes[-1] - closes[0]) / closes[0]
        recall = self.memory.recall(asset, bar.vix)

        action, leverage, reason = self._read(bar, momentum, genome, recall)
        if action == "hold":
            return None

        proposal = Proposal(
            scout_id=self.id,
            action=action,
            leverage=leverage,
            reason=reason,
            confidence=self.confidence,
        )
        return proposal if self.fact_check(proposal, bar, genome) else None

    def _read(self, bar: Bar, momentum: float, genome: Genome, recall: Recall) -> tuple:
        """One scout's opinion. ``trend_bias`` decides fade or follow."""
        follows = self._rng.random() < genome.trend_bias
        base = genome.leverage_cap * genome.risk_tolerance

        # Fear: a VIX spike with capitulating sentiment.
        if bar.vix >= genome.vix_spike and bar.sentiment <= genome.fear_threshold:
            if follows:
                return "hedge", base, f"VIX {bar.vix:.1f} spiking, sentiment breaking down"
            return "buy", base, f"VIX {bar.vix:.1f} with capitulation — fading the fear"

        # Greed: a calm tape that everyone already loves.
        if bar.vix <= genome.vix_calm and bar.sentiment >= genome.greed_threshold:
            if follows:
                return "buy", base * 0.8, f"calm tape, sentiment {bar.sentiment:+.2f}"
            return "sell", base * 0.6, f"everyone is long at VIX {bar.vix:.1f}"

        # Otherwise: trade the momentum reading, in whichever direction the
        # genome believes in.
        if abs(momentum) < 0.004:
            return "hold", 0.0, "nothing to say"

        rising = momentum > 0
        wants_long = rising if follows else not rising
        strength = min(1.0, abs(momentum) / 0.05)
        leverage = base * (0.4 + 0.6 * strength)

        # Memory does not decide; it sizes. A regime this scout has lost money
        # in before gets a smaller bet, never a reversed one.
        if recall.known and recall.avg_return < 0:
            leverage *= 0.6
            note = f" (memory: {recall.trades} trades here averaged {recall.avg_return:+.2f}%)"
        elif recall.known:
            note = f" (memory: {recall.win_rate:.0f}% wins here)"
        else:
            note = ""

        direction = "following" if follows else "fading"
        reason = f"{direction} {momentum * 100:+.2f}% over {int(genome.momentum_window)}d{note}"
        if wants_long:
            return "buy", leverage, reason
        return ("sell", leverage, reason) if not rising else ("hedge", leverage, reason)

    # -- checking itself --------------------------------------------------
    def fact_check(self, proposal: Proposal, bar: Bar, genome: Genome) -> bool:
        """Sanity, before the council ever hears it."""
        if proposal.action not in ACTIONS:
            return False
        if proposal.leverage <= 0:
            return False
        if proposal.leverage > genome.leverage_cap + 1e-9:
            return False  # the cap is a cap, not a suggestion
        if bar.close <= 0 or bar.volume <= 0:
            return False  # a bar with no trading in it is not a market
        return True

    # -- learning whether to be listened to --------------------------------
    def settle(self, was_right: bool) -> None:
        """Move this scout's confidence on the outcome of a call it made."""
        self.calls += 1
        self.right += 1 if was_right else 0
        step = 0.06 if was_right else -0.08  # losing costs more than winning pays
        self.confidence = max(
            self.MIN_CONFIDENCE, min(self.MAX_CONFIDENCE, self.confidence + step)
        )

    @property
    def hit_rate(self) -> Optional[float]:
        return 100.0 * self.right / self.calls if self.calls else None

    def __str__(self) -> str:
        rate = f"{self.hit_rate:.0f}%" if self.hit_rate is not None else "—"
        return f"scout {self.id}: confidence {self.confidence:.2f}, {self.calls} calls, {rate} right"


__all__ = ["ACTIONS", "Proposal", "ScoutAI"]
