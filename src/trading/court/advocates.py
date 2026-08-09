"""Prosecution and defence — the same shape as the firms' bull and bear.

Each advocate may cite only the findings on its side, and may not invent one.
Every sentence in a stored case is therefore traceable to a juror, which is
what makes the transcript worth keeping: a defence that could editorialise
would be a defence you have to fact-check before trusting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ...money import ZERO, percent
from .jury import Finding


@dataclass(frozen=True)
class Case:
    side: str
    weight: Decimal
    text: str
    cited: int


class Advocate:
    def __init__(self, side: str):
        self.side = side

    def build(self, findings: Sequence[Finding]) -> Case:
        mine = [
            f
            for f in findings
            if (f.is_against if self.side == "prosecution" else f.is_for)
        ]
        weight = sum((f.weight for f in mine), ZERO)
        if not mine:
            return Case(self.side, ZERO, f"The {self.side} offers nothing.", 0)

        vetoes = [f for f in mine if f.veto]
        header = f"{self.side.title()}, weight {percent(weight)} from {len(mine)} juror(s):"
        # Marker matches the side, so a defence point does not read as a minus.
        bullet = "-" if self.side == "prosecution" else "+"
        lines = [
            f"  {bullet} {f.juror}: {f.reason}" + ("  [VETO]" if f.veto else "") for f in mine
        ]
        if vetoes and self.side == "prosecution":
            lines.append(
                "  The prosecution notes that a veto finding ends the matter regardless "
                "of the weight on either side."
            )
        return Case(self.side, percent(weight), "\n".join([header, *lines]), len(mine))


class Prosecution(Advocate):
    def __init__(self):
        super().__init__("prosecution")


class Defence(Advocate):
    def __init__(self):
        super().__init__("defence")


__all__ = ["Advocate", "Case", "Defence", "Prosecution"]
