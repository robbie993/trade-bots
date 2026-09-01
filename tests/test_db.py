"""The database layer: both dialects, one interface."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.db import Database, sum_decimal, to_datetime, to_iso


def test_schema_creates_the_spec_tables(db):
    # Superset, not `>=`: on lists that operator compares lexicographically,
    # so it passed by accident and would break on any alphabetically-early
    # table added later (`file_cases` is what caught it).
    assert set(db.table_names()) >= {
        "cash_flow",
        "experiment_events",
        "experiments",
        "file_cases",
        "human_approvals",
        "orders",
        # Trading Edition — one schema, one database, one approvals table.
        "brokerage_events",
        "council_rulings",
        "fills",
        "firm_performance",
        "firms",
        "positions",
        "strategy_genomes",
        "trade_memory",
        "trade_proposals",
    }


def test_init_schema_is_idempotent(db):
    db.init_schema()
    db.init_schema()
    assert "experiments" in db.table_names()


def test_insert_returns_the_new_id(db):
    first = db.insert("experiments", {"product_id": "a", "product_name": "A"})
    second = db.insert("experiments", {"product_id": "b", "product_name": "B"})
    assert second > first


def test_decimals_round_trip_without_binary_drift(db):
    db.insert("cash_flow", {"description": "x", "category": "other", "amount": Decimal("0.07")})
    row = db.query_one("SELECT amount FROM cash_flow")
    assert Decimal(str(row["amount"])) == Decimal("0.07")


def test_money_is_summed_in_python_not_sql(db):
    for _ in range(10):
        db.insert("cash_flow", {"description": "x", "category": "other", "amount": Decimal("0.10")})
    rows = db.query("SELECT amount FROM cash_flow")
    assert sum_decimal(r["amount"] for r in rows) == Decimal("1.00")


def test_booleans_survive_the_round_trip(db):
    db.insert("experiments", {"product_id": "a", "meets_sample_gate": True, "ads_paused": False})
    row = db.query_one("SELECT meets_sample_gate, ads_paused FROM experiments")
    assert bool(row["meets_sample_gate"]) is True
    assert bool(row["ads_paused"]) is False


def test_transaction_rolls_back_on_error(db):
    db.insert("experiments", {"product_id": "a"})
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.insert("experiments", {"product_id": "b"})
            raise RuntimeError("boom")
    assert db.query_one("SELECT COUNT(*) AS n FROM experiments")["n"] == 1


def test_transaction_commits_on_success(db):
    with db.transaction():
        db.insert("experiments", {"product_id": "a"})
        db.insert("experiments", {"product_id": "b"})
    assert db.query_one("SELECT COUNT(*) AS n FROM experiments")["n"] == 2


def test_unique_product_id_is_enforced_by_the_database(db):
    db.insert("experiments", {"product_id": "dup"})
    with pytest.raises(Exception):
        db.insert("experiments", {"product_id": "dup"})


def test_placeholders_are_translated_for_postgres():
    pg = Database("postgresql://localhost/nope")
    assert pg.dialect == "postgres"
    assert pg._sql("SELECT * FROM t WHERE a = ? AND b = ?") == "SELECT * FROM t WHERE a = %s AND b = %s"
    assert pg.migrations_dir().name == "migrations"


def test_sqlite_urls_resolve(tmp_path):
    assert Database(f"sqlite:///{tmp_path / 'x.db'}").dialect == "sqlite"
    memory = Database("sqlite:///:memory:")
    memory.init_schema()
    assert "experiments" in memory.table_names()
    memory.close()


def test_timestamp_helpers():
    when = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    assert to_iso(when) == "2026-03-04T05:06:07Z"
    assert to_datetime("2026-03-04T05:06:07Z") == when
    assert to_datetime(None) is None
    assert to_datetime("not a date") is None
    # naive input is treated as UTC rather than silently shifting
    assert to_datetime(datetime(2026, 3, 4)).tzinfo is timezone.utc


def test_postgres_migrations_match_the_sqlite_ones():
    """Every migration must define the same tables and columns in both
    dialects, or the Postgres deployment and the local one are not the same
    system. Also checks the two sets have the same migration filenames."""
    import re
    from src.db.connection import MIGRATIONS

    def tables(sql: str) -> dict:
        out = {}
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S
        ):
            name, body = match.group(1), match.group(2)
            columns = set()
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("--"):
                    continue
                token = line.split()[0]
                if token.upper() in {"CREATE", "PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT"}:
                    continue
                columns.add(token)
            out[name] = columns
        return out

    pg_files = sorted(p.name for p in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    lite_files = sorted(p.name for p in (MIGRATIONS / "sqlite").glob("[0-9][0-9][0-9]_*.sql"))
    assert pg_files == lite_files
    # 001-006 the product village, 007-010 the trading edition,
    # 011-014 the court, competition, market and sandbox, 015 the flow,
    # 016 the council, 017 the switches on the wall, 018 published signals,
    # 019 which feed built a position, 020 which feed measured a firm,
    # 021 on which market bars it measured it.
    assert len(pg_files) == 23

    for name in pg_files:
        pg = tables((MIGRATIONS / name).read_text())
        lite = tables((MIGRATIONS / "sqlite" / name).read_text())
        assert set(pg) == set(lite), f"{name} defines different tables"
        for table in pg:
            assert pg[table] == lite[table], f"{name}: {table} columns differ"
