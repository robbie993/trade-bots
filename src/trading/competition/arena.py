"""The arena — bouts, milestones and the token standings.

Everything here reads the brokerage's scorecards and writes only tokens. It
cannot move capital, pause a firm or touch a position; the competition is a
layer of commentary on the ledger, not a participant in it. That is what lets
it be as gamified as you like without any of it reaching the money.

Bouts are decided on a stated metric over a stated moment, and the numbers
that decided one are stored on the row. "Firm A beat Firm B" is not a fact
about the firms in general — it is a fact about a metric at a timestamp, and
the record says which.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from ...db.connection import Database, utcnow_iso
from ...money import D, ZERO, money
from .tokens import TokenLedger

# What a bout can be fought over. Each maps to a Scorecard attribute, plus
# whether higher is better.
METRICS = {
    "return": ("return_pct", True),
    "score": ("score", True),
    "drawdown": ("drawdown_pct", False),
    "win_rate": ("win_rate_pct", True),
    "sharpe": ("sharpe", True),
}

AWARD_BOUT_WIN = D(20)
PENALTY_BOUT_LOSS = D(10)


@dataclass
class Bout:
    challenger: str
    opponent: str
    metric: str
    challenger_score: Decimal = ZERO
    opponent_score: Decimal = ZERO
    winner: Optional[str] = None
    stake: Decimal = ZERO
    decided: bool = False
    reason: str = ""
    id: Optional[int] = None

    def __str__(self) -> str:
        if not self.decided:
            return f"{self.challenger} vs {self.opponent} on {self.metric}: no contest ({self.reason})"
        loser = self.opponent if self.winner == self.challenger else self.challenger
        return (
            f"{self.winner} beat {loser} on {self.metric} "
            f"({self.challenger_score} vs {self.opponent_score})"
        )


class Arena:
    def __init__(self, db: Database, season: int = 1, tokens: Optional[TokenLedger] = None):
        self.db = db
        self.season = season
        self.tokens = tokens or TokenLedger(db, season)

    # -- bouts -------------------------------------------------------------
    def bout(self, challenger: str, opponent: str, cards: Sequence, metric: str = "score") -> Bout:
        """Settle one head-to-head on a named metric."""
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}; expected {', '.join(sorted(METRICS))}")
        attribute, higher_wins = METRICS[metric]
        by_key = {c.firm_key: c for c in cards}
        a, b = by_key.get(challenger), by_key.get(opponent)

        fight = Bout(challenger=challenger, opponent=opponent, metric=metric)
        if a is None or b is None:
            fight.reason = "one of the firms has no scorecard"
            return self._record(fight)

        # Below the sample gate a bout is not a contest. Awarding tokens on
        # four trades would make the standings a record of who got lucky
        # first, which is the same mistake the allocator refuses to make.
        if not (a.sufficient_data and b.sufficient_data):
            fight.reason = "one of the firms is below the sample gate"
            return self._record(fight)

        left, right = getattr(a, attribute, None), getattr(b, attribute, None)
        if left is None or right is None:
            fight.reason = f"{metric} is not available for both firms"
            return self._record(fight)

        fight.challenger_score, fight.opponent_score = D(left), D(right)
        if fight.challenger_score == fight.opponent_score:
            fight.reason = "drawn"
            return self._record(fight)

        challenger_wins = (
            fight.challenger_score > fight.opponent_score
            if higher_wins
            else fight.challenger_score < fight.opponent_score
        )
        fight.winner = challenger if challenger_wins else opponent
        fight.decided = True
        fight.stake = AWARD_BOUT_WIN
        loser = opponent if challenger_wins else challenger

        self._record(fight)
        self.tokens.award(
            fight.winner, AWARD_BOUT_WIN, f"beat {loser} on {metric}", category="bout"
        )
        self.tokens.deduct(
            loser, PENALTY_BOUT_LOSS, f"lost to {fight.winner} on {metric}", category="bout"
        )
        return fight

    def round_robin(self, cards: Sequence, metric: str = "score") -> list:
        """Every firm against every other, once."""
        keys = [c.firm_key for c in cards]
        out = []
        for i, left in enumerate(keys):
            for right in keys[i + 1 :]:
                out.append(self.bout(left, right, cards, metric))
        return out

    def _record(self, fight: Bout) -> Bout:
        fight.id = self.db.insert(
            "bouts",
            {
                "season": self.season,
                "challenger": fight.challenger,
                "opponent": fight.opponent,
                "challenger_score": fight.challenger_score,
                "opponent_score": fight.opponent_score,
                "metric": fight.metric,
                "winner": fight.winner,
                "stake": money(fight.stake),
                "decided": fight.decided,
                "reason": fight.reason,
                "created_at": utcnow_iso(),
            },
        )
        return fight

    def bouts(self, limit: int = 20) -> list:
        return self.db.query(
            "SELECT * FROM bouts WHERE season = ? ORDER BY id DESC LIMIT ?",
            (self.season, limit),
        )

    # -- milestones --------------------------------------------------------
    def award_milestones(self, cards: Sequence) -> list:
        """Tokens for things worth noticing. Idempotent per milestone per firm."""
        awarded = []
        for card in cards:
            if not card.sufficient_data:
                continue
            for key, amount, reason, earned in (
                ("first_20", D(50), "reached 20 closed trades", card.closed_trades >= 20),
                ("profitable", D(100), "profitable over its sample", card.return_pct > 0),
                ("return_5", D(150), "returned more than 5%", card.return_pct > D(5)),
                ("return_10", D(300), "returned more than 10%", card.return_pct > D(10)),
                ("steady", D(120), "kept drawdown under 5%", card.drawdown_pct < D(5)),
                (
                    "sharp",
                    D(200),
                    "Sharpe above 1.5",
                    card.sharpe is not None and card.sharpe > D("1.5"),
                ),
            ):
                if not earned or self._already_awarded(card.firm_key, key):
                    continue
                self.tokens.award(
                    card.firm_key, amount, reason, category="milestone", payload={"milestone": key}
                )
                awarded.append((card.firm_key, key, amount))
        return awarded

    def _already_awarded(self, firm_key: str, milestone: str) -> bool:
        rows = self.db.query(
            "SELECT payload FROM token_events WHERE firm_key = ? AND category = ?",
            (firm_key, "milestone"),
        )
        return any(f'"milestone": "{milestone}"' in (r.get("payload") or "") for r in rows)

    # -- standings ---------------------------------------------------------
    def standings(self) -> list:
        """The token table. Deliberately separate from the capital leaderboard."""
        return [
            {
                "rank": index,
                "firm": b.firm_key,
                "tokens": b.balance,
                "title": b.title,
                "reputation": b.reputation,
                "earned": b.lifetime_earned,
                "spent": b.lifetime_spent,
            }
            for index, b in enumerate(self.tokens.balances(), start=1)
        ]

    def render(self) -> str:
        rows = self.standings()
        if not rows:
            return "(no firms have earned a token yet)"
        columns = list(rows[0].keys())
        widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in columns}
        header = "  ".join(c.ljust(widths[c]) for c in columns)
        sep = "  ".join("-" * widths[c] for c in columns)
        body = ["  ".join(str(r[c]).ljust(widths[c]) for c in columns) for r in rows]
        return "\n".join([header, sep, *body]) + (
            "\n(tokens are points, not capital — nothing converts one into the other)"
        )


__all__ = ["AWARD_BOUT_WIN", "Arena", "Bout", "METRICS", "PENALTY_BOUT_LOSS"]
