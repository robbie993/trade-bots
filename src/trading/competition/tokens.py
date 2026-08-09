"""Tokens — the scoreboard, and emphatically not the money.

One rule governs this whole module, and it is enforced by the schema as well
as by the code: **tokens have no exchange rate to capital.** They live in
their own tables, nothing in ``brokerage/`` reads them, and there is no
function anywhere that converts a balance into an allocation. A currency the
firms can award themselves must never become a second route to the money,
because the first route — the allocator — is where the human gate sits.

What tokens are for: making the competition legible. A leaderboard sorted by
score tells you who is winning today; a token balance tells you who has been
winning, what they did to earn it, and what it cost them when they lost.

Every award and every deduction writes a ``token_events`` row with a reason,
so a balance is always explainable line by line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ...db.connection import Database, utcnow_iso
from ...money import D, ZERO, money

# Earned at, in lifetime tokens.
TITLES = (
    (D(0), "Novice"),
    (D(100), "Apprentice"),
    (D(300), "Trader"),
    (D(750), "Senior Trader"),
    (D(1500), "Portfolio Manager"),
    (D(3000), "Partner"),
    (D(6000), "Legend"),
    (D(12000), "Trading God"),
)


def title_for(lifetime_earned) -> str:
    earned = D(lifetime_earned)
    title = TITLES[0][1]
    for threshold, name in TITLES:
        if earned >= threshold:
            title = name
    return title


class InsufficientTokens(RuntimeError):
    """A firm tried to spend tokens it does not have."""


@dataclass
class Balance:
    firm_key: str
    balance: Decimal = ZERO
    lifetime_earned: Decimal = ZERO
    lifetime_spent: Decimal = ZERO
    title: str = "Novice"
    reputation: Decimal = D(100)
    season: int = 1
    firm_id: Optional[int] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row: dict) -> "Balance":
        return cls(
            id=row.get("id"),
            firm_id=row.get("firm_id"),
            firm_key=row.get("firm_key") or "",
            balance=D(row.get("balance")),
            lifetime_earned=D(row.get("lifetime_earned")),
            lifetime_spent=D(row.get("lifetime_spent")),
            title=row.get("title") or "Novice",
            reputation=D(row.get("reputation")),
            season=int(row.get("season") or 1),
        )

    def __str__(self) -> str:
        return (
            f"{self.firm_key}: {self.balance} tokens [{self.title}] "
            f"reputation {self.reputation}"
        )


class TokenLedger:
    def __init__(self, db: Database, season: int = 1):
        self.db = db
        self.season = season

    # -- reads -------------------------------------------------------------
    def balance(self, firm_key: str) -> Balance:
        row = self.db.query_one("SELECT * FROM token_balances WHERE firm_key = ?", (firm_key,))
        if row is None:
            return self._create(firm_key)
        return Balance.from_row(row)

    def balances(self) -> list:
        rows = self.db.query("SELECT * FROM token_balances ORDER BY balance DESC")
        return [Balance.from_row(r) for r in rows]

    def events(self, firm_key: Optional[str] = None, limit: int = 30) -> list:
        if firm_key:
            return self.db.query(
                "SELECT * FROM token_events WHERE firm_key = ? ORDER BY id DESC LIMIT ?",
                (firm_key, limit),
            )
        return self.db.query("SELECT * FROM token_events ORDER BY id DESC LIMIT ?", (limit,))

    def _create(self, firm_key: str) -> Balance:
        firm = self.db.query_one("SELECT id FROM firms WHERE firm_key = ?", (firm_key,))
        self.db.insert(
            "token_balances",
            {
                "firm_id": firm["id"] if firm else None,
                "firm_key": firm_key,
                "balance": ZERO,
                "lifetime_earned": ZERO,
                "lifetime_spent": ZERO,
                "title": TITLES[0][1],
                "reputation": D(100),
                "season": self.season,
                "updated_at": utcnow_iso(),
            },
        )
        return Balance.from_row(
            self.db.query_one("SELECT * FROM token_balances WHERE firm_key = ?", (firm_key,))
        )

    # -- writes ------------------------------------------------------------
    def award(
        self,
        firm_key: str,
        amount,
        reason: str,
        category: str = "milestone",
        payload: Optional[dict] = None,
    ) -> Balance:
        """Give tokens. Balance and event move together or not at all."""
        amount = money(amount)
        if amount < 0:
            return self.deduct(firm_key, -amount, reason, category, payload)
        current = self.balance(firm_key)
        new_balance = money(current.balance + amount)
        earned = money(current.lifetime_earned + amount)
        with self.db.transaction():
            self.db.update(
                "token_balances",
                current.id,
                {
                    "balance": new_balance,
                    "lifetime_earned": earned,
                    "title": title_for(earned),
                    "updated_at": utcnow_iso(),
                },
            )
            self._event(current, amount, new_balance, reason, category, payload)
        return self.balance(firm_key)

    def deduct(
        self,
        firm_key: str,
        amount,
        reason: str,
        category: str = "penalty",
        payload: Optional[dict] = None,
    ) -> Balance:
        """Take tokens. A balance floors at zero — a firm cannot owe points."""
        amount = money(amount)
        current = self.balance(firm_key)
        taken = min(amount, current.balance)
        new_balance = money(current.balance - taken)
        with self.db.transaction():
            self.db.update(
                "token_balances",
                current.id,
                {"balance": new_balance, "updated_at": utcnow_iso()},
            )
            self._event(current, -taken, new_balance, reason, category, payload)
        return self.balance(firm_key)

    def spend(
        self,
        firm_key: str,
        amount,
        reason: str,
        category: str = "market",
        payload: Optional[dict] = None,
    ) -> Balance:
        """Spend tokens on something. Refuses rather than flooring."""
        amount = money(amount)
        current = self.balance(firm_key)
        if amount > current.balance:
            raise InsufficientTokens(
                f"{firm_key} has {current.balance} tokens, needs {amount}"
            )
        new_balance = money(current.balance - amount)
        with self.db.transaction():
            self.db.update(
                "token_balances",
                current.id,
                {
                    "balance": new_balance,
                    "lifetime_spent": money(current.lifetime_spent + amount),
                    "updated_at": utcnow_iso(),
                },
            )
            self._event(current, -amount, new_balance, reason, category, payload)
        return self.balance(firm_key)

    def adjust_reputation(self, firm_key: str, delta, reason: str) -> Balance:
        """Reputation is separate from tokens: earned slowly, lost fast."""
        current = self.balance(firm_key)
        new_reputation = max(ZERO, min(D(200), money(current.reputation + D(delta))))
        with self.db.transaction():
            self.db.update(
                "token_balances",
                current.id,
                {"reputation": new_reputation, "updated_at": utcnow_iso()},
            )
            self._event(
                current, ZERO, current.balance, reason, "reputation",
                {"reputation": str(new_reputation), "delta": str(D(delta))},
            )
        return self.balance(firm_key)

    def _event(self, balance: Balance, amount, after, reason, category, payload) -> None:
        self.db.insert(
            "token_events",
            {
                "firm_id": balance.firm_id,
                "firm_key": balance.firm_key,
                "amount": money(amount),
                "balance_after": money(after),
                "reason": reason,
                "category": category,
                "season": self.season,
                "payload": json.dumps(payload or {}, default=str),
                "created_at": utcnow_iso(),
            },
        )


__all__ = ["Balance", "InsufficientTokens", "TITLES", "TokenLedger", "title_for"]
