"""Real exchange bars, through ccxt.

The village's own feed is a seeded synthetic one, and that is the default for
a reason: the tests, the backtests and the evolution loop are replays, and a
replay that quietly started depending on an exchange being up would stop being
a replay. This feed is what you point it at when you want real prices.

Nothing here touches the network. ccxt is faked, which is the only honest way
to test a feed — a test that needs an exchange to be up is not a test.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.trading.data.feeds import CcxtFeed, FeedNotConfigured, build_feed


# =========================================================================
# how a symbol is spelled
# =========================================================================
def test_the_village_spelling_is_translated_for_the_exchange():
    """The village says BTC-USD. Exchanges say BTC/USD, and mean BTC/USDT."""
    assert CcxtFeed.candidates("BTC-USD") == ["BTC/USD", "BTC/USDT", "BTC/USDC"]


def test_an_equity_symbol_is_left_alone():
    assert CcxtFeed.candidates("SPY") == ["SPY"]


def test_the_translation_meets_the_importer_halfway():
    """A bot written against an exchange says BTC/USD; the importer respells
    it to BTC-USD; this turns it back. The round trip has to survive."""
    from src.trading.importer import _feed_symbol

    village = _feed_symbol("BTC/USD")
    assert village == "BTC-USD"
    assert "BTC/USD" in CcxtFeed.candidates(village)


# =========================================================================
# a fake exchange
# =========================================================================
class FakeExchange:
    def __init__(self, options=None):
        self.options = options or {}
        self.asked: list = []
        self.bars = 200
        self.only: set = set()
        self.raises: dict = {}

    def fetch_ohlcv(self, symbol, timeframe="1d", limit=100):
        self.asked.append((symbol, timeframe, limit))
        if symbol in self.raises:
            raise self.raises[symbol]
        if self.only and symbol not in self.only:
            return []
        base = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
        return [
            [int(base + i * 86_400_000), 100 + i, 101 + i, 99 + i, 100.5 + i, 1_000 + i]
            for i in range(min(self.bars, limit))
        ]


@pytest.fixture
def fake_ccxt(monkeypatch):
    """Install a fake `ccxt` module and hand back its exchange."""
    import types

    exchange = FakeExchange()
    module = types.ModuleType("ccxt")
    module.binance = lambda options=None: exchange   # noqa: E731
    monkeypatch.setitem(sys.modules, "ccxt", module)
    return exchange


# =========================================================================
# fetching
# =========================================================================
def test_bars_come_back_in_the_villages_spelling(fake_ccxt):
    feed = CcxtFeed(exchange="binance", days=60)
    bars = feed.series("BTC-USD")

    assert len(bars) == 60
    assert all(b.symbol == "BTC-USD" for b in bars), "not the venue's spelling"
    assert isinstance(bars[0].close, Decimal), "money never touches a float"
    assert bars[0].as_of.tzinfo is not None


def test_it_falls_back_to_the_stablecoin_pair(fake_ccxt):
    """Most crypto exchanges quote USDT, not dollars. Refusing on that
    technicality would be pedantry rather than safety."""
    fake_ccxt.only = {"BTC/USDT"}

    bars = CcxtFeed(days=60).series("BTC-USD")
    assert bars
    assert [call[0] for call in fake_ccxt.asked] == ["BTC/USD", "BTC/USDT"]


def test_an_exchange_that_raises_on_one_spelling_still_gets_tried_on_the_next(fake_ccxt):
    fake_ccxt.raises["BTC/USD"] = ValueError("no such market")
    fake_ccxt.only = {"BTC/USDT"}

    assert CcxtFeed(days=60).series("BTC-USD")


def test_the_series_is_cached(fake_ccxt):
    feed = CcxtFeed(days=60)
    feed.series("BTC-USD")
    feed.series("BTC-USD")
    assert len(fake_ccxt.asked) == 1


def test_rate_limiting_is_on(monkeypatch):
    """A feed that gets the village banned from an exchange is worse than a
    slow one."""
    import types

    seen = {}

    def make(options=None):
        seen.update(options or {})
        return FakeExchange(options)

    module = types.ModuleType("ccxt")
    module.binance = make
    monkeypatch.setitem(sys.modules, "ccxt", module)

    CcxtFeed(days=60).series("BTC-USD")
    assert seen.get("enableRateLimit") is True


# =========================================================================
# refusing, rather than degrading
# =========================================================================
def test_a_truncated_history_is_refused(fake_ccxt):
    """A short history silently changes every moving average in the system.
    That is a worse failure than not starting."""
    fake_ccxt.bars = 10

    with pytest.raises(FeedNotConfigured, match="truncated history"):
        CcxtFeed(days=60).series("BTC-USD")


def test_a_symbol_the_exchange_does_not_have_is_refused(fake_ccxt):
    fake_ccxt.only = {"NOTHING"}

    with pytest.raises(FeedNotConfigured, match="returned nothing"):
        CcxtFeed(days=60).series("DOGE-USD")


def test_an_unknown_exchange_is_refused(fake_ccxt):
    with pytest.raises(FeedNotConfigured, match="no exchange called"):
        CcxtFeed(exchange="not_an_exchange").series("BTC-USD")


def test_a_missing_library_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "ccxt", None)

    with pytest.raises(FeedNotConfigured, match="pip install ccxt"):
        CcxtFeed().series("BTC-USD")


# =========================================================================
# it never becomes the default by accident
# =========================================================================
def test_the_default_feed_is_still_the_deterministic_one(monkeypatch):
    """A seeded replay that depends on an exchange being up is not a replay."""
    from src.trading.config import DataConfig

    monkeypatch.delenv("TRADE_DATA_SOURCE", raising=False)
    assert build_feed(DataConfig()).name == "synthetic"


def test_ccxt_is_reachable_by_configuration(fake_ccxt):
    from src.trading.config import DataConfig

    feed = build_feed(DataConfig(source="ccxt", history_days=60))
    assert feed.name == "ccxt"
    assert feed.series("BTC-USD")


def test_the_exchange_is_configurable(monkeypatch, fake_ccxt):
    from src.trading.config import DataConfig

    monkeypatch.setenv("TRADE_CCXT_EXCHANGE", "kraken")
    feed = build_feed(DataConfig(source="ccxt"))
    assert feed.exchange_id == "kraken"


def test_an_unknown_source_still_names_what_it_expected():
    from src.trading.config import DataConfig

    with pytest.raises(FeedNotConfigured, match="synthetic, csv, yahoo or ccxt"):
        build_feed(DataConfig(source="carrier_pigeon"))
