from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.agents.cash_ledger import CashLedger
from src.config import CashConfig, Config, DiscoveryConfig, GateConfig, StopConfig
from src.db import Database
from src.agents.human_gate import HumanGate
from src.agents.experiment_ledger import ExperimentLedger
from src.models import Experiment
from src.notifications import NullNotifier


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database.from_url(f"sqlite:///{tmp_path / 'test.db'}")
    database.init_schema()
    yield database
    database.close()


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        notification_log=tmp_path / "notifications.log",
        scout_fixture_path=tmp_path / "products.json",
        cash=CashConfig(),
        gate=GateConfig(),
        discovery=DiscoveryConfig(),
        stop=StopConfig(),
    )


@pytest.fixture
def notifier() -> NullNotifier:
    return NullNotifier()


@pytest.fixture
def ledger(db) -> ExperimentLedger:
    return ExperimentLedger(db)


@pytest.fixture
def cash(db, config) -> CashLedger:
    return CashLedger(db, config.cash)


@pytest.fixture
def gate(db, notifier, config) -> HumanGate:
    return HumanGate(db, notifier, config.gate, config)


def make_experiment(**overrides) -> Experiment:
    """A healthy experiment that clears every gate and every kill condition."""
    defaults = dict(
        product_id="test-1",
        product_name="Test Product",
        source_platform="aliexpress",
        supplier="Test Supplier",
        unit_cost=Decimal("10.00"),
        selling_price=Decimal("40.00"),
        impressions=100_000,
        clicks=2_000,  # CTR 2%
        sessions=1_500,
        orders=60,  # CR 4%
        revenue=Decimal("2400.00"),
        ad_spend=Decimal("600.00"),  # CAC $10, under 50% of $40
        refunds=1,
        chargebacks=0,
        avg_delivery_days=Decimal("7"),
        status="active",
    )
    defaults.update(overrides)
    return Experiment(**defaults)


@pytest.fixture
def healthy() -> Experiment:
    return make_experiment()


@pytest.fixture
def fixture_file(tmp_path) -> Path:
    path = tmp_path / "products.json"
    path.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "product_id": "ae-1",
                        "product_name": "Good margin widget",
                        "source_platform": "aliexpress",
                        "supplier": "Supplier A",
                        "unit_cost": 5.00,
                        "shipping_cost": 2.00,
                    },
                    {
                        "product_id": "ae-2",
                        "product_name": "No margin widget",
                        "source_platform": "aliexpress",
                        "supplier": "Supplier B",
                        "unit_cost": 4.00,
                        "shipping_cost": 1.00,
                        "selling_price": 5.20,
                    },
                    {
                        "product_id": "x-1",
                        "product_name": "Unapproved platform widget",
                        "source_platform": "wish",
                        "supplier": "Supplier C",
                        "unit_cost": 3.00,
                    },
                ]
            }
        )
    )
    return path
