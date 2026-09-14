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
                    ``sandbox_events``. Any other table raises. Its ``query``
                    pair takes SQL, so the statement itself is checked too.

Both are enforced by ``__getattr__``, not by convention, so a future edit that
tries to write through the sandbox fails loudly at the first call rather than
silently corrupting a firm's books.

**The query passthrough was the hole in exactly that claim.** ``_check(table)``
guarded ``insert`` and ``update``, and ``query`` handed its string straight to
``Database.query``, which is ``cursor.execute`` and does not care what verb it
is given. So ``writer.query("UPDATE firms SET equity = 1.0")`` wrote, and
committed, and survived a reconnect — through the object whose entire purpose
is that it cannot do that. The test named
``test_the_sandbox_cannot_reach_raw_sql`` asserted that the *attribute* ``db``
was refused, which it was, while the raw-SQL door on the same object stood
open. That is the difference between asking "did the code run" and asking "is
the guarantee true", and it is why ``read_only_sql`` now checks the statement
rather than trusting the caller to only pass reads.

The check refuses anything it cannot *prove* is a read: comments and string
literals are stripped, the first keyword must open a read, no write verb may
appear anywhere (which is what stops ``WITH ... DELETE``), and a statement
separator is refused outright. Erring toward refusal is the right direction
here — a false refusal is a loud failure in a cosmetic subsystem, a false
acceptance is a silently corrupted ledger.
"""

from __future__ import annotations

import re
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

# Statements that can only read. `values` is here for the dialect that allows
# a bare VALUES list as a query; `explain` reports a plan without running it.
READ_OPENERS = frozenset({"select", "with", "explain", "values"})

# Any of these anywhere in a statement disqualifies it. `with` is a read
# opener, but `WITH x AS (...) DELETE FROM firms` is not a read — scanning the
# whole statement rather than just its first word is what catches that.
WRITE_WORDS = frozenset(
    {
        "insert", "update", "delete", "replace", "upsert", "merge", "truncate",
        "create", "drop", "alter", "rename", "reindex", "vacuum",
        "attach", "detach", "pragma", "copy", "call",
        "begin", "commit", "rollback", "savepoint", "release",
        "grant", "revoke",
    }
)

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_LITERALS = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", re.DOTALL)
_WORDS = re.compile(r"[a-z_]+")


class SandboxViolation(RuntimeError):
    """The sandbox tried to reach outside itself."""


def read_only_sql(sql: str) -> str:
    """Return `sql` unchanged if it can only read; raise otherwise.

    Deliberately conservative. It strips comments and quoted text first so
    that a literal like ``'delete me'`` or a column quoted as ``"update"``
    cannot trip it, then works on the bare words that are left.
    """
    bare = _LITERALS.sub(" ", _COMMENTS.sub(" ", sql or ""))

    # One statement only. SQLite's `execute` already refuses a second one, but
    # that is a property of one driver and this has to hold for both.
    if ";" in bare.strip().rstrip(";"):
        raise SandboxViolation(
            "the sandbox may not run more than one statement at a time — see "
            "src/trading/sandbox/guard.py for why."
        )

    words = _WORDS.findall(bare.lower())
    if not words:
        raise SandboxViolation("the sandbox was handed a statement with no SQL in it")

    if words[0] not in READ_OPENERS:
        raise SandboxViolation(
            f"the sandbox may only read; {words[0].upper()} is not one of "
            f"{', '.join(sorted(w.upper() for w in READ_OPENERS))} — see "
            "src/trading/sandbox/guard.py for why."
        )

    found = sorted(set(words) & WRITE_WORDS)
    if found:
        raise SandboxViolation(
            f"the sandbox may not run {', '.join(w.upper() for w in found)} — "
            "it reads the ledger and writes only to "
            f"{', '.join(sorted(WRITABLE_TABLES))}. See "
            "src/trading/sandbox/guard.py for why."
        )
    return sql


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
        """*Reads* are unrestricted — the sandbox may look at any table it likes.

        It is the reading that is unrestricted, not the statement. This used to
        pass the string straight through, which made the table allow-list above
        decorative: `cursor.execute` runs whatever verb it is given.
        """
        return self._db.query(read_only_sql(sql), params)

    def query_one(self, sql: str, params=()) -> Optional[dict]:
        return self._db.query_one(read_only_sql(sql), params)


def sandbox_handles(store: TradingStore):
    """The pair every sandbox object is constructed from."""
    return ReadOnlyStore(store), SandboxWriter(store.db)


__all__ = [
    "READABLE",
    "READ_OPENERS",
    "ReadOnlyStore",
    "SandboxViolation",
    "SandboxWriter",
    "WRITABLE_TABLES",
    "WRITE_WORDS",
    "read_only_sql",
    "sandbox_handles",
]
