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

    with pytest.raises(FeedNotConfigured, match="alpaca or ccxt"):
        build_feed(DataConfig(source="carrier_pigeon"))


# =========================================================================
# a chain, because a real universe is rarely all one thing
#
# Pointing the village at a crypto exchange priced BTC-USD perfectly and could
# not price SPY at all — and because a feed refuses rather than degrades, one
# unlistable symbol stopped every firm, including the ones it had nothing to
# do with. Every tick died on `binance returned nothing for SPY`.
# =========================================================================
def test_a_chain_falls_through_to_the_next_feed(fake_ccxt, monkeypatch):
    from src.trading.data.feeds import Bar, ChainFeed

    fake_ccxt.only = {"BTC/USDT"}

    class Equities:
        name = "pretend-yahoo"

        def series(self, symbol):
            if symbol == "SPY":
                return [
                    Bar(symbol=symbol, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        open=D1, high=D1, low=D1, close=D1, volume=D1)
                ]
            raise FeedNotConfigured(f"no {symbol} here")

    D1 = Decimal("1")
    chain = ChainFeed([CcxtFeed(days=60), Equities()])

    assert chain.series("BTC-USD"), "the exchange should still serve crypto"
    assert chain.series("SPY"), "and the fallback should serve the equity"


def test_the_chain_remembers_which_feed_answered(fake_ccxt):
    """The dangerous version of a fallback is the silent one."""
    from src.trading.data.feeds import Bar, ChainFeed

    fake_ccxt.only = {"BTC/USDT"}

    class Equities:
        name = "pretend-yahoo"

        def series(self, symbol):
            one = Decimal("1")
            return [Bar(symbol=symbol, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        open=one, high=one, low=one, close=one, volume=one)]

    chain = ChainFeed([CcxtFeed(days=60), Equities()])
    chain.series("BTC-USD")
    chain.series("SPY")

    assert chain.origin("BTC-USD") == "ccxt"
    assert chain.origin("SPY") == "pretend-yahoo"
    assert chain.name == "ccxt+pretend-yahoo"


def test_a_symbol_no_feed_can_price_still_refuses(fake_ccxt):
    """Falling through every feed is not the same as inventing a price."""
    from src.trading.data.feeds import ChainFeed

    fake_ccxt.only = {"NOTHING"}

    class Nope:
        name = "nope"

        def series(self, symbol):
            raise FeedNotConfigured("not here either")

    chain = ChainFeed([CcxtFeed(days=60), Nope()])
    with pytest.raises(FeedNotConfigured, match="no feed in the chain could price"):
        chain.series("DOGE-USD")


def test_mixing_synthetic_with_real_prices_warns():
    """Legitimate for a demo, a lie in production — so it says so out loud."""
    from src.trading.data.feeds import ChainFeed, SyntheticFeed

    class Real:
        name = "yahoo"

        def series(self, symbol):
            return []

    with pytest.warns(UserWarning, match="mixes synthetic prices with real ones"):
        ChainFeed([Real(), SyntheticFeed(seed=1, days=60)])


def test_an_all_real_chain_is_quiet(fake_ccxt):
    import warnings as w

    from src.trading.data.feeds import ChainFeed

    class Real:
        name = "yahoo"

        def series(self, symbol):
            return []

    with w.catch_warnings():
        w.simplefilter("error")
        ChainFeed([CcxtFeed(days=60), Real()])


def test_a_comma_separated_source_builds_a_chain():
    from src.trading.config import DataConfig
    from src.trading.data.feeds import build_feed

    feed = build_feed(DataConfig(source="ccxt,yahoo", history_days=60))
    assert feed.name == "ccxt+yahoo"


def test_one_source_is_still_one_feed_not_a_chain_of_one():
    from src.trading.config import DataConfig
    from src.trading.data.feeds import build_feed

    assert build_feed(DataConfig(source="synthetic")).name == "synthetic"


