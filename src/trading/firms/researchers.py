"""Bull and Bear — the firm's internal argument.

The village already has this shape: the File Court runs a prosecutor against a
defence and takes the verdict from the disagreement, not from either side's
enthusiasm. The trading firm does the same thing with money.

Each researcher gets the *same* signals and is only allowed to cite the ones
that support its side. The winner is decided by weight of evidence, and the
margin between the two cases becomes the trader's confidence. A one-sided
debate produces a big position; a close one produces a small position or
none at all, which is the behaviour you want from a disagreement.

Neither researcher may invent evidence. Both cases are built from the signal
list, so every sentence in a stored rationale is traceable to an analyst.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ...money import D, ZERO, percent
from ..models import Side, Signal


@dataclass(frozen=True)
class DebateResult:
    symbol: str
    winner: str            # "bull" | "bear" | "none"
    bull_weight: Decimal
    bear_weight: Decimal
    confidence: Decimal    # 0-100, the margin of victory
    bull_case: str
    bear_case: str

    @property
    def side(self):
        if self.winner == "bull":
            return Side.BUY
        if self.winner == "bear":
            return Side.SELL
        return None

    @property
    def is_decided(self) -> bool:
        return self.winner in ("bull", "bear")


class Researcher:
    """One side of the argument. ``sign`` picks which evidence it may cite."""

    def __init__(self, stance: str):
        self.stance = stance
        self.sign = 1 if stance == "bull" else -1

    def build_case(self, signals: Sequence[Signal]) -> tuple[Decimal, str]:
        cited = [
            s
            for s in signals
            if not s.is_silent and (s.weighted * D(self.sign)) > 0
        ]
        weight = sum((abs(s.weighted) for s in cited), ZERO)
        if not cited:
            return ZERO, f"No {self.stance} evidence in the signals."
        lines = [f"{s.analyst}: {s.note} (score {s.score}, confidence {s.confidence})" for s in cited]
        header = (
            f"{self.stance.title()} case, weight {percent(weight)} across {len(cited)} analyst(s):"
        )
        return weight, "\n".join([header, *(f"  - {line}" for line in lines)])


class DebateRoom:
    """Runs one bull-versus-bear argument over one symbol."""

    def __init__(self, min_margin: Decimal = D("5")):
        # Below this margin the room reports "none" rather than a coin-flip.
        self.min_margin = D(min_margin)
        self.bull = Researcher("bull")
        self.bear = Researcher("bear")

    def debate(self, symbol: str, signals: Sequence[Signal]) -> DebateResult:
        bull_weight, bull_case = self.bull.build_case(signals)
        bear_weight, bear_case = self.bear.build_case(signals)
        margin = bull_weight - bear_weight
        total = bull_weight + bear_weight

        if total == 0 or abs(margin) < self.min_margin:
            return DebateResult(
                symbol=symbol,
                winner="none",
                bull_weight=percent(bull_weight),
                bear_weight=percent(bear_weight),
                confidence=ZERO,
                bull_case=bull_case,
                bear_case=bear_case,
            )

        # Confidence is the share of total evidence the winner holds, mapped
        # from 50-100% of the evidence onto 0-100 confidence. Winning 51/49
        # is not a conviction trade and must not size like one.
        share = (max(bull_weight, bear_weight) / total) * D(100)
        confidence = percent(max(ZERO, min(D(100), (share - D(50)) * D(2))))

        # **A lone voice is not unanimous.** The share above measures agreement
        # among whoever spoke, and one analyst agrees with itself completely —
        # so an unopposed seat scored 100 and took the firm's whole per-trade
        # risk budget, because `trader.propose` sizes on exactly this number.
        # Silence and unanimity came out identical: one weak seat and three
        # strong seats in accord both sized at 100%, while three seats agreeing
        # over one dissenter sized at 50%. More evidence produced a smaller
        # position than no evidence at all.
        #
        # The rule is the one the comment above already states, applied to the
        # other axis: when the winning case rests on a single analyst, the room
        # cannot be more certain than that analyst is. A seat that is 20% sure
        # yields a 20% confidence, whichever way it is pointing and however
        # loud it is. Nothing is blocked — a strong lone signal still acts, at
        # the size of one strong signal rather than the size of a chorus.
        #
        # This is not about any one seat. It became visible when the news desk
        # arrived, because a headline exists whether or not price is doing
        # anything, which makes `signals` the seat most likely to be talking
        # while the rest of the room is quiet — and `firm_f_bonds`, whose two
        # analysts are both effectively mute on its own universe, would have
        # become a news-only trader on its first strong headline.
        winner_side = self.bull if margin > 0 else self.bear
        speakers = [s for s in signals
                    if not s.is_silent
                    and (s.weighted * D(winner_side.sign)) > 0]
        if len(speakers) == 1:
            confidence = percent(min(confidence, D(speakers[0].confidence)))
        return DebateResult(
            symbol=symbol,
            winner="bull" if margin > 0 else "bear",
            bull_weight=percent(bull_weight),
            bear_weight=percent(bear_weight),
            confidence=confidence,
            bull_case=bull_case,
            bear_case=bear_case,
        )


__all__ = ["DebateResult", "DebateRoom", "Researcher"]
