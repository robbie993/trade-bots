"""Database access.

The spec names PostgreSQL as the source of truth, and it is: point
``DATABASE_URL`` at a Postgres instance and the migrations in
``src/db/migrations/`` are applied verbatim. SQLite is supported as a zero-setup backend for local runs
and the test suite, because a Phase 1 system whose tests need a database
server does not get run.

Both backends go through the same tiny interface, so no calling code knows
which one it is talking to:

    db = Database.from_url(cfg.database_url)
    db.init_schema()
    rows = db.query("SELECT * FROM experiments WHERE status = ?", ("active",))

SQL is written with ``?`` placeholders and rewritten to ``%s`` for psycopg.
Money is never summed in SQL — SQLite's NUMERIC affinity stores it as a float
— so aggregation happens in Python over ``Decimal``. See src/money.py.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from ..config import REPO_ROOT

MIGRATIONS = REPO_ROOT / "src" / "db" / "migrations"

_PLACEHOLDER_RE = re.compile(r"\?")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def to_datetime(value: Any) -> Optional[datetime]:
    """Normalise whatever the backend handed back into an aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Database:
    """Minimal DB-API wrapper shared by both backends."""

    def __init__(self, url: str):
        self.url = url
        self.dialect = "postgres" if url.startswith(("postgres://", "postgresql://")) else "sqlite"
        self._conn = None

    # -- construction -----------------------------------------------------
    @classmethod
    def from_url(cls, url: str) -> "Database":
        return cls(url)

    @property
    def conn(self):
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _connect(self):
        if self.dialect == "postgres":
            try:
                import psycopg  # type: ignore
            except ImportError as exc:  # pragma: no cover - env dependent
                raise RuntimeError(
                    "DATABASE_URL points at PostgreSQL but psycopg is not installed. "
                    "Run `pip install 'psycopg[binary]'` or use a sqlite:/// URL."
                ) from exc
            from psycopg.rows import dict_row  # type: ignore

            return psycopg.connect(self.url, row_factory=dict_row, autocommit=True)

        # sqlite:///abs/path -> /abs/path ; sqlite:///:memory: -> :memory:
        target = self.url[len("sqlite://") :]
        if target.startswith("/"):
            target = target[1:]
        if target in ("", ":memory:"):
            target = ":memory:"
        else:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if target != ":memory:":
            # Two processes share this file the moment the village runs on its
            # own: the tick loop writes while the web server reads and the gate
            # writes approvals. SQLite's default journal makes those block each
            # other and fail with "database is locked"; WAL lets readers run
            # during a write, and the timeout covers the writer-vs-writer case
            # instead of giving up instantly.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- statements -------------------------------------------------------
    def _sql(self, sql: str) -> str:
        if self.dialect == "postgres":
            return _PLACEHOLDER_RE.sub("%s", sql)
        return sql

    def _params(self, params: Sequence[Any]) -> tuple:
        out = []
        for p in params:
            if isinstance(p, Decimal):
                out.append(str(p) if self.dialect == "sqlite" else p)
            elif isinstance(p, bool):
                out.append(int(p) if self.dialect == "sqlite" else p)
            elif isinstance(p, datetime):
                out.append(to_iso(p) if self.dialect == "sqlite" else p)
            elif isinstance(p, Path):
                out.append(str(p))
            else:
                out.append(p)
        return tuple(out)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), self._params(params))
        if self.dialect == "postgres":
            cur.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), self._params(params))
        rows = cur.fetchall()
        result = [dict(r) for r in rows]
        if self.dialect == "postgres":
            cur.close()
        return result

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, values: dict) -> int:
        """Insert a row and return its new id."""
        cols = list(values.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        params = self._params([values[c] for c in cols])
        cur = self.conn.cursor()
        if self.dialect == "postgres":
            cur.execute(self._sql(sql) + " RETURNING id", params)
            new_id = cur.fetchone()["id"]
            cur.close()
            return int(new_id)
        cur.execute(sql, params)
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, values: dict) -> None:
        if not values:
            return
        cols = list(values.keys())
        assignments = ", ".join(f"{c} = ?" for c in cols)
        sql = f"UPDATE {table} SET {assignments} WHERE id = ?"
        self.execute(sql, [values[c] for c in cols] + [row_id])

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """All-or-nothing block. Used wherever cash and state move together."""
        if self.dialect == "postgres":
            self.conn.autocommit = False
            try:
                yield self
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            finally:
                self.conn.autocommit = True
        else:
            self.conn.execute("BEGIN")
            try:
                yield self
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    # -- schema -----------------------------------------------------------
    def migrations_dir(self) -> Path:
        """Numbered migrations (spec section 12).

        The Postgres files are the spec's schema. The sqlite/ subdirectory is
        a mirror in SQLite's type spellings; a test compares the two directory
        by directory so the local system and the deployed one cannot drift.
        """
        return MIGRATIONS if self.dialect == "postgres" else MIGRATIONS / "sqlite"

    def migration_files(self) -> list[Path]:
        return sorted(self.migrations_dir().glob("[0-9][0-9][0-9]_*.sql"))

    def init_schema(self) -> None:
        """Apply every migration in order. Each is idempotent."""
        for path in self.migration_files():
            sql = path.read_text()
            if self.dialect == "sqlite":
                self.conn.executescript(sql)
            else:
                cur = self.conn.cursor()
                cur.execute(sql)
                cur.close()

    def table_names(self) -> list[str]:
        if self.dialect == "postgres":
            rows = self.query(
                "SELECT tablename AS name FROM pg_tables WHERE schemaname = 'public'"
            )
        else:
            rows = self.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        return sorted(r["name"] for r in rows)


def sum_decimal(values: Iterable[Any]) -> Decimal:
    """Sum money in Python, never in SQL."""
    from ..money import D

    total = Decimal("0")
    for v in values:
        total += D(v)
    return total