# =========================================================================
# one symbol nothing can price
#
# The chain fixed SPY by falling through to Yahoo. PEPE-USD had nowhere to
# fall: Binance does not list it and Yahoo returned an unreadable payload. The
# refusal came out of `as_of()`, so every tick raised and all eleven firms in a
# real village went quiet for three hours over one meme coin.
#
# A feed refusing is right about a symbol and catastrophic about the market.
# =========================================================================
def _blind_market(bad="PEPE-USD"):
    from src.trading.data.feeds import SyntheticFeed
    from src.trading.data.market_data import MarketData

    class OneBadSymbol:
        name = "mostly-fine"

        def __init__(self):
            self.real = SyntheticFeed(seed=12345, days=180)

        def series(self, symbol):
            if symbol == bad:
                raise FeedNotConfigured(f"nothing anywhere lists {symbol}")
            return self.real.series(symbol)

    return MarketData(OneBadSymbol(), ["SPY", "QQQ", bad])


def test_an_unpriceable_symbol_does_not_stop_the_market():
    market = _blind_market()

    assert market.as_of() is not None, "the other symbols still have a clock"
    assert market.mark("SPY") > 0
    assert market.bar("PEPE-USD") is None


def test_the_refusal_is_remembered_rather_than_swallowed():
    """Quietly having no price is how you end up trading on nothing."""
    market = _blind_market()
    market.as_of()

    assert "PEPE-USD" in market.unpriceable
    assert "nothing anywhere lists" in market.unpriceable["PEPE-USD"]
    assert "SPY" not in market.unpriceable


def test_the_tick_survives_and_says_what_it_could_not_price(
    db, tmp_path, firms_yaml, notifier
):
    from src.config import Config
    from src.trading.config import DataConfig, TradingConfig
    from src.trading.ecosystem import Ecosystem

    firms_yaml.write_text(
        "firms:\n"
        "  alpha:\n"
        "    name: Alpha\n"
        "    asset_class: ETF\n"
        "    capital_allocation: 50000\n"
        "    universe: [SPY, QQQ]\n"
        "    analysts: [technical]\n"
        "  meme:\n"
        "    name: Meme\n"
        "    asset_class: Crypto\n"
        "    capital_allocation: 25000\n"
        "    universe: [SPY, PEPE-USD]\n"
        "    analysts: [technical]\n"
    )
    eco = Ecosystem(
        db,
        TradingConfig(firms_config=firms_yaml, audit_vault=tmp_path / "v",
                      vendor_dir=tmp_path / "d",
                      data=DataConfig(source="synthetic", seed=12345, history_days=180)),
        Config(database_url=db.url, notification_log=tmp_path / "n.log"),
        notifier,
    )
    eco.init_firms()

    market = _blind_market()
    market.register(["SPY", "QQQ", "PEPE-USD"])
    report = eco.tick(market)

    # The tick ran. That is the whole point.
    assert report.oversight is not None, "the tick completed"
    assert any("PEPE-USD" in note for note in report.bot_notes), "and said so"
    # The firm that has nothing to do with PEPE is unaffected.
    assert eco.store.get_firm("alpha").status == "active"


