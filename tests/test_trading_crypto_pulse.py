"""The crypto pulse keeps btcc's formulas, stays neutral on OI until it has
history, and says which of its sources failed."""

from __future__ import annotations

from datetime import datetime, timezone

from src.trading.crypto_pulse import (CryptoPulse, funding_score, macro_score,
                                      sentiment_score)
from src.trading.signals import SignalBoard


def test_btcc_scales():
    assert funding_score(0.0005 / 8) == -1.0          # +0.05%/8h: crowded long
    assert funding_score(-0.0005 / 8) == 1.0
    assert sentiment_score(85) == -0.6 and sentiment_score(10) == 0.6
    assert sentiment_score(50) == 0
    assert macro_score(4.0) == 1.0 and macro_score(-8.0) == -1.0


class _Market:
    symbols = ["BTC-USD", "WIF-USD", "SPY"]

    def __init__(self, minute=0):
        self.minute = minute

    def as_of(self):
        return datetime(2026, 9, 25, 15, self.minute, tzinfo=timezone.utc)


def _post(oi):
    def post(url, body):
        assert body == {"type": "metaAndAssetCtxs"}
        return [{"universe": [{"name": "BTC"}, {"name": "WIF"}]},
                [{"funding": "0.0000125", "openInterest": str(oi), "markPx": "84000"},
                 {"funding": "0.0001", "openInterest": "10", "markPx": "0.25"}]]
    return post


def _get(fail=()):
    def get(url, params, headers=None):
        if any(f in url for f in fail):
            raise RuntimeError("HTTP 429")
        if "alternative.me" in url:
            return {"data": [{"value": "74"}]}
        return {"data": {"market_cap_change_percentage_24h_usd": -2.0}}
    return get


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def test_only_the_coins_the_village_trades_and_open_interest_waits(db):
    clock = Clock()
    pulse = CryptoPulse(SignalBoard(db), get_json=_get(), post_json=_post(100), clock=clock)
    pulse.run(_Market(0))
    rows = {r["symbol"]: r for r in db.query("SELECT * FROM signals")}
    assert set(rows) == {"BTC-USD", "WIF-USD"}
    assert "oi" not in rows["BTC-USD"]["note"]           # no history yet
    clock.t += 1900
    pulse._post = _post(120)
    pulse.run(_Market(15))
    latest = db.query("SELECT note FROM signals WHERE symbol = 'BTC-USD' ORDER BY id DESC LIMIT 1")
    assert "oi +" in latest[0]["note"]                   # OI up 20% with price flat-up


def test_a_failing_source_is_named_and_the_others_still_vote(db):
    notes = CryptoPulse(SignalBoard(db), get_json=_get(fail=("coingecko",)),
                        post_json=_post(100), clock=Clock()).run(_Market())
    assert any("FAILING" in n and "coingecko" in n for n in notes)
    assert db.query("SELECT COUNT(*) AS n FROM signals")[0]["n"] == 2


def test_the_pulse_is_off_unless_switched_on(ecosystem):
    assert ecosystem.crypto_pulse is None
