"""The meme radar: DEX flow as a vote, launches as a log, and never a crash."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.trading import intel
from src.trading.meme_radar import COINS, MemeRadar, flow_reading
from src.trading.signals import SignalBoard

WIF = next(c for c in COINS if c.symbol == "WIF-USD")
DOGE = next(c for c in COINS if c.symbol == "DOGE-USD")


def _pool(buys, sells, liq=1_000_000, change=1.5):
    return {"liquidity": {"usd": liq}, "txns": {"h6": {"buys": buys, "sells": sells}},
            "priceChange": {"h6": change}, "volume": {"h24": 900_000}}


class _Market:
    symbols = ["DOGE-USD", "SHIB-USD", "PEPE-USD", "WIF-USD", "BTC-USD"]

    def as_of(self):
        return datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc)


def _fake(pairs_by_address=None, fail=()):
    pairs_by_address = pairs_by_address or {}
    calls = []

    def get(url, params, headers=None):
        calls.append(url)
        assert "User-Agent" in headers
        for bad in fail:
            if bad in url:
                raise RuntimeError(f"HTTP 403 from {bad}")
        if "/tokens/v1/" in url:
            return pairs_by_address.get(url.rsplit("/", 1)[1], [_pool(100, 100)])
        if "token-boosts" in url:
            return [{"chainId": "solana", "tokenAddress": "Abc", "url": "https://dexscreener.com/x",
                     "description": "a boosted coin", "totalAmount": 500, "links": []}]
        return [{"mint": "Mint1", "name": "Frog", "symbol": "FROG", "usd_market_cap": 12345,
                 "complete": False}]

    get.calls = calls
    return get


def test_more_buys_than_sells_is_an_up_vote():
    r = flow_reading(WIF, [_pool(900, 300)])
    assert r.symbol == "WIF-USD" and r.score == Decimal("50.0")
    assert "900 buys / 300 sells" in r.note


def test_the_deepest_pool_speaks_for_the_coin():
    shallow = _pool(1000, 0, liq=10)
    deep = _pool(0, 1000, liq=10_000_000)
    assert flow_reading(WIF, [shallow, deep]).score < 0


def test_a_few_trades_cannot_shout():
    few = flow_reading(WIF, [_pool(9, 1)])
    many = flow_reading(WIF, [_pool(900, 100)])
    assert few.score == many.score
    assert few.confidence < many.confidence / 5


def test_doge_is_capped_because_that_pool_is_not_where_doge_trades():
    assert flow_reading(DOGE, [_pool(100_000, 0)]).confidence <= DOGE.max_confidence


def test_no_pool_or_no_trades_is_silence():
    assert flow_reading(WIF, []) is None
    assert flow_reading(WIF, [_pool(0, 0)]) is None


def test_the_radar_publishes_once_a_bar_and_logs_launches(db):
    board = SignalBoard(db)
    get = _fake({WIF.address: [_pool(600, 200)]})
    radar = MemeRadar(board, db, get_json=get)
    notes = radar.run(_Market())
    assert any("WIF-USD" in n for n in notes)
    rows = db.query("SELECT symbol FROM signals WHERE publisher = 'meme_radar'")
    assert {r["symbol"] for r in rows} == {c.symbol for c in COINS}
    assert {x["source"] for x in intel.recent(db)} == {
        "dexscreener_boosts", "pumpfun_top", "pumpfun_live"}

    before = len(get.calls)
    assert radar.run(_Market()) == []          # same bar: nothing fetched again
    assert len(get.calls) == before


def test_a_blocked_source_is_named_and_the_rest_still_runs(db):
    radar = MemeRadar(SignalBoard(db), db, get_json=_fake(fail=("pump.fun",)))
    notes = radar.run(_Market())
    assert any("FAILING" in n and "pumpfun" in n for n in notes)
    assert db.query("SELECT COUNT(*) AS n FROM signals WHERE publisher = 'meme_radar'")[0]["n"] == 4


def test_only_coins_the_village_trades_get_a_vote(db):
    class Narrow(_Market):
        symbols = ["WIF-USD"]

    MemeRadar(SignalBoard(db), db, get_json=_fake()).run(Narrow())
    rows = db.query("SELECT symbol FROM signals WHERE publisher = 'meme_radar'")
    assert [r["symbol"] for r in rows] == ["WIF-USD"]


def test_seeing_a_launch_again_moves_it_rather_than_duplicating_it(db):
    intel.upsert(db, "pumpfun_top", "Mint1", title="Frog", score=1)
    intel.upsert(db, "pumpfun_top", "Mint1", title="Frog", score=2)
    rows = intel.recent(db, "pumpfun_top")
    assert len(rows) == 1 and float(rows[0]["score"]) == 2


def test_the_radar_is_off_unless_switched_on(ecosystem):
    assert ecosystem.meme_radar is None
