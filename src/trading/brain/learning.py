"""Learning — what the ecosystem concluded, in sentences a human can check.

The brain's third job, after remembering and evolving: look across every
firm's record and state what it implies. Each lesson names the evidence that
produced it and is written to ``trade_memory`` as a ``lesson`` row, so the
conclusions accumulate in the same table as the trades they came from.

Lessons never change behaviour on their own. They are surfaced to the
operator and stored; the only things that alter what a firm does are a
promoted genome, an allocation change, or a human. A system that silently
rewrote its own strategy from its own conclusions would be exactly the thing
the kill switch cannot see coming.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from ...money import D, ZERO, fmt_money, money, percent
from ..config import TradingConfig
from ..store import TradingStore
from .memory import AgentMemory


@dataclass
class Lesson:
    topic: str
    statement: str
    evidence: str
    kind: str = ""

    @property
    def key(self) -> str:
        """What makes two lessons *the same* lesson.

        Deliberately not the text. The evidence in a lesson moves every tick —
        one more trade, a different win rate — so text-matching would write a
        near-identical row on every pass and bury the actual conclusions. A
        lesson is identified by what it is about and what it claims.
        """
        return f"{self.topic}:{self.kind or self.statement[:40]}"

    def __str__(self) -> str:
        return f"{self.topic}: {self.statement}  [{self.evidence}]"


class Learner:
    def __init__(
        self,
        store: TradingStore,
        memory: Optional[AgentMemory] = None,
        config: Optional[TradingConfig] = None,
    ):
        self.store = store
        self.config = config or TradingConfig()
        self.memory = memory or AgentMemory(store, self.config.brain)

    def lessons(self, cards: Sequence = ()) -> list:
        out: list = []
        out.extend(self._symbol_lessons())
        out.extend(self._firm_lessons(cards))
        out.extend(self._cost_lessons())
        return out

    def _symbol_lessons(self) -> list:
        rows = self.store.db.query(
            "SELECT symbol, outcome, reward FROM trade_memory WHERE memory_type = ?", ("trade",)
        )
        by_symbol: dict = {}
        for row in rows:
            entry = by_symbol.setdefault(row["symbol"], {"wins": 0, "losses": 0, "net": ZERO})
            if row["outcome"] == "win":
                entry["wins"] += 1
            elif row["outcome"] == "loss":
                entry["losses"] += 1
            entry["net"] = money(entry["net"] + D(row["reward"]))

        out = []
        for symbol, entry in sorted(by_symbol.items()):
            total = entry["wins"] + entry["losses"]
            if total < 5:
                continue  # below five closed trades a name has told us nothing
            rate = percent(D(entry["wins"]) / D(total) * D(100))
            if entry["net"] < 0 and rate < D(40):
                out.append(
                    Lesson(
                        symbol,
                        f"has lost money across {total} closed trades; treat signals here "
                        "as weaker than they read",
                        f"win rate {rate}%, net {fmt_money(entry['net'])}",
                        kind="loser",
                    )
                )
            elif entry["net"] > 0 and rate > D(60):
                out.append(
                    Lesson(
                        symbol,
                        f"has been consistently profitable across {total} closed trades",
                        f"win rate {rate}%, net {fmt_money(entry['net'])}",
                        kind="winner",
                    )
                )
        return out

    def _firm_lessons(self, cards: Sequence) -> list:
        out = []
        for card in cards:
            if not getattr(card, "sufficient_data", False):
                continue
            if card.drawdown_pct > D(10) and card.return_pct > ZERO:
                out.append(
                    Lesson(
                        card.firm_key,
                        "is profitable but volatile; its returns are being bought with "
                        "drawdown",
                        f"return {card.return_pct}%, drawdown {card.drawdown_pct}%",
                        kind="volatile",
                    )
                )
            if card.win_rate_pct is not None and card.win_rate_pct > D(60) and card.return_pct < ZERO:
                out.append(
                    Lesson(
                        card.firm_key,
                        "wins often and loses money: its losers are bigger than its winners",
                        f"win rate {card.win_rate_pct}%, return {card.return_pct}%",
                        kind="skew",
                    )
                )
        return out

    def _cost_lessons(self) -> list:
        rows = self.store.db.query("SELECT fee, realized_pnl FROM fills")
        if not rows:
            return []
        fees = money(sum((D(r["fee"]) for r in rows), ZERO))
        gross = money(sum((D(r["realized_pnl"]) for r in rows), ZERO))
        if fees <= 0:
            return []
        if gross > 0 and fees > gross / D(2):
            return [
                Lesson(
                    "costs",
                    "fees are consuming more than a third of gross profit; the edge is "
                    "thinner than the P&L suggests",
                    f"fees {fmt_money(fees)} against gross {fmt_money(gross)}",
                    kind="fee_drag",
                )
            ]
        if gross <= 0 and fees > 0:
            return [
                Lesson(
                    "costs",
                    "the ecosystem has paid fees without producing gross profit",
                    f"fees {fmt_money(fees)}, gross {fmt_money(gross)}",
                    kind="fees_no_profit",
                )
            ]
        return []

    def new_lessons(self, lessons: Sequence[Lesson]) -> list:
        """The ones that have not been drawn before.

        Exposed separately from ``record`` so a caller can tell which lessons
        were *newly* concluded — the vault writes a note per new conclusion,
        and re-writing every known lesson on every tick would bury the moment
        a thing was actually learned.
        """
        existing = {
            m.payload.get("key") for m in self.memory.lessons(limit=500) if m.payload.get("key")
        }
        out, seen = [], set()
        for lesson in lessons:
            if lesson.key in existing or lesson.key in seen:
                continue
            seen.add(lesson.key)
            out.append(lesson)
        return out

    def record(self, lessons: Sequence[Lesson]) -> int:
        """Persist lessons the ecosystem has not already drawn."""
        existing = {
            m.payload.get("key") for m in self.memory.lessons(limit=500) if m.payload.get("key")
        }
        written = 0
        for lesson in lessons:
            if lesson.key in existing:
                continue
            self.memory.remember(
                str(lesson),
                symbol=lesson.topic if lesson.topic.isupper() else "",
                memory_type="lesson",
                payload={
                    "key": lesson.key,
                    "topic": lesson.topic,
                    "kind": lesson.kind,
                    "evidence": lesson.evidence,
                },
            )
            existing.add(lesson.key)
            written += 1
        return written


__all__ = ["Learner", "Lesson"]
