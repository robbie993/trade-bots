"""Market data: determinism, and the cursor that cannot see the future."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.trading.data.feeds import CsvFeed, FeedNotConfigured, SyntheticFeed, build_feed
from src.trading.data.market_data import MarketData


def test_the_synthetic_feed_is_reproducible():
    first = SyntheticFeed(seed=7, days=60).series("SPY")
    second = SyntheticFeed(seed=7, days=60).series("SPY")
    assert [b.close for b in first] == [b.close for b in second]
    assert all(isinstance(b.close, Decimal) for b in first)


def test_different_seeds_and_symbols_diverge():
    a = SyntheticFeed(seed=7, days=60).series("SPY")
    b = SyntheticFeed(seed=8, days=60).series("SPY")
    c = SyntheticFeed(seed=7, days=60).series("QQQ")
    assert [x.close for x in a] != [x.close for x in b]
    assert [x.close for x in a] != [x.close for x in c]


def test_synthetic_bars_are_internally_consistent():
    for bar in SyntheticFeed(seed=3, days=90).series("BTC-USD"):
        assert bar.low <= bar.open <= bar.high
        assert bar.low <= bar.close <= bar.high
        assert bar.close > 0


def test_the_cursor_never_reveals_the_future(market):
    market.seek(30)
    history = market.history("SPY")
    assert len(history) == 31
    assert market.bar("SPY") is history[-1]

    full = market.feed.series("SPY")
    assert market.mark("SPY") == full[30].close
    assert market.mark("SPY") != full[31].close


def test_advance_moves_one_bar_at_a_time(market):
    market.seek(10)
    market.advance()
    assert market.cursor == 11
    market.advance(5)
    assert market.cursor == 16


def test_advance_stops_at_the_end(market):
    market.seek(market.length() - 1)
    market.advance(50)
    assert market.cursor == market.length() - 1
    assert market.at_end()


def test_length_is_the_shortest_common_history(feed):
    market = MarketData(feed, ["SPY", "QQQ"])
    assert market.length() == len(feed.series("SPY"))


def test_is_ready_gates_on_bar_count(market):
    market.seek(5)
    assert not market.is_ready("SPY", 60)
    market.seek(120)
    assert market.is_ready("SPY", 60)


def test_returns_skip_zero_priced_gaps(market):
    market.seek(60)
    returns = market.returns("SPY", 30)
    assert len(returns) == 29
    assert all(isinstance(r, Decimal) for r in returns)


def test_register_adds_symbols_once(market):
    market.register(["spy", "IWM"])
    assert market.symbols.count("SPY") == 1
    assert "IWM" in market.symbols


def test_csv_feed_reads_a_file(tmp_path):
    path = tmp_path / "SPY.csv"
    path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-01-02,100,102,99,101,1000\n"
        "2026-01-01,99,101,98,100,900\n"
    )
    bars = CsvFeed(tmp_path).series("SPY")
    assert [str(b.close) for b in bars] == ["100", "101"]  # sorted by date


def test_csv_feed_says_what_is_missing(tmp_path):
    with pytest.raises(FeedNotConfigured) as exc:
        CsvFeed(tmp_path).series("NOPE")
    assert "NOPE.csv" in str(exc.value)


def test_unknown_source_is_refused(trading_config):
    from src.trading.config import DataConfig

    with pytest.raises(FeedNotConfigured):
        build_feed(DataConfig(source="bloomberg"))


def test_build_feed_returns_the_configured_source(trading_config):
    assert build_feed(trading_config.data).name == "synthetic"
