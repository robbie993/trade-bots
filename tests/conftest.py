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


# =========================================================================
# Trading Edition (src/trading)
# =========================================================================
@pytest.fixture
def trading_config(tmp_path):
    """A trading config pinned to tmp_path, with a fixed data seed.

    The seed matters: every trading test that touches prices must be
    reproducible, so no test may depend on the ambient TRADE_* environment.
    """
    from src.trading.config import DataConfig, TradingConfig

    return TradingConfig(
        firms_config=tmp_path / "firms.yaml",
        audit_vault=tmp_path / "vault",
        vendor_dir=tmp_path / "vendor",
        data=DataConfig(source="synthetic", seed=12345, history_days=180),
    )


@pytest.fixture
def feed(trading_config):
    from src.trading.data.feeds import SyntheticFeed

    return SyntheticFeed(seed=trading_config.data.seed, days=trading_config.data.history_days)


@pytest.fixture
def market(feed):
    from src.trading.data.market_data import MarketData

    return MarketData(feed, ["SPY", "QQQ", "BTC-USD"])


@pytest.fixture
def market_data(feed):
    """A MarketData positioned mid-history — for tests that need marks."""
    from src.trading.data.market_data import MarketData

    data = MarketData(feed, ["SPY", "QQQ", "BTC-USD"])
    data.seek(150)
    return data


@pytest.fixture
def store(db):
    from src.trading.store import TradingStore

    return TradingStore(db)


@pytest.fixture
def firm_record(store):
    """One funded firm, saved, with a universe the market fixture covers."""
    from src.trading.models import FirmRecord

    return store.upsert_firm(
        FirmRecord(
            firm_key="test_firm",
            name="Test Firm",
            asset_class="ETF",
            allocation=Decimal("100000.00"),
            initial_allocation=Decimal("100000.00"),
            cash=Decimal("100000.00"),
            high_water_mark=Decimal("100000.00"),
            risk_limit=Decimal("0.02"),
            universe=["SPY", "QQQ"],
            genome={"fast_window": 10, "slow_window": 30, "trend_bias": 60},
        )
    )


@pytest.fixture
def firms_yaml(tmp_path) -> Path:
    path = tmp_path / "firms.yaml"
    path.write_text(
        "firms:\n"
        "  alpha:\n"
        "    name: \"Alpha\"\n"
        "    asset_class: ETF\n"
        "    risk_limit: 0.02\n"
        "    capital_allocation: 50000\n"
        "    universe:\n"
        "      - SPY\n"
        "      - QQQ\n"
        "    analysts:\n"
        "      - technical\n"
        "      - sentiment\n"
        "    genome:\n"
        "      trend_bias: 70\n"
        "  beta:\n"
        "    name: \"Beta\"\n"
        "    asset_class: Crypto\n"
        "    capital_allocation: 25000\n"
        "    universe: [BTC-USD]\n"
        "    analysts: [technical]\n"
    )
    return path


@pytest.fixture
def ecosystem(db, tmp_path, firms_yaml, notifier):
    """A fully wired ecosystem on a temp database, with firms created."""
    from src.trading.config import DataConfig, TradingConfig
    from src.trading.ecosystem import Ecosystem

    config = TradingConfig(
        firms_config=firms_yaml,
        audit_vault=tmp_path / "vault",
        vendor_dir=tmp_path / "vendor",
        data=DataConfig(source="synthetic", seed=12345, history_days=180),
    )
    app_config = Config(
        database_url=db.url, notification_log=tmp_path / "notifications.log"
    )
    eco = Ecosystem(db, config, app_config, notifier)
    eco.init_firms()
    return eco


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
