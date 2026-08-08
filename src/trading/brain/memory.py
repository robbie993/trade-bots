"""AgentMem — persistent memory of what actually happened.

Every closed trade and every notable event is written to ``trade_memory`` and
can be recalled by symbol, by firm or by outcome. Recall is keyword and
outcome matching over rows, not embeddings, and that is a deliberate choice:
the memory that justifies a trade has to be quotable in an audit, and a
vector index that returns "something similar" cannot be quoted.

If ``agentmem`` is installed, ``AgentMemory`` will also mirror writes into it
(see ``adapters.py``) — but the SQL table stays the source of truth, so the
system remembers exactly as much with the package as without it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ...db.connection import utcnow_iso
from ...money import D, ZERO, fmt_money, money
from ..config import BrainConfig
from ..models import Fill, TradeProposal
from ..store import TradingStore


@dataclass
class Memory:
    id: Optional[int]
    firm_id: Optional[int]
    symbol: str
    memory_type: str
    summary: str
    outcome: str
    reward: Decimal
    payload: dict

    @classmethod
    def from_row(cls, row: dict) -> "Memory":
        raw = row.get("payload")
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {"raw": raw}
        return cls(
            id=row.get("id"),
            firm_id=row.get("firm_id"),
            symbol=row.get("symbol") or "",
            memory_type=row.get("memory_type") or "",
            summary=row.get("summary") or "",
            outcome=row.get("outcome") or "",
            reward=D(row.get("reward")),
            payload=payload,
        )

    def __str__(self) -> str:
        return f"[{self.outcome}] {self.symbol}: {self.summary} ({fmt_money(self.reward)})"


class AgentMemory:
    def __init__(self, store: TradingStore, config: Optional[BrainConfig] = None):
        self.store = store
        self.config = config or BrainConfig()

    # -- writing ----------------------------------------------------------
    def remember_fill(self, fill: Fill, proposal: Optional[TradeProposal] = None) -> int:
        outcome = "win" if fill.realized_pnl > 0 else "loss" if fill.realized_pnl < 0 else "flat"
        summary = (
            f"{fill.side} {fill.quantity} {fill.symbol} at {fill.price}"
            + (f" — {proposal.rationale}" if proposal and proposal.rationale else "")
        )
        return self.store.db.insert(
            "trade_memory",
            {
                "firm_id": fill.firm_id,
                "symbol": fill.symbol,
                "memory_type": "trade",
                "summary": summary,
                "payload": json.dumps(
                    {
                        "side": fill.side,
                        "quantity": str(fill.quantity),
                        "price": str(fill.price),
                        "fee": str(fill.fee),
                        "confidence": str(proposal.confidence) if proposal else None,
                        "debate_winner": proposal.debate_winner if proposal else None,
                        "rationale": proposal.rationale if proposal else None,
                    },
                    default=str,
                ),
                "outcome": outcome,
                "reward": money(fill.realized_pnl),
                "tags": fill.venue,
                "as_of": fill.as_of,
                "created_at": utcnow_iso(),
            },
        )

    def remember(
        self,
        summary: str,
        *,
        symbol: str = "",
        firm_id: Optional[int] = None,
        memory_type: str = "lesson",
        outcome: str = "",
        reward: Decimal = ZERO,
        payload: Optional[dict] = None,
    ) -> int:
        return self.store.db.insert(
            "trade_memory",
            {
                "firm_id": firm_id,
                "symbol": symbol,
                "memory_type": memory_type,
                "summary": summary,
                "payload": json.dumps(payload or {}, default=str),
                "outcome": outcome,
                "reward": money(reward),
                "created_at": utcnow_iso(),
            },
        )

    # -- reading ----------------------------------------------------------
    def recall(
        self,
        symbol: Optional[str] = None,
        firm_id: Optional[int] = None,
        outcome: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list:
        clauses, params = [], []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if firm_id is not None:
            clauses.append("firm_id = ?")
            params.append(firm_id)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit or self.config.memory_recall_limit)
        rows = self.store.db.query(
            f"SELECT * FROM trade_memory{where} ORDER BY id DESC LIMIT ?", tuple(params)
        )
        return [Memory.from_row(r) for r in rows]

    def search(self, text: str, limit: Optional[int] = None) -> list:
        """Keyword recall over summaries — quotable, therefore auditable."""
        needle = f"%{text.lower()}%"
        rows = self.store.db.query(
            "SELECT * FROM trade_memory WHERE LOWER(summary) LIKE ? ORDER BY id DESC LIMIT ?",
            (needle, limit or self.config.memory_recall_limit),
        )
        return [Memory.from_row(r) for r in rows]

    def track_record(self, symbol: str, firm_id: Optional[int] = None) -> dict:
        """What this firm's history in this name actually says.

        Returned to the analysts as context, never as a signal in itself: the
        memory of losing money in a name is a reason to size down, not a
        prediction about the next trade.
        """
        memories = [m for m in self.recall(symbol=symbol, firm_id=firm_id, limit=200)
                    if m.memory_type == "trade"]
        wins = [m for m in memories if m.outcome == "win"]
        losses = [m for m in memories if m.outcome == "loss"]
        total = sum((m.reward for m in memories), ZERO)
        return {
            "symbol": symbol.upper(),
            "trades": len(memories),
            "wins": len(wins),
            "losses": len(losses),
            "net": money(total),
            "worst": money(min((m.reward for m in memories), default=ZERO)),
            "best": money(max((m.reward for m in memories), default=ZERO)),
        }

    def lessons(self, limit: int = 10) -> list:
        rows = self.store.db.query(
            "SELECT * FROM trade_memory WHERE memory_type = ? ORDER BY id DESC LIMIT ?",
            ("lesson", limit),
        )
        return [Memory.from_row(r) for r in rows]


__all__ = ["AgentMemory", "Memory"]
