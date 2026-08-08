"""The Leaderboard — who is winning, and on what evidence.

Ranked by score, but the table deliberately shows the inputs next to the
rank. A leaderboard that shows only a number invites the reader to trust it;
this one shows the drawdown and the trade count that produced it, so a firm
sitting at the top on nine trades is visibly a firm sitting at the top on
nine trades.

Firms below the sample gate are ranked last regardless of score and marked
"measuring". They have not earned a position either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ...money import D, fmt_money, fmt_pct
from ..models import FirmRecord
from .evaluator import Scorecard


@dataclass
class LeaderboardRow:
    rank: int
    firm_key: str
    name: str
    status: str
    score: Decimal
    equity: Decimal
    allocation: Decimal
    return_pct: Decimal
    drawdown_pct: Decimal
    trades: int
    sufficient_data: bool

    @property
    def state(self) -> str:
        if self.status == "killed":
            return "killed"
        if not self.sufficient_data:
            return "measuring"
        return self.status

    def to_display(self) -> dict:
        return {
            "#": self.rank,
            "firm": self.firm_key,
            "name": self.name[:24],
            "state": self.state,
            "score": f"{self.score:.2f}" if self.sufficient_data else f"({self.score:.2f})",
            "equity": fmt_money(self.equity),
            "allocation": fmt_money(self.allocation),
            "return": fmt_pct(self.return_pct),
            "drawdown": fmt_pct(self.drawdown_pct),
            "trades": self.trades,
        }


class Leaderboard:
    def __init__(self, cards: Sequence[Scorecard], firms: Sequence[FirmRecord]):
        self.by_id = {f.id: f for f in firms}
        self.rows = self._rank(cards)

    def _rank(self, cards: Sequence[Scorecard]) -> list:
        def sort_key(card: Scorecard):
            firm = self.by_id.get(card.firm_id)
            killed = bool(firm and firm.is_killed)
            # Killed last, then unmeasured, then by score descending. Firm key
            # breaks ties so the table is stable between runs.
            return (killed, not card.sufficient_data, -D(card.score), card.firm_key)

        ordered = sorted(cards, key=sort_key)
        rows = []
        for index, card in enumerate(ordered, start=1):
            firm = self.by_id.get(card.firm_id)
            rows.append(
                LeaderboardRow(
                    rank=index,
                    firm_key=card.firm_key,
                    name=firm.name if firm else card.firm_key,
                    status=firm.status if firm else "unknown",
                    score=card.score,
                    equity=card.equity,
                    allocation=D(firm.allocation) if firm else D(0),
                    return_pct=card.return_pct,
                    drawdown_pct=card.drawdown_pct,
                    trades=card.closed_trades,
                    sufficient_data=card.sufficient_data,
                )
            )
        return rows

    def top(self, n: int = 3) -> list:
        return [r for r in self.rows if r.sufficient_data][:n]

    def to_display(self) -> list:
        return [r.to_display() for r in self.rows]

    def render(self) -> str:
        rows = self.to_display()
        if not rows:
            return "(no firms)"
        columns = list(rows[0].keys())
        widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in columns}
        header = "  ".join(c.ljust(widths[c]) for c in columns)
        sep = "  ".join("-" * widths[c] for c in columns)
        body = ["  ".join(str(r[c]).ljust(widths[c]) for c in columns) for r in rows]
        note = "\n(parenthesised scores are below the sample gate — not yet a verdict)"
        return "\n".join([header, sep, *body]) + note


__all__ = ["Leaderboard", "LeaderboardRow"]
