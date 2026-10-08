"""The operator's paper desk: orders from the chat, one tap, the normal review.

Synthetic prices only. What is pinned: an order is only ever written from an
explicit ORDER line, nothing trades without the tap, a confirmed order goes
through the same firm machinery as any other (and can be refused by it), and
the outcome comes back to the chat.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.trading import chat, desk


def test_orders_are_taken_out_of_a_reply_and_bad_ones_dropped():
    text, orders = desk.parse(
        "Done, here you go.\n"
        'ORDER: {"side": "buy", "symbol": "nvda", "dollars": 1000}\n'
        'ORDER: {"side": "sell", "symbol": "TSLA", "all": true}\n'
        'ORDER: {"side": "short", "symbol": "AAPL", "dollars": 5}\n'
        'ORDER: {"side": "buy", "symbol": "BTC", "quantity": 0.01}\n'
        'ORDER: {"side": "buy", "symbol": "SPY", "dollars": -3}\n'
        "ORDER: not json\n")
    assert text == "Done, here you go."
    assert [(o["side"], o["symbol"]) for o in orders] == [
        ("buy", "NVDA"), ("sell", "TSLA"), ("buy", "BTC-USD")]
    assert orders[0]["dollars"] == Decimal("1000") and orders[1]["sell_all"]


def test_a_reply_without_an_order_line_writes_no_order(db):
    q = chat.post(db, "should I buy NVDA?")
    chat.claim(db)
    chat.answer(db, q, "Maybe. Buying NVDA here is a bet on earnings. Want me to place one?")
    assert desk.orders(db) == []


def test_an_order_waits_for_the_tap_and_can_be_cancelled(db):
    q = chat.post(db, "buy $500 of NVDA")
    chat.claim(db)
    reply = chat.answer(db, q, 'On it.\nORDER: {"side": "buy", "symbol": "NVDA", "dollars": 500}')
    (o,) = desk.orders(db)
    assert o["message_id"] == reply and o["status"] == "proposed"
    assert "ORDER" not in chat.history(db)[-1]["text"]
    assert "confirmed" in desk.confirm(db, o["id"])
    assert "already confirmed" in desk.confirm(db, o["id"])
    assert "cancelled" in desk.cancel(db, o["id"])
    assert desk.orders(db)[0]["status"] == "cancelled"


class _Context:
    def __init__(self, universe, prices, held=None, as_of="2026-10-07T15:00:00+00:00"):
        self.universe = tuple(universe)
        self._prices = prices
        self._held = held or {}
        self.as_of = as_of

    def price(self, s):
        return self._prices.get(s)

    def quantity(self, s):
        return Decimal(str(self._held.get(s, 0)))


def _confirmed(db, line):
    q = chat.post(db, "trade")
    chat.claim(db)
    chat.answer(db, q, f"ok\nORDER: {line}")
    o = desk.orders(db)[0]
    desk.confirm(db, o["id"])
    return o["id"]


def test_the_bot_hands_over_only_confirmed_orders_whose_market_is_open(db):
    stock = _confirmed(db, '{"side": "buy", "symbol": "NVDA", "dollars": 1000}')
    # A Wednesday 15:00 UTC is 11:00 in New York: open.
    out = desk.take(db, _Context(["SPY", "NVDA"], {"NVDA": Decimal("100")}))
    assert out == [{"symbol": "NVDA", "side": "buy", "notional": Decimal("1000"),
                    "rationale": f"operator order #{stock}: buy $1,000.00 of NVDA"}]
    assert desk.orders(db)[0]["status"] == "sent"
    # A Saturday: a new stock order waits, and is not marked sent.
    later = _confirmed(db, '{"side": "buy", "symbol": "AAPL", "dollars": 100}')
    assert desk.take(db, _Context(["AAPL"], {"AAPL": Decimal("200")},
                                  as_of="2026-10-10T15:00:00+00:00")) == []
    row = db.query_one("SELECT status, result FROM operator_orders WHERE id = ?", (later,))
    assert row["status"] == "confirmed" and "market" in row["result"]


def test_selling_everything_sells_what_the_desk_holds(db):
    _confirmed(db, '{"side": "sell", "symbol": "BTC-USD", "all": true}')
    (order,) = desk.take(db, _Context(["BTC-USD"], {"BTC-USD": Decimal("60000")},
                                      held={"BTC-USD": "0.25"}))
    assert order["quantity"] == Decimal("0.25") and order["side"] == "sell"


def test_unconfirmed_orders_expire(db):
    q = chat.post(db, "x")
    chat.claim(db)
    chat.answer(db, q, 'ORDER: {"side": "buy", "symbol": "SPY", "dollars": 10}')
    db.execute("UPDATE operator_orders SET created_at = '2026-01-01T00:00:00Z'")
    desk.expire(db)
    assert desk.orders(db)[0]["status"] == "expired"


# =========================================================================
# end to end, through the real tick
# =========================================================================
@pytest.fixture
def village(db, tmp_path, notifier, monkeypatch):
    from src.config import Config
    from src.trading.config import DataConfig, TradingConfig
    from src.trading.ecosystem import Ecosystem

    monkeypatch.setenv("DATABASE_URL", db.url)          # the desk's bot reads it
    firms = tmp_path / "firms.yaml"
    firms.write_text(
        "firms:\n"
        f"  {desk.DESK}:\n"
        "    name: Desk\n"
        "    asset_class: Mixed\n"
        "    strategy: \"bot:bots/operator_desk.py\"\n"
        "    risk_limit: 0.5\n"
        "    capital_allocation: 20000\n"
        "    universe: [SPY]\n"
        "    analysts: [technical]\n"
        "    genome_locked: true\n"
        "  beta:\n"
        "    name: Beta\n"
        "    asset_class: Crypto\n"
        "    capital_allocation: 25000\n"
        "    universe: [BTC-USD]\n"
        "    analysts: [technical]\n")
    eco = Ecosystem(
        db,
        TradingConfig(firms_config=firms, audit_vault=tmp_path / "v", vendor_dir=tmp_path / "d",
                      data=DataConfig(source="synthetic", seed=12345, history_days=180)),
        Config(database_url=db.url, notification_log=tmp_path / "n.log"),
        notifier,
    )
    eco.init_firms()
    return eco


def test_nothing_trades_on_the_desk_without_the_tap(village, db):
    q = chat.post(db, "buy $1000 of bitcoin")
    chat.claim(db)
    chat.answer(db, q, 'ORDER: {"side": "buy", "symbol": "BTC-USD", "dollars": 1000}')
    village.simulate(3)
    d = village.store.get_firm(desk.DESK)
    assert village.store.positions(d.id) == []


def test_a_confirmed_order_is_filled_through_the_normal_review(village, db):
    oid = _confirmed(db, '{"side": "buy", "symbol": "BTC-USD", "dollars": 1000}')
    village.simulate(4)
    d = village.store.get_firm(desk.DESK)
    assert "BTC-USD" in d.universe
    held = {p.symbol: p for p in village.store.positions(d.id)}
    assert "BTC-USD" in held and held["BTC-USD"].quantity > 0
    row = db.query_one("SELECT status, result FROM operator_orders WHERE id = ?", (oid,))
    assert row["status"] == "filled" and row["result"].startswith("filled: buy")
    proposal = db.query_one("SELECT * FROM trade_proposals WHERE firm_id = ?", (d.id,))
    assert proposal["risk_verdict"] in ("allow", "resize")     # it met the risk manager
    # The other firm never saw the chat.
    beta = village.store.get_firm("beta")
    assert not db.query("SELECT * FROM trade_proposals WHERE firm_id = ? AND rationale "
                        "LIKE 'operator order%'", (beta.id,))


def test_an_order_the_risk_manager_refuses_comes_back_refused(village, db):
    oid = _confirmed(db, '{"side": "sell", "symbol": "BTC-USD", "quantity": 1}')
    village.simulate(4)
    row = db.query_one("SELECT status, result FROM operator_orders WHERE id = ?", (oid,))
    assert row["status"] == "blocked" and "not traded" in row["result"]