# =========================================================================
# alpaca: one feed for both halves of a universe
#
# Every other real feed here covers half. Yahoo knows SPY and not DOGE; a
# crypto exchange knows DOGE and not SPY; and Binance does not serve a US
# address at all, which is what actually stopped the village.
# =========================================================================
@pytest.fixture
def alpaca_keys(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key-123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret-456")


@pytest.fixture
def fake_http(monkeypatch):
    """Stand in for the one HTTP call the feeds make, recording what was asked.

    Patched at `_get_json` rather than at `requests`, which is what this
    originally faked. `requests` was never a declared dependency — it is in
    requirements.txt and not in pyproject — so `pip install -e .` produced a
    village whose every real feed refused on import, presenting as a market
    that had ceased to exist. The feeds run on urllib now, and this fixture
    fakes the seam that actually exists.
    """
    state = {"calls": [], "status": 200, "bars": 200, "payload": None}

    def rows(n):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            {"t": (base.replace(day=1 + (i % 27))).isoformat().replace("+00:00", "Z"),
             "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i, "v": 1000 + i}
            for i in range(n)
        ]

    def get_json(url, params=None, headers=None, timeout=None):
        from src.trading.data.feeds import FeedHTTPError

        state["calls"].append({"url": url, "params": params or {},
                               "headers": headers or {}})
        if state["status"] != 200:
            raise FeedHTTPError(state["status"], "nope")
        if state["payload"] is not None:
            return state["payload"]
        if "crypto" in url:
            pair = (params or {}).get("symbols")
            return {"bars": {pair: rows(state["bars"])}}
        return {"bars": rows(state["bars"])}

    monkeypatch.setattr("src.trading.data.feeds._get_json", get_json)
    return state


def test_a_stock_goes_to_the_stock_endpoint(alpaca_keys, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    bars = AlpacaFeed(days=60).series("SPY")

    assert len(bars) == 60
    call = fake_http["calls"][0]
    assert "/v2/stocks/SPY/bars" in call["url"]
    assert call["params"]["feed"] == "iex", "the free tier, unless told otherwise"


def test_a_pair_goes_to_the_crypto_endpoint(alpaca_keys, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    bars = AlpacaFeed(days=60).series("BTC-USD")

    assert bars
    call = fake_http["calls"][0]
    assert "crypto" in call["url"]
    assert call["params"]["symbols"] == "BTC/USD"
    assert all(b.symbol == "BTC-USD" for b in bars), "the village's spelling"


def test_the_credentials_are_sent_but_never_in_the_url(alpaca_keys, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    AlpacaFeed(days=60).series("SPY")
    call = fake_http["calls"][0]

    assert call["headers"]["APCA-API-KEY-ID"] == "key-123"
    assert call["headers"]["APCA-API-SECRET-KEY"] == "secret-456"
    assert "secret" not in str(call["params"]).lower()


def test_missing_credentials_say_what_to_set(monkeypatch, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    for name in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY",
                 "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(FeedNotConfigured, match="ALPACA_API_KEY_ID"):
        AlpacaFeed(days=60).series("SPY")


def test_alpacas_own_variable_names_also_work(monkeypatch, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    for name in ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")

    assert AlpacaFeed(days=60).series("SPY")


def test_a_refused_key_says_so_rather_than_returning_nothing(alpaca_keys, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    fake_http["status"] = 403
    with pytest.raises(FeedNotConfigured, match="refused the credentials"):
        AlpacaFeed(days=60).series("SPY")


def test_a_refused_stock_key_mentions_the_tier(alpaca_keys, fake_http):
    """403 on stocks is usually asking for SIP on a free account."""
    from src.trading.data.feeds import AlpacaFeed

    fake_http["status"] = 401
    with pytest.raises(FeedNotConfigured, match="subscription"):
        AlpacaFeed(days=60).series("SPY")


def test_a_truncated_history_is_refused_here_too(alpaca_keys, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    fake_http["bars"] = 12
    with pytest.raises(FeedNotConfigured, match="truncated history"):
        AlpacaFeed(days=60).series("SPY")


def test_money_stays_decimal(alpaca_keys, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    bar = AlpacaFeed(days=60).series("SPY")[0]
    assert isinstance(bar.close, Decimal)
    assert bar.as_of.tzinfo is not None


def test_alpaca_is_reachable_by_configuration(alpaca_keys, fake_http):
    from src.trading.config import DataConfig
    from src.trading.data.feeds import build_feed

    feed = build_feed(DataConfig(source="alpaca", history_days=60))
    assert feed.name == "alpaca"
    assert feed.series("SPY")


def test_one_source_covers_a_mixed_universe(alpaca_keys, fake_http):
    """The whole point: no chain needed for stocks plus crypto."""
    from src.trading.data.feeds import AlpacaFeed

    feed = AlpacaFeed(days=60)
    assert feed.series("SPY")
    assert feed.series("BTC-USD")
    assert feed.series("AAPL")


# =========================================================================
# the window, which is not optional
# =========================================================================
def test_a_start_date_is_always_sent(alpaca_keys, fake_http):
    """Without `start`, Alpaca answers from the beginning of the current day —
    one bar. `limit` caps how many come back and does nothing to widen the
    window, so a village asking for 180 days got one bar per symbol, refused
    every one as a truncated history, and reported itself blind while the API
    was working perfectly."""
    from src.trading.data.feeds import AlpacaFeed

    AlpacaFeed(days=60, min_interval_s=0).series("SPY")
    params = fake_http["calls"][0]["params"]
    assert "start" in params, "no start date: Alpaca will return a single bar"
    assert params["start"] < datetime.now(timezone.utc).date().isoformat()


def test_the_window_is_calendar_days_not_trading_days(alpaca_keys, fake_http):
    """180 trading days is about 260 days of calendar. Asking for 180 calendar
    days silently shortens every indicator by a fifth."""
    from datetime import date

    from src.trading.data.feeds import AlpacaFeed

    AlpacaFeed(days=180, min_interval_s=0).series("SPY")
    start = date.fromisoformat(fake_http["calls"][0]["params"]["start"])
    assert (date.today() - start).days > 250


def test_crypto_asks_for_a_window_too(alpaca_keys, fake_http):
    from src.trading.data.feeds import AlpacaFeed

    AlpacaFeed(days=60, min_interval_s=0).series("BTC-USD")
    assert "start" in fake_http["calls"][0]["params"]


def test_a_rate_limit_is_retried_before_being_believed(alpaca_keys, fake_http,
                                                       monkeypatch):
    """A 429 is the one refusal that is *known* to be temporary. Treating it as
    "this symbol has no price" is how a village goes blind over a burst it
    caused itself."""
    from src.trading.data.feeds import AlpacaFeed

    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = {"n": 0}
    real = fake_http

    def flaky(url, params=None, headers=None, timeout=None):
        from src.trading.data.feeds import FeedHTTPError

        calls["n"] += 1
        if calls["n"] == 1:
            raise FeedHTTPError(429, '{"message": "too many requests."}')
        real["calls"].append({"url": url, "params": params or {}, "headers": headers or {}})
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return {"bars": [
            {"t": base.replace(day=1 + (i % 27)).isoformat().replace("+00:00", "Z"),
             "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 10}
            for i in range(120)
        ]}

    monkeypatch.setattr("src.trading.data.feeds._get_json", flaky)
    bars = AlpacaFeed(days=60, min_interval_s=0).series("SPY")
    assert len(bars) == 60
    assert calls["n"] == 2, "the 429 should have been retried once"


def test_a_second_rate_limit_says_what_to_change(alpaca_keys, monkeypatch):
    from src.trading.data.feeds import AlpacaFeed, FeedHTTPError, FeedNotConfigured

    monkeypatch.setattr("time.sleep", lambda _s: None)

    def always_limited(url, params=None, headers=None, timeout=None):
        raise FeedHTTPError(429, "too many requests")

    monkeypatch.setattr("src.trading.data.feeds._get_json", always_limited)
    with pytest.raises(FeedNotConfigured, match="MVV_TICK_INTERVAL"):
        AlpacaFeed(days=60, min_interval_s=0).series("SPY")


def test_requests_are_spaced_out(alpaca_keys, fake_http, monkeypatch):
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    from src.trading.data.feeds import AlpacaFeed

    feed = AlpacaFeed(days=60, min_interval_s=0.35)
    feed.series("SPY")
    feed.series("QQQ")
    assert any(s > 0 for s in slept), "forty symbols in a burst will rate-limit"


# =========================================================================
# trusting a certificate, which is not the same as skipping one
# =========================================================================
def test_the_ssl_context_verifies(monkeypatch):
    """The one-line "fix" for a certificate error is to stop checking them,
    which would make every price this village reads spoofable by anyone on the
    network path. It must never be that."""
    import ssl

    from src.trading.data import feeds

    monkeypatch.setattr(feeds, "_SSL_CONTEXT", None)
    context = feeds._ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_certifi_is_preferred_when_it_is_installed(monkeypatch):
    """Python from python.org does not use the macOS keychain, so the system
    store is empty until somebody runs Install Certificates.command. certifi
    is what `requests` used, and why this never broke before the feeds moved
    to the standard library."""
    import ssl

    from src.trading.data import feeds

    used = {}
    real = ssl.create_default_context

    def spy(*args, **kwargs):
        used.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(feeds, "_SSL_CONTEXT", None)
    monkeypatch.setattr(ssl, "create_default_context", spy)
    feeds._ssl_context()
    try:
        import certifi  # noqa: F401
    except ImportError:
        assert "cafile" not in used          # system store, correct on Linux
    else:
        assert used.get("cafile"), "certifi is installed and should be used"


def test_a_certificate_failure_says_how_to_fix_the_machine(monkeypatch):
    """`could not reach ...: CERTIFICATE_VERIFY_FAILED` reads as a network
    problem or a broken feed. It is neither, and the remedy is one command."""
    from urllib.error import URLError

    from src.trading.data import feeds

    def refuse(*args, **kwargs):
        raise URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(feeds.FeedNotConfigured) as caught:
        feeds._get_json("https://example.test/bars", {})

    message = str(caught.value)
    assert "Install Certificates.command" in message
    assert "certifi" in message
    assert "Do not disable certificate verification" in message
