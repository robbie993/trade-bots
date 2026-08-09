"""The sandbox — alliances, betrayal, espionage, sabotage.

    guard.py      the read-only ledger view and the two-table writer
    alliances.py  pacts, membership, standing
    intrigue.py   betrayal, espionage, sabotage, and the shadow scoreboard

Everything here is a game played *over* the ledger and never *on* it. The
sandbox is constructed from ``sandbox_handles(store)``, which hands it a
``ReadOnlyStore`` and a ``SandboxWriter`` restricted to ``alliances`` and
``sandbox_events``. Both raise ``SandboxViolation`` on anything else.
"""

from __future__ import annotations

from typing import Optional

from ..competition.tokens import TokenLedger
from ..store import TradingStore
from .alliances import Alliance, AllianceRegistry
from .guard import (
    READABLE,
    WRITABLE_TABLES,
    ReadOnlyStore,
    SandboxViolation,
    SandboxWriter,
    sandbox_handles,
)
from .intrigue import Intrigue, IntrigueEngine


class Sandbox:
    """One object holding the whole adversarial layer, safely wired."""

    def __init__(
        self,
        store: TradingStore,
        tokens: Optional[TokenLedger] = None,
        season: int = 1,
        seed: int = 20260808,
    ):
        self.reader, self.writer = sandbox_handles(store)
        self.tokens = tokens or TokenLedger(store.db, season)
        self.alliances = AllianceRegistry(self.reader, self.writer, self.tokens)
        self.intrigue = IntrigueEngine(
            self.reader, self.writer, self.tokens, self.alliances, season, seed
        )

    # Convenience passthroughs — the verbs a player actually uses.
    def form(self, name, founder, members, pact=""):
        return self.alliances.form(name, founder, members, pact)

    def betray(self, actor, alliance_name):
        return self.intrigue.betray(actor, alliance_name)

    def spy(self, actor, target):
        return self.intrigue.spy(actor, target)

    def sabotage(self, actor, target):
        return self.intrigue.sabotage(actor, target)

    def scoreboard(self):
        return self.intrigue.scoreboard()

    def events(self, limit: int = 30, event_type: Optional[str] = None):
        return self.intrigue.events(limit, event_type)

    def render(self) -> str:
        rows = self.scoreboard()
        if not rows:
            return "(no firms in the sandbox)"
        columns = list(rows[0].keys())
        widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in columns}
        header = "  ".join(c.ljust(widths[c]) for c in columns)
        sep = "  ".join("-" * widths[c] for c in columns)
        body = ["  ".join(str(r[c]).ljust(widths[c]) for c in columns) for r in rows]
        return "\n".join([header, sep, *body]) + (
            "\n(shadow equity is the sandbox's own scoreboard — sabotage moves it, "
            "and never the real ledger)"
        )


__all__ = [
    "READABLE",
    "WRITABLE_TABLES",
    "Alliance",
    "AllianceRegistry",
    "Intrigue",
    "IntrigueEngine",
    "ReadOnlyStore",
    "Sandbox",
    "SandboxViolation",
    "SandboxWriter",
    "sandbox_handles",
]
