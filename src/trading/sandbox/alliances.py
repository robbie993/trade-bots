"""Alliances — pacts between firms, and the standing that comes with them.

An alliance is an agreement recorded in the sandbox. It does not pool capital,
share positions, or change what any firm trades: the pods go on reading their
own genomes and their own universes exactly as before. What it changes is the
game — who is aligned with whom, and therefore what a betrayal costs.

Reputation is the currency of the pact and is kept in the token ledger, which
also has no exchange rate to capital. Keeping the whole social layer priced in
points is what allows it to be as cut-throat as you like.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Sequence

from ...db.connection import utcnow_iso
from ...money import D, ZERO
from ..competition.tokens import TokenLedger
from .guard import ReadOnlyStore, SandboxViolation, SandboxWriter


@dataclass
class Alliance:
    id: Optional[int]
    name: str
    members: list = field(default_factory=list)
    founder: str = ""
    pact: str = ""
    standing: Decimal = D(100)
    status: str = "active"
    broken_by: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @classmethod
    def from_row(cls, row: dict) -> "Alliance":
        try:
            members = json.loads(row.get("members") or "[]")
        except (TypeError, ValueError):
            members = []
        return cls(
            id=row.get("id"),
            name=row.get("name") or "",
            members=members,
            founder=row.get("founder") or "",
            pact=row.get("pact") or "",
            standing=D(row.get("standing")),
            status=row.get("status") or "active",
            broken_by=row.get("broken_by"),
        )

    def __str__(self) -> str:
        return (
            f"{self.name} [{self.status}] — {', '.join(self.members)} "
            f"(standing {self.standing})"
            + (f", broken by {self.broken_by}" if self.broken_by else "")
        )


class AllianceRegistry:
    def __init__(
        self,
        store: ReadOnlyStore,
        writer: SandboxWriter,
        tokens: Optional[TokenLedger] = None,
    ):
        self.store = store
        self.writer = writer
        self.tokens = tokens

    # -- reads -------------------------------------------------------------
    def all(self, status: Optional[str] = None) -> list:
        if status:
            rows = self.writer.query(
                "SELECT * FROM alliances WHERE status = ? ORDER BY id", (status,)
            )
        else:
            rows = self.writer.query("SELECT * FROM alliances ORDER BY id")
        return [Alliance.from_row(r) for r in rows]

    def get(self, name: str) -> Optional[Alliance]:
        row = self.writer.query_one("SELECT * FROM alliances WHERE name = ?", (name,))
        return Alliance.from_row(row) if row else None

    def require(self, name: str) -> Alliance:
        alliance = self.get(name)
        if alliance is None:
            raise SandboxViolation(f"no alliance named {name!r}")
        return alliance

    def for_firm(self, firm_key: str) -> list:
        return [a for a in self.all("active") if firm_key in a.members]

    # -- writes ------------------------------------------------------------
    def form(
        self, name: str, founder: str, members: Sequence[str], pact: str = ""
    ) -> Alliance:
        known = set(self.store.firm_keys())
        roster = list(dict.fromkeys([founder, *members]))
        unknown = [m for m in roster if m not in known]
        if unknown:
            raise SandboxViolation(f"unknown firm(s): {', '.join(unknown)}")
        if len(roster) < 2:
            raise SandboxViolation("an alliance needs at least two firms")
        if self.get(name) is not None:
            raise SandboxViolation(f"an alliance named {name!r} already exists")

        self.writer.insert(
            "alliances",
            {
                "name": name,
                "members": json.dumps(roster),
                "founder": founder,
                "pact": pact or f"{', '.join(roster)} agree to act together",
                "standing": D(100),
                "status": "active",
                "formed_at": utcnow_iso(),
            },
        )
        alliance = self.require(name)
        self.writer.insert(
            "sandbox_events",
            {
                "event_type": "alliance",
                "actor": founder,
                "alliance_id": alliance.id,
                "detail": f"{founder} formed {name} with {', '.join(roster[1:])}",
                "payload": json.dumps({"members": roster, "pact": alliance.pact}),
                "real_effect": False,
                "created_at": utcnow_iso(),
            },
        )
        if self.tokens is not None:
            for member in roster:
                self.tokens.adjust_reputation(member, 5, f"joined alliance {name}")
        return alliance

    def join(self, name: str, firm_key: str) -> Alliance:
        alliance = self.require(name)
        if not alliance.is_active:
            raise SandboxViolation(f"{name} is {alliance.status}")
        if firm_key not in self.store.firm_keys():
            raise SandboxViolation(f"unknown firm {firm_key}")
        if firm_key in alliance.members:
            return alliance
        alliance.members.append(firm_key)
        self.writer.update("alliances", alliance.id, {"members": json.dumps(alliance.members)})
        self.writer.insert(
            "sandbox_events",
            {
                "event_type": "alliance",
                "actor": firm_key,
                "alliance_id": alliance.id,
                "detail": f"{firm_key} joined {name}",
                "payload": json.dumps({"members": alliance.members}),
                "real_effect": False,
                "created_at": utcnow_iso(),
            },
        )
        return self.require(name)

    def leave(self, name: str, firm_key: str) -> Alliance:
        """Walking away openly. Cheap — unlike betrayal, which is not."""
        alliance = self.require(name)
        if firm_key not in alliance.members:
            raise SandboxViolation(f"{firm_key} is not in {name}")
        alliance.members.remove(firm_key)
        status = "dissolved" if len(alliance.members) < 2 else alliance.status
        self.writer.update(
            "alliances",
            alliance.id,
            {"members": json.dumps(alliance.members), "status": status},
        )
        self.writer.insert(
            "sandbox_events",
            {
                "event_type": "alliance",
                "actor": firm_key,
                "alliance_id": alliance.id,
                "detail": f"{firm_key} left {name} openly",
                "payload": json.dumps({"remaining": alliance.members}),
                "reputation_delta": D(-5),
                "real_effect": False,
                "created_at": utcnow_iso(),
            },
        )
        if self.tokens is not None:
            self.tokens.adjust_reputation(firm_key, -5, f"left alliance {name}")
        return self.require(name)

    def adjust_standing(self, name: str, delta, reason: str) -> Alliance:
        alliance = self.require(name)
        standing = max(ZERO, min(D(200), alliance.standing + D(delta)))
        self.writer.update("alliances", alliance.id, {"standing": standing})
        self.writer.insert(
            "sandbox_events",
            {
                "event_type": "alliance",
                "actor": "",
                "alliance_id": alliance.id,
                "detail": f"{name} standing {alliance.standing} -> {standing}: {reason}",
                "real_effect": False,
                "created_at": utcnow_iso(),
            },
        )
        return self.require(name)


__all__ = ["Alliance", "AllianceRegistry"]
