"""The ledger on a real Postgres, where the hosted village runs.

SQLite forgives what Postgres does not: a failed statement inside a transaction
that the code catches and ignores leaves SQLite's transaction usable and
Postgres's aborted. That difference let the hosted village fail to open any new
position for a day while every SQLite test passed. These run only when
TEST_POSTGRES_URL points at a disposable Postgres database.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

URL = os.environ.get("TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set")


@pytest.fixture
def pg():
    import psycopg

    from src.db.connection import Database

    name = f"t_{uuid.uuid4().hex[:10]}"
    admin = psycopg.connect(URL, autocommit=True)
    admin.execute(f"CREATE DATABASE {name}")
    db = Database.from_url(URL.rsplit("/", 1)[0] + f"/{name}")
    db.init_schema()
    yield db
    db.close()
    admin.execute(f"DROP DATABASE {name}")
    admin.close()


def test_a_first_buy_settles_on_postgres(pg):
    from src.trading.models import Fill, FirmRecord
    from src.trading.store import TradingStore

    store = TradingStore(pg)
    firm = store.upsert_firm(FirmRecord(firm_key="f", name="F", asset_class="Cryptocurrency",
                                        strategy="s", venue="paper", allocation=Decimal("20000"),
                                        cash=Decimal("20000"), universe=["SOL-USD"]))
    fill = Fill(firm_id=firm.id, symbol="SOL-USD", side="buy", quantity=Decimal("6.5"),
                price=Decimal("122"), fee=Decimal("1"), slippage=Decimal("0"), venue="paper")
    fill.priced_by = "alpaca"
    store.settle(firm, fill)
    assert store.get_position(firm.id, "SOL-USD").quantity == Decimal("6.5")
    assert store.provenance(firm.id) == {"SOL-USD": "alpaca"}


def test_tables_without_an_id_accept_inserts(pg):
    assert pg.insert("firm_strikes", {"firm_id": 1, "strikes": 1}) == 0
    assert pg.query_one("SELECT strikes FROM firm_strikes WHERE firm_id = 1")["strikes"] == 1
    assert pg.insert("fleet_snapshots", {"source": "s", "fetched_at": "x", "payload": "{}"}) > 0
