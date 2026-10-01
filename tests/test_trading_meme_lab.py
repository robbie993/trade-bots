"""The Pump.fun lab — every trending meme launch bought on paper, and scored.

The properties that make its numbers worth reading:

    1. A token is bought once, the first time it shows up, and only in a pool
       deep enough to get in and out of. A token skipped once is never bought
       later, once it has proven itself.
    2. Nothing is backfilled: a token the radar saw hours ago is not bought
       at today's price after a restart.
    3. A pool that disappears is a total loss; an outage is not.
    4. It never touches the network in a test, and never the ledger at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.db.connection import to_iso
from src.trading.sandbox import SandboxViolation, WRITABLE_TABLES, sandbox_handles
from src.trading.sandbox import memes
from src.trading.sandbox.memes import (
    DexPrices, MemeLab, Quote, after_costs, deepest, looks, scoreboard, token_from,
)

NOW = datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc)
MINT = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAApump"
OTHER = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBpump"


def pair(address, price, liquidity, mcap=None, quote_side=False):
    token = {"address": address, "symbol": "X"}
    sol = {"address": "So11111111111111111111111111111111111111112", "symbol": "SOL"}
    return {"chainId": "solana", "dexId": "pumpswap",
            "baseToken": sol if quote_side else token,
            "quoteToken": token if quote_side else sol,
            "priceUsd": str(price), "liquidity": {"usd": liquidity},
            "marketCap": mcap}


class Dex:
    """DexScreener without a network: a price book, and a record of what was asked."""

    def __init__(self, book=None, fail=False):
        self.book = dict(book or {})          # address -> list of pairs
        self.fail = fail
        self.asked = []

    def __call__(self, url, params, headers=None):
        self.asked.append(url)
        if self.fail:
            raise RuntimeError("dexscreener returned 429: slow down")
        addresses = url.rsplit("/", 1)[1].split(",")
        return [p for a in addresses for p in self.book.get(a, [])]


def seen(db, item_key, source="pumpfun_live", at=NOW, title="Coin ($COIN)"):
    db.insert("intel", {"source": source, "item_key": item_key, "title": title,
                        "url": "", "symbols": "", "score": None, "detail": "{}",
                        "first_seen": to_iso(at), "last_seen": to_iso(at)})


@pytest.fixture
def dex():
    return Dex({MINT: [pair(MINT, "0.001", 20000, 1000000)]})


@pytest.fixture
def lab(store, dex):
    _, writer = sandbox_handles(store)
    return MemeLab(writer, DexPrices(get_json=dex))


def rows(db, **where):
    out = db.query("SELECT * FROM meme_lab ORDER BY id")
    return [r for r in out if all(str(r[k]) == str(v) for k, v in where.items())]


# =========================================================================
# buying
# =========================================================================
def test_a_new_token_opens_one_idea_per_horizon(lab, db):
    seen(db, MINT)
    report = lab.run(NOW)
    ideas = rows(db, status="open")
    assert [r["horizon"] for r in ideas] == ["1h", "1d", "1w"]
    assert {r["token_key"] for r in ideas} == {f"solana:{MINT}"}
    assert [r["due_at"] for r in ideas] == [
        to_iso(NOW + timedelta(hours=1)), to_iso(NOW + timedelta(days=1)),
        to_iso(NOW + timedelta(days=7))]
    assert Decimal(str(ideas[0]["entry_price"])) == Decimal("0.001")
    assert report.bought == 1
    assert report.lines() == ["Pump.fun lab: bought 1 new token(s) on paper"]


def test_running_again_buys_nothing_twice(lab, db):
    seen(db, MINT)
    lab.run(NOW)
    lab.run(NOW + timedelta(minutes=15))
    assert len(rows(db)) == 3


def test_a_thin_pool_is_skipped_and_never_bought_later(lab, db, dex):
    dex.book[MINT] = [pair(MINT, "0.001", 4999)]
    seen(db, MINT)
    report = lab.run(NOW)
    assert [r["status"] for r in rows(db)] == ["thin"] and report.thin == 1
    # It grows a deep pool a bar later: buying it now would credit "buy every
    # launch" with a launch that had already proven itself.
    dex.book[MINT] = [pair(MINT, "0.004", 90000)]
    lab.run(NOW + timedelta(minutes=15))
    assert [r["status"] for r in rows(db)] == ["thin"]


def test_a_token_with_no_pool_is_recorded_as_such(lab, db):
    seen(db, OTHER)
    report = lab.run(NOW)
    assert [(r["status"], r["horizon"]) for r in rows(db)] == [("unlisted", "")]
    assert "skipped 1 with no pool" in report.lines()[0]


def test_a_token_seen_hours_ago_is_not_bought_at_todays_price(lab, db, dex):
    seen(db, MINT, at=NOW - timedelta(hours=2, minutes=1))
    report = lab.run(NOW)
    assert rows(db) == [] and dex.asked == [] and report.lines() == []


def test_a_failed_request_is_asked_again_inside_the_window(store, db):
    seen(db, MINT)
    _, writer = sandbox_handles(store)
    down = Dex({MINT: [pair(MINT, "0.001", 20000)]}, fail=True)
    lab = MemeLab(writer, DexPrices(get_json=down))
    report = lab.run(NOW)
    assert rows(db) == []
    assert "DexScreener not reached" in report.lines()[0]
    down.fail = False
    lab.run(NOW + timedelta(minutes=15))
    assert len(rows(db, status="open")) == 3


def test_each_list_is_its_own_bet(lab, db):
    seen(db, MINT, source="pumpfun_live")
    seen(db, f"solana:{MINT}", source="dexscreener_boosts")
    lab.run(NOW)
    assert {r["source"] for r in rows(db)} == {"pumpfun_live", "dexscreener_boosts"}
    assert len(rows(db)) == 6


# =========================================================================
# closing
# =========================================================================
def test_a_due_idea_closes_at_the_price_then(lab, db, dex):
    seen(db, MINT)
    lab.run(NOW)
    dex.book[MINT] = [pair(MINT, "0.0015", 30000)]
    report = lab.run(NOW + timedelta(hours=1))
    closed = rows(db, status="closed")
    assert [r["horizon"] for r in closed] == ["1h"]
    assert Decimal(str(closed[0]["return_pct"])) == Decimal("50")
    assert report.closed == 1
    assert len(rows(db, status="open")) == 2


def test_a_vanished_pool_is_a_total_loss_only_after_a_day(lab, db, dex):
    seen(db, MINT)
    lab.run(NOW)
    dex.book[MINT] = []
    due = NOW + timedelta(hours=1)
    lab.run(due + timedelta(hours=23))
    assert rows(db, status="vanished") == [], "a day's grace first"
    report = lab.run(due + timedelta(days=1, minutes=1))
    gone = rows(db, status="vanished")
    assert [r["horizon"] for r in gone] == ["1h"]
    assert Decimal(str(gone[0]["return_pct"])) == Decimal("-100")
    assert "1 of them vanished" in report.lines()[0]


def test_an_outage_voids_an_idea_rather_than_calling_it_a_loss(store, db):
    seen(db, MINT)
    _, writer = sandbox_handles(store)
    dex = Dex({MINT: [pair(MINT, "0.001", 20000)]})
    lab = MemeLab(writer, DexPrices(get_json=dex))
    lab.run(NOW)
    dex.fail = True
    lab.run(NOW + timedelta(hours=1, days=1, minutes=1))
    assert [r["horizon"] for r in rows(db, status="void")] == ["1h"]
    assert rows(db, status="vanished") == []


# =========================================================================
# pricing
# =========================================================================
def test_the_deepest_pool_trading_the_token_as_its_base_sets_the_price():
    pairs = [pair(MINT, "0.001", 6000), pair(MINT, "0.002", 60000),
             pair(MINT, "999", 10 ** 9, quote_side=True)]
    assert deepest(pairs, MINT) == Quote(Decimal("0.002"), Decimal("60000"), None)
    assert deepest(pairs, MINT.lower()) is not None, "addresses match without case"
    assert deepest(pairs, OTHER) is None


def test_requests_are_batched_by_chain_and_stop_at_the_first_failure():
    dex = Dex({f"a{i}": [pair(f"a{i}", "1", 10000)] for i in range(12)})
    prices = DexPrices(get_json=dex, per_call=5)
    out = prices.fetch([("solana", f"a{i}") for i in range(12)] + [("base", "0xabc")])
    assert len(dex.asked) == 4                  # 5 + 5 + 2 on solana, 1 on base
    assert out["base:0xabc"] is None and out["solana:a11"].price == Decimal("1")
    down = Dex(fail=True)
    prices = DexPrices(get_json=down, per_call=5)
    assert prices.fetch([("solana", f"a{i}") for i in range(12)]) == {}
    assert len(down.asked) == 1 and "429" in prices.last_error


def test_a_response_that_may_be_cut_short_proves_no_token_missing():
    crowded = [pair("big", "1", 10 ** 6) for _ in range(memes.PAIRS_CAP)]
    dex = Dex({"big": crowded})
    out = DexPrices(get_json=dex).fetch([("solana", "big"), ("solana", "small")])
    assert out["solana:big"] is not None
    assert "solana:small" not in out, "asked again later, not recorded as having no pool"


def test_an_answer_that_is_not_a_list_records_nothing():
    prices = DexPrices(get_json=lambda url, params, headers=None: {"error": "busy"})
    assert prices.fetch([("solana", MINT)]) == {}
    assert "not a list" in prices.last_error


def test_a_slow_site_gets_a_budget_not_the_whole_tick(monkeypatch):
    import time

    clock = iter([0.0, 0.0])                    # the start, then the first request
    monkeypatch.setattr(time, "monotonic", lambda: next(clock, 30.0))
    dex = Dex()
    DexPrices(get_json=dex, per_call=1).fetch([("solana", "a"), ("solana", "b")])
    assert len(dex.asked) == 1


def test_tokens_are_read_from_each_list():
    assert token_from({"source": "pumpfun_top", "item_key": MINT}).key == f"solana:{MINT}"
    sui = token_from({"source": "dexscreener_boosts", "item_key": "sui:0x2::coin::COIN"})
    assert (sui.chain, sui.address) == ("sui", "0x2::coin::COIN")
    assert token_from({"source": "dexscreener_boosts", "item_key": "None:None"}) is None


# =========================================================================
# the scoreboard
# =========================================================================
def test_costs_are_taken_both_ways_and_nothing_loses_more_than_everything():
    assert after_costs(0) == Decimal("-1.9802")
    assert after_costs(100) == Decimal("96.0396")
    assert after_costs(-100) == Decimal("-100.0000")


def test_the_scoreboard_leads_with_the_median(db):
    for ret, status in ((Decimal("200"), "closed"), (Decimal("-10"), "closed"),
                        (Decimal("-60"), "closed"), (Decimal("-100"), "vanished")):
        db.insert("meme_lab", {"source": "pumpfun_live", "token_key": "solana:x",
                               "chain": "solana", "address": "x", "horizon": "1d",
                               "opened_at": to_iso(NOW), "due_at": to_iso(NOW),
                               "entry_price": Decimal("1"), "return_pct": ret,
                               "status": status})
    [record] = scoreboard(db)
    assert (record.closed, record.up, record.halved, record.vanished) == (4, 1, 2, 1)
    assert record.mean_pct == Decimal("7.5000")
    assert record.median_pct == Decimal("-35.0000")
    assert record.after_costs_pct == after_costs(Decimal("7.5"))
    assert record.label == "Pump.fun live now"


def test_what_showed_up_counts_tokens_not_ideas(lab, db, dex):
    dex.book[OTHER] = [pair(OTHER, "0.001", 100)]
    seen(db, MINT)
    seen(db, OTHER)
    seen(db, "MintCCCC")
    lab.run(NOW)
    assert looks(db) == {"pumpfun_live": {"looked": 3, "bought": 1, "thin": 1, "unlisted": 1}}
    text = memes.render(db)
    assert "looked at 3, bought 1, 1 too thin, 1 with no pool" in text


# =========================================================================
# the bar, the guard, the village
# =========================================================================
def test_it_runs_once_a_bar_and_never_on_a_bar_that_stepped_back(lab, db, dex):
    seen(db, MINT)
    lab.tick("2026-10-01T19:00", NOW)
    assert len(dex.asked) == 1
    lab.tick("2026-10-01T19:00", NOW + timedelta(minutes=1))
    lab.tick("2026-10-01T18:45", NOW + timedelta(minutes=2))
    assert len(dex.asked) == 1
    seen(db, OTHER, at=NOW + timedelta(minutes=15))
    dex.book[OTHER] = [pair(OTHER, "0.001", 20000)]
    lab.tick("2026-10-01T19:15", NOW + timedelta(minutes=15))
    assert len(dex.asked) == 2
    # Bars longer than the lab's patience: it still runs.
    lab.tick("2026-10-01T19:15", NOW + timedelta(minutes=15) + memes.IDLE)
    assert len(dex.asked) == 2, "nothing due and nothing new asks nothing"
    assert lab._ran_at == NOW + timedelta(minutes=15) + memes.IDLE


def test_the_lab_writes_its_own_table_and_nothing_else(lab):
    assert "meme_lab" in WRITABLE_TABLES
    with pytest.raises(SandboxViolation):
        lab.writer.insert("fills", {"firm_id": 1})


def test_it_is_off_unless_the_meme_radar_is_on(ecosystem, monkeypatch):
    assert ecosystem.meme_lab is None
    monkeypatch.setenv("TRADE_MEME_RADAR_ENABLED", "1")
    monkeypatch.setenv("TRADE_MEME_LAB", "off")
    assert ecosystem.meme_lab is None
    monkeypatch.setenv("TRADE_MEME_LAB", "on")
    assert isinstance(ecosystem.meme_lab, MemeLab)


def test_a_tick_runs_the_lab(ecosystem, monkeypatch, dex):
    monkeypatch.setattr(memes, "enabled", lambda: True)   # the lab, not the radar
    _, writer = sandbox_handles(ecosystem.store)
    ecosystem._meme_lab = MemeLab(writer, DexPrices(get_json=dex))
    seen(ecosystem.db, MINT, at=datetime.now(timezone.utc))
    report = ecosystem.tick()
    assert "Pump.fun lab: bought 1 new token(s) on paper" in report.bot_notes
    assert len(ecosystem.db.query("SELECT * FROM meme_lab")) == 3


def test_the_panel_is_hidden_while_off_and_honest_when_on(ecosystem, monkeypatch):
    from src.trading.web import _meme_lab_panel

    assert _meme_lab_panel(ecosystem) == ""
    monkeypatch.setattr(memes, "enabled", lambda: True)
    assert "Nothing yet" in _meme_lab_panel(ecosystem)
    ecosystem.db.insert("meme_lab", {
        "source": "pumpfun_live", "token_key": "solana:x", "chain": "solana",
        "address": "x", "horizon": "1h", "opened_at": to_iso(NOW), "due_at": to_iso(NOW),
        "entry_price": Decimal("1"), "return_pct": Decimal("-60"), "status": "closed"})
    html = _meme_lab_panel(ecosystem)
    assert "Pump.fun lab: what buying every trending launch would have done" in html
    assert "Held for an hour" in html and "-60.0%" in html
    assert "guess, not a measurement" in html
