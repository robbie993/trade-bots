"""Copy a village's SQLite ledger into Postgres, once, and prove it arrived.

Why this exists: the village is moving off one machine's `data/mvv.db` and onto
a Postgres that every machine can reach (Railway). A SQLite file cannot be
shared — syncing a live WAL database through Dropbox or a network share
corrupts it quietly — so the history has to be carried across exactly once,
and after that Postgres is the only ledger.

    # stop the loop first: a copy of a database that is still being written is
    # a copy of some moment nobody chose
    python scripts/sqlite_to_postgres.py --sqlite data/mvv.db \
        --postgres "$DATABASE_URL" --dry-run
    python scripts/sqlite_to_postgres.py --sqlite data/mvv.db \
        --postgres "$DATABASE_URL"

What it does, in order:

1. Applies the Postgres migrations to the target (idempotent).
2. Refuses a target that already holds rows, unless `--replace`. Merging two
   ledgers is not a copy; it is a decision about whose history is true.
3. Checks every value against the column it is going into *before* writing
   anything: a string too long for a VARCHAR, JSON that does not parse, a
   column SQLite has and Postgres does not. Any of those stops the run with a
   list, rather than failing a third of the way through.
4. Copies every table in foreign-key order, in one transaction, with COPY.
   Either the whole ledger arrives or none of it does.
5. Moves each SERIAL sequence past the highest copied id, so the first row the
   village writes afterwards does not collide with history.
6. Counts rows on both sides, table by table, and fails loudly if any differ.

It never writes to the SQLite file; it opens it read-only.

`--skip bouts` leaves the arena's history behind. On the Mac that table was
1.4M rows of "no contest" between dead firms (see commit 0a14e0f), most of the
database's size, and nothing reads old bouts to decide anything.

Standard library plus psycopg, which the hosted village already requires.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.db.connection import Database

SKIP_ALWAYS = {"sqlite_sequence"}


def _open_sqlite(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"no such SQLite file: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_tables(src: sqlite3.Connection) -> dict[str, list[str]]:
    names = [
        r[0]
        for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        if r[0] not in SKIP_ALWAYS
    ]
    return {n: [c[1] for c in src.execute(f'PRAGMA table_info("{n}")')] for n in names}


def _pg_columns(cur) -> dict[str, dict[str, dict]]:
    cur.execute(
        """
        SELECT table_name, column_name, data_type, character_maximum_length,
               column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """
    )
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for t, c, dtype, maxlen, default in cur.fetchall():
        out[t][c] = {"type": dtype, "maxlen": maxlen, "default": default}
    return out


def _pg_dependencies(cur) -> dict[str, set[str]]:
    """table -> the tables it references (and so must be loaded after)."""
    cur.execute(
        """
        SELECT tc.table_name, ccu.table_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
         AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
        """
    )
    deps: dict[str, set[str]] = defaultdict(set)
    for child, parent in cur.fetchall():
        if child != parent:
            deps[child].add(parent)
    return deps


def _load_order(tables: list[str], deps: dict[str, set[str]]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()

    def visit(t: str, stack: tuple[str, ...]) -> None:
        if t in seen:
            return
        if t in stack:
            raise SystemExit(f"foreign-key cycle through {' -> '.join(stack + (t,))}")
        for parent in sorted(deps.get(t, ())):
            if parent in tables:
                visit(parent, stack + (t,))
        seen.add(t)
        order.append(t)

    for t in sorted(tables):
        visit(t, ())
    return order


def _convert(value, col: dict, where: str, problems: list[str], notes: dict):
    """Turn one SQLite value into COPY text for its Postgres column, or record why not."""
    if value is None:
        return None
    dtype = col["type"]
    if isinstance(value, bytes):
        problems.append(f"{where}: binary value in a {dtype} column")
        return None
    if dtype == "boolean":
        if value in (0, 1, "0", "1", True, False):
            return "t" if int(value) else "f"
        if isinstance(value, str) and value.lower() in ("true", "false", "t", "f"):
            return value.lower()[0]
        problems.append(f"{where}: {value!r} is not a boolean")
        return None
    if dtype in ("json", "jsonb"):
        if isinstance(value, str) and value.strip() == "":
            notes["blank JSON written as NULL"] += 1
            return None
        text = value if isinstance(value, str) else json.dumps(value)
        try:
            json.loads(text)
        except (TypeError, ValueError):
            problems.append(f"{where}: not valid JSON: {str(value)[:60]!r}")
            return None
        return text
    if dtype.startswith("timestamp") or dtype == "date":
        if isinstance(value, str) and value.strip() == "":
            notes["blank timestamp written as NULL"] += 1
            return None
        return str(value)
    if dtype in ("integer", "bigint", "smallint"):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, int):
            return str(value)
        try:
            return str(int(str(value)))
        except ValueError:
            problems.append(f"{where}: {value!r} is not an integer")
            return None
    if isinstance(value, float):
        return repr(value)
    text = str(value)
    if col["maxlen"] is not None and len(text) > col["maxlen"]:
        problems.append(f"{where}: {len(text)} chars into VARCHAR({col['maxlen']})")
        return None
    return text


def _plan(src, pg_cols, tables, skip):
    """Validate everything first. Returns (plan, problems, notes)."""
    problems: list[str] = []
    notes: dict[str, int] = defaultdict(int)
    plan = {}
    for table, src_cols in tables.items():
        if table in skip:
            continue
        if table not in pg_cols:
            problems.append(f"{table}: exists in SQLite but not in the Postgres schema")
            continue
        missing = [c for c in src_cols if c not in pg_cols[table]]
        if missing:
            problems.append(f"{table}: SQLite columns with nowhere to go in Postgres: {missing}")
            continue
        plan[table] = src_cols
        cols = pg_cols[table]
        for row in src.execute(f'SELECT rowid AS _rid, * FROM "{table}"'):
            for c in src_cols:
                _convert(row[c], cols[c], f"{table}[{row['_rid']}].{c}", problems, notes)
            if len(problems) > 50:
                return plan, problems, notes
    return plan, problems, notes


def _target_rows(cur, tables) -> dict[str, int]:
    out = {}
    for t in tables:
        cur.execute(f'SELECT count(*) FROM "{t}"')
        out[t] = cur.fetchone()[0]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", required=True, type=Path, help="source .db file (opened read-only)")
    ap.add_argument("--postgres", required=True, help="target postgresql:// URL")
    ap.add_argument("--skip", action="append", default=[], help="table to leave behind (repeatable)")
    ap.add_argument("--replace", action="store_true", help="empty a non-empty target first")
    ap.add_argument("--dry-run", action="store_true", help="validate and report; write nothing")
    args = ap.parse_args(argv)

    if not args.postgres.startswith(("postgres://", "postgresql://")):
        raise SystemExit("--postgres must be a postgres:// or postgresql:// URL")

    import psycopg

    src = _open_sqlite(args.sqlite)
    tables = _sqlite_tables(src)
    skip = set(args.skip)

    target = Database.from_url(args.postgres)
    if not args.dry_run:
        target.init_schema()
        print("schema: migrations applied to target")
    target.close()

    with psycopg.connect(args.postgres) as pg:
        cur = pg.cursor()
        pg_cols = _pg_columns(cur)
        if not pg_cols:
            raise SystemExit("target has no tables yet - run without --dry-run, or `init-db` first")

        plan, problems, notes = _plan(src, pg_cols, tables, skip)
        order = _load_order(list(plan), _pg_dependencies(cur))
        counts = {t: src.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in order}

        print(f"source: {args.sqlite} - {len(tables)} tables, {sum(counts.values()):,} rows to copy")
        for t in sorted(skip & set(tables)):
            n = src.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            print(f"  skipped: {t} ({n:,} rows left behind)")
        for note, n in sorted(notes.items()):
            print(f"  note: {n:,} {note}")
        if problems:
            print(f"\nREFUSED - {len(problems)} value(s) would not survive the copy:")
            for p in problems[:50]:
                print(f"  {p}")
            return 1

        # Every village table in the target, skipped ones included: --replace
        # that left an old ledger's bouts beside a new ledger's firms would be
        # a merge by accident.
        village = sorted(t for t in pg_cols if t in tables or t in plan)
        existing = {t: n for t, n in _target_rows(cur, village).items() if n}
        if existing and not args.replace:
            print("\nREFUSED - the target already has rows:")
            for t, n in sorted(existing.items()):
                print(f"  {t}: {n:,}")
            print("Pass --replace to empty it first. That deletes what is there.")
            return 1

        if args.dry_run:
            for t in order:
                print(f"  would copy {t}: {counts[t]:,}")
            print("\ndry run: every value checked, nothing written")
            return 0

        with pg.transaction():
            if existing:
                names = ", ".join(f'"{t}"' for t in village)
                cur.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
                print(f"emptied {len(existing)} non-empty target table(s)")
            for t in order:
                cols = plan[t]
                pgc = pg_cols[t]
                collist = ", ".join(f'"{c}"' for c in cols)
                sink: list[str] = []
                with cur.copy(f'COPY "{t}" ({collist}) FROM STDIN') as copy:
                    for row in src.execute(f'SELECT rowid AS _rid, * FROM "{t}"'):
                        copy.write_row(
                            [_convert(row[c], pgc[c], t, sink, defaultdict(int)) for c in cols]
                        )
                if sink:
                    raise RuntimeError(f"{t}: value changed between check and copy: {sink[0]}")
                seq_default = (pgc.get("id") or {}).get("default") or ""
                if seq_default.startswith("nextval("):
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence('\"{t}\"', 'id'), "
                        f"COALESCE((SELECT max(id) FROM \"{t}\"), 0) + 1, false)"
                    )
                print(f"  copied {t}: {counts[t]:,}")

            after = _target_rows(cur, order)
            wrong = {t: (counts[t], after[t]) for t in order if counts[t] != after[t]}
            if wrong:
                for t, (a, b) in wrong.items():
                    print(f"  MISMATCH {t}: sqlite {a:,} vs postgres {b:,}")
                raise RuntimeError("row counts differ; rolled back, nothing kept")

    print(f"\ndone: {sum(counts.values()):,} rows in {len(order)} tables, counts match on both sides")
    print("next: `trade reconcile` against the new DATABASE_URL before trusting it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
