"""The guard — why the intrigue cannot reach the money.

The sandbox holds the adversarial half of the game: alliances, betrayal,
espionage, sabotage. All of it is fun, and none of it may touch the ledger.

The reason is not squeamishness, it is measurement. The brokerage cuts capital
on a score, and the firm kill switch's reason has to be reproducible from
stored metrics. If one firm could move another's equity, then "Drawdown 22%
exceeds 20%" would stop being a fact about that firm's strategy, the allocator
would start punishing victims, and every kill reason in the system would
become a guess. Sabotage would not make the ecosystem more interesting; it
would make it unmeasurable.

So the sandbox is handed two objects and nothing else:

``ReadOnlyStore``   the ledger, reads only. Every write method of
                    ``TradingStore`` raises ``SandboxViolation``, and so does
                    any attribute not on the allow-list — including ``db``,
                    so nobody can reach around it to raw SQL.
``SandboxWriter``   inserts and updates restricted to ``alliances`` and
                    ``sandbox_events``. Any other table raises.

Both are enforced by ``__getattr__``, not by convention, so a future edit that
tries to write through the sandbox fails loudly at the first call rather than
silently corrupting a firm's books.
"""

from __future__ import annotations

from typing import Optional

from ...db.connection import Database
from ..store import TradingStore

# Everything the intrigue is allowed to know about the real ecosystem.
READABLE = frozenset(
    {
        "firms",
        "active_firms",
        "get_firm",
        "get_firm_by_id",
        "require_firm_by_id",
        "positions",
        "get_position",
        "fills",
        "proposals",
        "realized_pnl",
        "fees_paid",
        "cash_delta_total",
        "performance_history",
        "latest_performance",
        "cash_view",
        "events",
    }
)

# The only tables the sandbox may write.
WRITABLE_TABLES = frozenset({"alliances", "sandbox_events"})


class SandboxViolation(RuntimeError):
    """The sandbox tried to reach outside itself."""


class ReadOnlyStore:
    """A ``TradingStore`` with the writes removed."""

    def __init__(self, store: TradingStore):
        object.__setattr__(self, "_store", store)

    def __getattr__(self, name: str):
        if name in READABLE:
            return getattr(object.__getattribute__(self, "_store"), name)
        raise SandboxViolation(
            f"the sandbox may not use TradingStore.{name}. It reads the ledger and "
            f"writes only to {', '.join(sorted(WRITABLE_TABLES))} — see "
            "src/trading/sandbox/guard.py for why."
        )

    def __setattr__(self, name: str, value):
        raise SandboxViolation("the sandbox may not mutate the store")

    def firm_keys(self) -> list:
        return [f.firm_key for f in self.firms()]


class SandboxWriter:
    """Inserts and updates, restricted to the sandbox's own two tables."""

    def __init__(self, db: Database):
        self._db = db

    def _check(self, table: str) -> None:
        if table not in WRITABLE_TABLES:
            raise SandboxViolation(
                f"the sandbox may not write to {table!r}; only "
                f"{', '.join(sorted(WRITABLE_TABLES))} are its own"
            )

    def insert(self, table: str, values: dict) -> int:
        self._check(table)
        return self._db.insert(table, values)

    def update(self, table: str, row_id: int, values: dict) -> None:
        self._check(table)
        self._db.update(table, row_id, values)

    def query(self, sql: str, params=()) -> list:
        """Reads are unrestricted — the sandbox is allowed to *look* anywhere."""
        return self._db.query(sql, params)

    def query_one(self, sql: str, params=()) -> Optional[dict]:
        return self._db.query_one(sql, params)


def sandbox_handles(store: TradingStore):
    """The pair every sandbox object is constructed from."""
    return ReadOnlyStore(store), SandboxWriter(store.db)


__all__ = [
    "READABLE",
    "ReadOnlyStore",
    "SandboxViolation",
    "SandboxWriter",
    "WRITABLE_TABLES",
    "sandbox_handles",
]
