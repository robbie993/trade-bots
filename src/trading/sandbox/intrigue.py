"""Betrayal, espionage and sabotage — the game, and its blast radius.

All three are real moves with real consequences *inside the sandbox*: standing
collapses, reputation is spent, shadow equity moves, and the whole thing is
recorded. None of them can move a real position, a real allocation or a real
firm's status, because the sandbox is handed a read-only view of the ledger
(``guard.py``) and cannot write those tables at all.

That boundary changes what each move means, and it is worth being precise
rather than pretending otherwise:

**Betrayal** is fully real. Leaving an alliance for a reward, at the cost of
reputation, needs nothing but the sandbox's own tables.

**Espionage** copies a rival's genome into the sandbox record. It does *not*
install it: a stolen genome enters the real system only by going through the
strategy court like any other submission, and being deployed by a human. So
the theft is real, the advantage is earned separately, and the audit trail
shows both.

**Sabotage** moves ``shadow_equity`` — the sandbox's own scoreboard — and
never the target's books. This is the one place the game is deliberately
weaker than the fiction: a saboteur can win the *tournament* and cannot cost
the operator a cent. The alternative is a system where a drawdown might be
someone else's doing, which would make every kill reason unfalsifiable and
every capital cut potentially a punishment of the victim.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ...db.connection import utcnow_iso
from ...money import D, ZERO, money
from ..competition.tokens import TokenLedger
from .alliances import AllianceRegistry
from .guard import ReadOnlyStore, SandboxViolation, SandboxWriter

BETRAYAL_REWARD = D(100)
BETRAYAL_REPUTATION = D(-50)
ESPIONAGE_COST = D(75)
ESPIONAGE_REPUTATION = D(-20)
SABOTAGE_COST = D(120)
SABOTAGE_REPUTATION = D(-35)


@dataclass
class Intrigue:
    event_type: str
    actor: str
    target: str = ""
    detail: str = ""
    payload: dict = None
    shadow_equity: Decimal = ZERO
    caught: bool = False
    id: Optional[int] = None

    def __str__(self) -> str:
        caught = " — CAUGHT" if self.caught else ""
        return f"[{self.event_type}] {self.actor} -> {self.target or '-'}: {self.detail}{caught}"


class IntrigueEngine:
    def __init__(
        self,
        store: ReadOnlyStore,
        writer: SandboxWriter,
        tokens: TokenLedger,
        alliances: Optional[AllianceRegistry] = None,
        season: int = 1,
        seed: int = 20260808,
    ):
        self.store = store
        self.writer = writer
        self.tokens = tokens
        self.alliances = alliances or AllianceRegistry(store, writer, tokens)
        self.season = season
        self.seed = seed

    # =====================================================================
    def betray(self, actor: str, alliance_name: str) -> Intrigue:
        """Break a pact for a reward. Costs reputation, and everyone sees."""
        alliance = self.alliances.require(alliance_name)
        if actor not in alliance.members:
            raise SandboxViolation(f"{actor} is not a member of {alliance_name}")
        if not alliance.is_active:
            raise SandboxViolation(f"{alliance_name} is already {alliance.status}")

        remaining = [m for m in alliance.members if m != actor]
        self.writer.update(
            "alliances",
            alliance.id,
            {
                "members": json.dumps(remaining),
                "status": "broken",
                "broken_by": actor,
                "broken_at": utcnow_iso(),
                "standing": max(ZERO, alliance.standing - D(40)),
            },
        )

        self.tokens.award(
            actor, BETRAYAL_REWARD, f"betrayed {alliance_name}", category="betrayal",
            payload={"alliance": alliance_name, "victims": remaining},
        )
        self.tokens.adjust_reputation(actor, BETRAYAL_REPUTATION, f"betrayed {alliance_name}")
        for victim in remaining:
            self.tokens.adjust_reputation(victim, 10, f"betrayed by {actor}")

        return self._record(
            Intrigue(
                "betrayal",
                actor,
                ", ".join(remaining),
                f"broke {alliance_name}: +{BETRAYAL_REWARD} tokens, "
                f"{BETRAYAL_REPUTATION} reputation",
                {"alliance": alliance_name, "victims": remaining},
            ),
            alliance_id=alliance.id,
            reputation_delta=BETRAYAL_REPUTATION,
        )

    # =====================================================================
    def spy(self, actor: str, target: str) -> Intrigue:
        """Copy a rival's genome into the sandbox record. Installs nothing."""
        self._require_firms(actor, target)
        self.tokens.spend(actor, ESPIONAGE_COST, f"espionage against {target}", category="espionage")

        victim = self.store.get_firm(target)
        stolen = dict(victim.genome or {})
        caught = self._roll(f"spy:{actor}:{target}") < 0.35
        if caught:
            self.tokens.adjust_reputation(actor, ESPIONAGE_REPUTATION, f"caught spying on {target}")
            self.tokens.adjust_reputation(target, 5, f"caught {actor} spying")

        return self._record(
            Intrigue(
                "espionage",
                actor,
                target,
                (
                    f"copied {target}'s genome ({len(stolen)} genes). It is recorded here "
                    "only — to use it, submit it to the strategy court like anything else"
                ),
                {
                    "stolen_genome": stolen,
                    "universe": list(victim.universe or []),
                    "usable_via": "trade court submit",
                },
                caught=caught,
            ),
            reputation_delta=ESPIONAGE_REPUTATION if caught else ZERO,
        )

    # =====================================================================
    def sabotage(self, actor: str, target: str) -> Intrigue:
        """Attack a rival's *shadow* standing. The real books are untouched."""
        self._require_firms(actor, target)
        self.tokens.spend(actor, SABOTAGE_COST, f"sabotage against {target}", category="sabotage")

        before = self.shadow_equity(target)
        damage = money(D(2000) + D(int(self._roll(f"sab:{actor}:{target}") * 6000)))
        after = money(before - damage)
        caught = self._roll(f"sabcaught:{actor}:{target}") < 0.5

        if caught:
            self.tokens.adjust_reputation(actor, SABOTAGE_REPUTATION, f"caught sabotaging {target}")
            self.tokens.adjust_reputation(target, 10, f"survived sabotage by {actor}")

        return self._record(
            Intrigue(
                "sabotage",
                actor,
                target,
                (
                    f"cost {target} {damage} of SHADOW equity ({before} -> {after}). "
                    "The real ledger is untouched: a drawdown has to stay a fact about "
                    "the firm's own strategy, or no kill reason means anything"
                ),
                {"damage": str(damage), "shadow_before": str(before), "shadow_after": str(after)},
                shadow_equity=after,
                caught=caught,
            ),
            reputation_delta=SABOTAGE_REPUTATION if caught else ZERO,
        )

    # =====================================================================
    # shadow scoreboard
    # =====================================================================
    def shadow_equity(self, firm_key: str) -> Decimal:
        """The sandbox's own running total for a firm.

        Seeded from the firm's real equity the first time it is asked for, and
        moved only by sandbox events thereafter. Reading the real number is
        allowed; writing it is not.
        """
        row = self.writer.query_one(
            "SELECT shadow_equity FROM sandbox_events WHERE target = ? AND event_type = ? "
            "ORDER BY id DESC LIMIT 1",
            (firm_key, "sabotage"),
        )
        if row is not None and row.get("shadow_equity") is not None:
            return money(row["shadow_equity"])
        firm = self.store.get_firm(firm_key)
        return money(firm.cash) if firm else ZERO

    def scoreboard(self) -> list:
        out = []
        for firm_key in self.store.firm_keys():
            balance = self.tokens.balance(firm_key)
            out.append(
                {
                    "firm": firm_key,
                    "shadow_equity": self.shadow_equity(firm_key),
                    "tokens": balance.balance,
                    "reputation": balance.reputation,
                    "alliances": len(self.alliances.for_firm(firm_key)),
                }
            )
        return sorted(out, key=lambda r: -r["shadow_equity"])

    # =====================================================================
    def events(self, limit: int = 30, event_type: Optional[str] = None) -> list:
        if event_type:
            return self.writer.query(
                "SELECT * FROM sandbox_events WHERE event_type = ? ORDER BY id DESC LIMIT ?",
                (event_type, limit),
            )
        return self.writer.query(
            "SELECT * FROM sandbox_events ORDER BY id DESC LIMIT ?", (limit,)
        )

    # -- internals ---------------------------------------------------------
    def _require_firms(self, *keys) -> None:
        known = set(self.store.firm_keys())
        missing = [k for k in keys if k not in known]
        if missing:
            raise SandboxViolation(f"unknown firm(s): {', '.join(missing)}")
        if len(set(keys)) != len(keys):
            raise SandboxViolation("a firm cannot target itself")

    def _roll(self, key: str) -> float:
        """Deterministic given the seed — the intrigue replays like everything else."""
        return random.Random(f"{self.seed}:{self.season}:{key}").random()

    def _record(
        self,
        event: Intrigue,
        alliance_id: Optional[int] = None,
        reputation_delta=ZERO,
    ) -> Intrigue:
        event.id = self.writer.insert(
            "sandbox_events",
            {
                "season": self.season,
                "event_type": event.event_type,
                "actor": event.actor,
                "target": event.target,
                "alliance_id": alliance_id,
                "detail": event.detail + (" [CAUGHT]" if event.caught else ""),
                "payload": json.dumps(event.payload or {}, default=str),
                "shadow_equity": money(event.shadow_equity),
                "reputation_delta": D(reputation_delta),
                # Never true. Asserted by tests/test_trading_sandbox.py.
                "real_effect": False,
                "created_at": utcnow_iso(),
            },
        )
        return event


__all__ = [
    "BETRAYAL_REWARD",
    "ESPIONAGE_COST",
    "Intrigue",
    "IntrigueEngine",
    "SABOTAGE_COST",
]
