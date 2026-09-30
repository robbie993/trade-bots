"""The idea lab — every scanner call traded on paper in the sandbox, and scored.

The properties that make its numbers worth reading:

    1. It cannot reach the ledger. It is built on the sandbox writer, which
       refuses every table but the sandbox's own.
    2. A call is entered where a firm could have entered it: an open market
       and a fresh price, paying the session's real costs both ways.
    3. A call repeated every bar is one idea per horizon, and a scanner that
       turns against its own call closes it at once.
    4. A short record gets no verdict, and the bar for one rises with the
       number of scanners and horizons being looked at.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.trading.sandbox import SandboxViolation, sandbox_handles
from src.trading.sandbox import ideas
from src.trading.sandbox.ideas import (
    BarPrices, Call, CostBook, IdeaLab, LatestPrices, Record, grade, scoreboard,
)
from src.trading.signals import Reading, ScannerSpec, Scanners, SignalBoard, stamp

#: Wednesday 30 September 2026, 10:00 in New York: regular hours.
OPEN = datetime(2026, 9, 30, 14, 0, tzinfo=timezone.utc)
#: The same day at 02:00 in New York: every equity venue is shut.
NIGHT = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)


class Prices:
    """A bar's prices without a market behind them."""

    def __init__(self, prices: dict, spy=None, why: str = "no price"):
        self.prices = {k: Decimal(str(v)) for k, v in prices.items()}
        self.spy = None if spy is None else Decimal(str(spy))
        self.why = why

    def __call__(self, symbol):
        price = self.prices.get(symbol)
        return (price, "") if price is not None else (None, self.why)

    def benchmark(self):
        return self.spy


def buy(symbol="AMD", publisher="scan", score=60, outside=False):
    return Call(publisher, symbol, Decimal(str(score)), Decimal("50"), "because", outside)


def sell(symbol="AMD", publisher="scan"):
    return buy(symbol, publisher, score=-60)


@pytest.fixture
def lab(store):
    _, writer = sandbox_handles(store)
    # A flat 10 bps a side, so the arithmetic below can be done by hand.
    flat = CostBook()
    flat.side_bps = lambda symbol, when: Decimal("10")
    return IdeaLab(writer, costs=flat)


def rows(db, **where):
    sql = "SELECT * FROM sandbox_ideas"
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = ?" for k in where)
    return db.query(sql + " ORDER BY id", tuple(where.values()))


# =========================================================================
# it cannot reach the money
# =========================================================================
def test_the_lab_writes_its_own_table_and_nothing_else(store):
    _, writer = sandbox_handles(store)
    writer.insert("sandbox_ideas", {
        "publisher": "scan", "symbol": "AMD", "side": "buy", "horizon": "1h",
        "opened_bar": "b", "due_at": "2026-01-01T00:00:00Z", "entry_price": "1"})
    for table in ("fills", "positions", "firms", "cash_flow"):
        with pytest.raises(SandboxViolation):
            writer.insert(table, {"x": 1})


def test_testing_calls_leaves_the_ledger_exactly_as_it_was(store, firm_record, lab):
    before = [(f.firm_key, f.cash, f.allocation) for f in store.firms()]
    lab.step(OPEN, [buy("SPY"), sell("QQQ")], Prices({"SPY": 500, "QQQ": 400}, spy=500))
    lab.step(OPEN + timedelta(hours=2), [], Prices({"SPY": 505, "QQQ": 390}, spy=505))
    assert [(f.firm_key, f.cash, f.allocation) for f in store.firms()] == before
    assert store.fills(firm_record.id) == []
    assert store.positions(firm_record.id) == []


# =========================================================================
# what an idea is
# =========================================================================
def test_a_call_opens_one_idea_per_horizon_at_the_bars_price(db, lab):
    report = lab.step(OPEN, [buy()], Prices({"AMD": 100}, spy=500))
    assert report.opened == 3
    opened = rows(db)
    assert [r["horizon"] for r in opened] == ["1h", "1d", "1w"]
    assert {Decimal(str(r["entry_price"])) for r in opened} == {Decimal("100")}
    assert {r["side"] for r in opened} == {"buy"}
    assert {r["due_at"] for r in opened} == {
        "2026-09-30T15:00:00Z", "2026-10-01T14:00:00Z", "2026-10-07T14:00:00Z"}


def test_a_call_repeated_every_bar_is_one_idea_not_one_a_bar(db, lab):
    """The fleet scanner says AMD +60 on every bar it likes AMD. That is one
    call, held until each horizon closes, not ninety-six a day."""
    for minutes in (0, 15, 30, 45):
        lab.step(OPEN + timedelta(minutes=minutes), [buy()], Prices({"AMD": 100}))
    assert len(rows(db)) == 3


def test_an_idea_closes_at_its_horizon_net_of_both_sides_costs(db, lab):
    lab.step(OPEN, [buy()], Prices({"AMD": 100}, spy=500))
    report = lab.step(OPEN + timedelta(hours=1), [], Prices({"AMD": 102}, spy=501))
    assert report.closed == 1
    (hour,) = rows(db, horizon="1h")
    # +2.00% on the price, less 10 bps in and 10 bps out.
    assert Decimal(str(hour["return_pct"])) == Decimal("1.8")
    assert Decimal(str(hour["spy_pct"])) == Decimal("0.2")
    assert Decimal(str(hour["pnl"])) == Decimal("18")        # on $1,000
    assert hour["closed_why"] == "held to horizon"
    # The day and the week are still open.
    assert [r["horizon"] for r in rows(db) if r["closed_bar"] is None] == ["1d", "1w"]


def test_a_sell_call_makes_money_when_the_price_falls(db, lab):
    lab.step(OPEN, [sell()], Prices({"AMD": 100}, spy=500))
    lab.step(OPEN + timedelta(hours=1), [], Prices({"AMD": 98}, spy=500))
    (hour,) = rows(db, horizon="1h")
    assert Decimal(str(hour["return_pct"])) == Decimal("1.8")


def test_a_call_right_by_less_than_its_costs_lost_money(db, lab):
    lab.step(OPEN, [buy()], Prices({"AMD": 100}))
    lab.step(OPEN + timedelta(hours=1), [], Prices({"AMD": "100.15"}))
    (hour,) = rows(db, horizon="1h")
    assert Decimal(str(hour["return_pct"])) < 0


def test_a_call_still_made_when_its_idea_closes_opens_the_next_one(db, lab):
    lab.step(OPEN, [buy()], Prices({"AMD": 100}))
    lab.step(OPEN + timedelta(hours=1), [buy()], Prices({"AMD": 101}))
    hours = rows(db, horizon="1h")
    assert len(hours) == 2
    assert hours[0]["closed_bar"] is not None and hours[1]["closed_bar"] is None
    assert Decimal(str(hours[1]["entry_price"])) == Decimal("101")


def test_a_scanner_that_turns_on_its_call_closes_it_and_takes_the_other_side(db, lab):
    lab.step(OPEN, [buy()], Prices({"AMD": 100}))
    report = lab.step(OPEN + timedelta(minutes=30), [sell()], Prices({"AMD": 99}))
    assert report.closed == 3 and report.flipped == 3
    closed = [r for r in rows(db) if r["closed_bar"] is not None]
    assert {r["closed_why"] for r in closed} == {"the scanner flipped"}
    assert {r["side"] for r in rows(db) if r["closed_bar"] is None} == {"sell"}
    assert "because the scanner flipped" in report.lines()[-1]


def test_an_idea_is_never_settled_on_a_bar_before_it_opened(db, lab):
    """The village's clock can step back a bar (`Ecosystem._high_bar`). A flip
    read on the earlier bar must not close an idea opened on the later one."""
    lab.step(OPEN + timedelta(minutes=15), [buy()], Prices({"AMD": 100}))
    report = lab.step(OPEN, [sell()], Prices({"AMD": 99}))
    assert report.closed == 0
    assert {r["side"] for r in rows(db)} == {"buy"}


def test_two_scanners_calling_one_symbol_are_two_ideas(db, lab):
    lab.step(OPEN, [buy(publisher="news"), sell(publisher="scribe")], Prices({"AMD": 100}))
    assert {(r["publisher"], r["side"]) for r in rows(db)} == {("news", "buy"), ("scribe", "sell")}


def test_silence_is_not_a_call(db, store, lab):
    board = SignalBoard(db)
    board.publish("scan", [Reading("AMD", Decimal("0"), Decimal("50")),
                           Reading("NVDA", Decimal("40"), Decimal("0")),
                           Reading("META", Decimal("47"), Decimal("40"))], OPEN)
    board.mark_silent("quiet", OPEN)
    assert [(c.publisher, c.symbol) for c in lab.calls_on(stamp(OPEN))] == [("scan", "META")]


def test_the_newest_reading_on_a_bar_is_the_call(db, lab):
    board = SignalBoard(db)
    board.publish("scan", [Reading("AMD", Decimal("40"), Decimal("50"))], OPEN)
    board.publish("scan", [Reading("AMD", Decimal("-40"), Decimal("50"))], OPEN)
    (call,) = lab.calls_on(stamp(OPEN))
    assert call.side == "sell"


def test_a_reading_from_another_bar_is_not_this_bars_call(db, lab):
    SignalBoard(db).publish("scan", [Reading("AMD", Decimal("40"), Decimal("50"))], OPEN)
    assert lab.calls_on(stamp(OPEN + timedelta(minutes=15))) == []


# =========================================================================
# where a firm could have traded it
# =========================================================================
def test_a_stock_called_while_its_market_is_shut_waits(market, lab, db):
    prices = BarPrices(market, NIGHT)
    assert prices("SPY") == (None, "its market is shut")
    report = lab.step(NIGHT, [buy("SPY")], prices)
    assert report.opened == 0
    assert report.waiting == {"SPY": "its market is shut"}
    assert rows(db) == []


def test_crypto_never_closes(market):
    price, why = BarPrices(market, NIGHT, session_aware=True,
                           fresh=timedelta(days=36500))("BTC-USD")
    assert price is not None and why == ""


def test_a_price_older_than_an_hour_is_not_traded(market):
    newest = market.bar("BTC-USD").as_of
    price, why = BarPrices(market, newest + timedelta(hours=2), session_aware=False)("BTC-USD")
    assert price is None and "2.0h old" in why


def test_a_symbol_the_feed_cannot_price_is_not_traded(market):
    market.unpriceable["SPY"] = "no bars"
    newest = market.bar("SPY").as_of
    assert BarPrices(market, newest, session_aware=False)("SPY")[0] is None


def test_spy_is_the_yardstick_even_when_it_is_not_trading(market):
    """A yardstick that did not trade did not move: SPY's last price stands."""
    assert BarPrices(market, NIGHT).benchmark() == market.mark("SPY")


def test_a_name_outside_the_village_needs_an_outside_price(market):
    newest = market.bar("SPY").as_of
    prices = BarPrices(market, newest, session_aware=False)
    assert prices("ZZZZ")[0] is None
    priced = BarPrices(market, newest, {"ZZZZ": (Decimal("3.10"), newest)},
                       session_aware=False)
    assert priced("ZZZZ") == (Decimal("3.10"), "")


def test_an_idea_due_on_a_shut_market_waits_for_it_to_open(db, lab):
    lab.step(OPEN, [buy()], Prices({"AMD": 100}))
    report = lab.step(OPEN + timedelta(days=1, hours=1), [], Prices({}, why="its market is shut"))
    assert report.closed == 0 and report.waiting == {"AMD": "its market is shut"}
    lab.step(OPEN + timedelta(days=1, hours=2), [], Prices({"AMD": 101}))
    assert all(r["closed_bar"] is not None for r in rows(db, horizon="1d"))


def test_an_idea_that_never_gets_a_price_is_written_off_and_not_scored(db, lab):
    lab.step(OPEN, [buy()], Prices({"AMD": 100}))
    report = lab.step(OPEN + timedelta(days=6), [], Prices({}, why="delisted"))
    assert report.voided == 2                       # the hour and the day
    voided = [r for r in rows(db) if r["closed_why"] and r["closed_why"].startswith("written off")]
    assert len(voided) == 2 and all(r["return_pct"] is None for r in voided)
    assert scoreboard(db) == []


# =========================================================================
# what crossing costs
# =========================================================================
class _Spec:
    def __init__(self, universe, fee=None, spread=None):
        self.universe, self.fee_bps, self.slippage_bps = universe, fee, spread

    @property
    def costs_overridden(self):
        return self.fee_bps is not None or self.slippage_bps is not None


def test_crypto_pays_the_fee_and_the_widest_measured_spread():
    book = CostBook([_Spec(["BTC-USD", "SOL-USD"], fee=25, spread="5.3"),
                     _Spec(["SOL-USD"], fee=25, spread="7.0")])
    assert book.side_bps("BTC-USD", OPEN) == Decimal("30.3")
    assert book.side_bps("SOL-USD", OPEN) == Decimal("32.0")


def test_a_coin_no_desk_trades_is_priced_like_the_thinnest_book():
    assert CostBook().side_bps("AAVE-USD", OPEN) == Decimal("25") + Decimal("19.7")


def test_a_stock_pays_its_sessions_spread():
    book = CostBook(fee_bps=2)
    after_hours = datetime(2026, 9, 30, 21, 0, tzinfo=timezone.utc)     # 17:00 ET
    assert book.side_bps("AMD", OPEN) == Decimal("2.82")
    assert book.side_bps("AMD", after_hours) == Decimal("7.14")
    assert CostBook(fee_bps=2, session_aware=False).side_bps("AMD", after_hours) == Decimal("2.82")


# =========================================================================
# the score
# =========================================================================
def _record(t, closed=40, horizon="1d", name="scan"):
    return Record(name, horizon, False, closed, closed // 2, Decimal("0"), Decimal("0"),
                  Decimal("0"), Decimal("0"), t)


def test_a_short_record_gets_no_verdict():
    (r,) = grade([_record(t=9.0, closed=19)])
    assert r.verdict == "too few to tell"


def test_a_scanner_calling_spy_itself_trails_it_by_exactly_its_costs(db, lab):
    """Buying SPY against SPY has no luck in it to test: every idea trails by
    its own costs, so the verdict is the sign, not a t-test on zero spread."""
    for i in range(20):
        start = OPEN + timedelta(hours=2 * i)
        lab.step(start, [buy("SPY")], Prices({"SPY": 500}, spy=500))
        move = 500 + i
        lab.step(start + timedelta(hours=1), [], Prices({"SPY": move}, spy=move))
    hour = [r for r in scoreboard(db) if r.horizon == "1h"][0]
    assert hour.t is None and hour.edge_pct == Decimal("-0.2")
    assert hour.verdict == "trails SPY"


def test_a_clear_edge_over_spy_is_called_one_and_a_clear_deficit_too():
    beats, trails, flat = grade([_record(2.5, name="a"), _record(-2.5, name="b"),
                                 _record(0.4, name="c")], min_closed=20)[0:3]
    verdicts = {r.publisher: r.verdict for r in (beats, trails, flat)}
    # Three looks: the bar is 2.39, and 2.5 clears it both ways.
    assert verdicts == {"a": "beats SPY", "b": "trails SPY", "c": "no edge yet"}


def test_the_bar_rises_with_every_scanner_on_the_board():
    """t = 2.5 is an edge when it is the only thing looked at, and luck when it
    is the best of thirty."""
    (alone,) = grade([_record(2.5)])
    assert alone.verdict == "beats SPY"
    crowd = grade([_record(2.5, name="lucky")] + [_record(0.0, name=f"s{i}") for i in range(29)])
    assert {r.publisher: r.verdict for r in crowd}["lucky"] == "no edge yet"


def test_the_scoreboard_adds_up_what_closed(db, lab):
    for i in range(25):
        start = OPEN + timedelta(hours=2 * i)
        lab.step(start, [buy()], Prices({"AMD": 100}, spy=500))
        # Up 1% every time while SPY is flat, give or take a little.
        wiggle = Decimal("0.1") * (i % 3)
        lab.step(start + timedelta(hours=1), [],
                 Prices({"AMD": Decimal("101") + wiggle}, spy=500))
    board = {(r.publisher, r.horizon): r for r in scoreboard(db)}
    hour = board[("scan", "1h")]
    assert hour.closed == 25 and hour.won == 25
    assert hour.pnl == sum((Decimal(str(r["pnl"])) for r in rows(db, horizon="1h")
                            if r["pnl"] is not None), Decimal("0"))
    assert hour.spy_pct == 0
    assert hour.verdict == "beats SPY"
    assert ("scan", "1d") in board          # the days closed too, and are too few


def test_the_text_scoreboard_says_when_nothing_has_closed(db):
    assert "no idea has closed yet" in ideas.render(db)


# =========================================================================
# calls on names the village does not trade
# =========================================================================
def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return path


def test_the_scanners_keep_what_they_called_outside_the_universe(market, tmp_path, db):
    path = _write(tmp_path, "s.py",
                  "def scan(context):\n    return {'SPY': 30, 'ZZZZ': 40, 'AAVE-USD': -20}\n")
    board = SignalBoard(db)
    desk = Scanners(board, [ScannerSpec("form4", path)])
    notes = desk.run(market, OPEN)
    # The board and the log are exactly what they were: the names are dropped.
    assert board.reading("ZZZZ", OPEN) is None
    assert any("2 symbol(s) outside the village's universe, dropped" in n for n in notes)
    # And the lab can have them.
    ((name, readings),) = desk.take_outside()
    assert name == "form4"
    assert {(r.symbol, r.score) for r in readings} == {
        ("ZZZZ", Decimal("40.00")), ("AAVE-USD", Decimal("-20.00"))}
    assert desk.take_outside() == []            # handed over once


def test_outside_calls_are_tested_and_marked(db, lab):
    calls = ideas.calls_from("form4", [Reading("ZZZZ", Decimal("40"), Decimal("30"))],
                             outside=True)
    lab.step(OPEN, calls, Prices({"ZZZZ": "3.10"}))
    assert {int(r["outside"]) for r in rows(db)} == {1}
    lab.step(OPEN + timedelta(hours=1), [], Prices({"ZZZZ": "3.41"}))
    (record,) = [r for r in scoreboard(db) if r.horizon == "1h"]
    assert record.outside and record.label == "form4 (outside the village)"


def test_outside_a_scanners_universe_but_inside_the_villages_is_priced_by_the_village(
        market, store):
    """A scanner limited to three ETFs can still name a stock a desk trades.
    That call is priced off the village's own bars and is not marked outside."""
    asked = []

    class Source(LatestPrices):
        def fetch(self, symbols, bar):
            asked.append(set(symbols))
            return {}

    _, writer = sandbox_handles(store)
    lab = IdeaLab(writer, outside=Source(), session_aware=False,
                  fresh=timedelta(days=36500))
    call = Call("social", "QQQ", Decimal("40"), Decimal("30"), "", outside=True)
    report = lab.run(market, [call])
    assert report.opened == 3
    assert {int(r["outside"]) for r in rows(store.db)} == {0}
    assert asked == []


def test_outside_prices_are_asked_for_in_one_batch_per_bar():
    asked = []

    def getter(url, params, timeout):
        asked.append((url, params["symbols"]))
        if "crypto" in url:
            return {"bars": {"AAVE/USD": {"c": 250.5, "t": "2026-09-30T14:14:00Z"}}}
        return {"bars": {"ZZZZ": {"c": 3.1, "t": "2026-09-30T14:14:00Z"}}}

    source = LatestPrices(getter=getter)
    got = source.fetch(["ZZZZ", "AAVE-USD", "NOPE"], "bar-1")
    assert got["ZZZZ"][0] == Decimal("3.1") and got["AAVE-USD"][0] == Decimal("250.5")
    assert "NOPE" not in got
    assert sorted(u.rsplit("/", 1)[-1] for u, _ in asked) == ["bars", "latest"]
    assert ("NOPE,ZZZZ" in [s for _, s in asked])
    # The same bar asks for nothing again, a miss included.
    source.fetch(["ZZZZ", "NOPE"], "bar-1")
    assert len(asked) == 2
    # A new bar asks again.
    source.fetch(["ZZZZ"], "bar-2")
    assert len(asked) == 3


def test_a_malformed_name_is_never_sent_so_it_cannot_sink_the_batch():
    """Alpaca refuses a whole symbol list over one bad name, and a scanner is a
    file anybody can drop in."""
    asked = []

    def getter(url, params, timeout):
        asked.append(params["symbols"])
        return {"bars": {"ZZZZ": {"c": 3.1, "t": "2026-09-30T14:14:00Z"}}}

    source = LatestPrices(getter=getter)
    got = source.fetch(["ZZZZ", "BRK.B", "NOT A TICKER", "A,B", "$ZZZZ", "DOGE/USD"], "bar-1")
    assert asked == ["BRK.B,ZZZZ"]
    assert list(got) == ["ZZZZ"]
    # Refused names count as asked: the rest of the bar sends nothing.
    source.fetch(["NOT A TICKER", "A,B"], "bar-1")
    assert len(asked) == 1


def test_an_outside_price_that_fails_is_a_note_not_an_exception():
    def getter(url, params, timeout):
        raise RuntimeError("alpaca is down")

    source = LatestPrices(getter=getter)
    assert source.fetch(["ZZZZ"], "bar") == {}
    assert "alpaca is down" in source.last_error


# =========================================================================
# in the village
# =========================================================================
def test_a_tick_tests_the_calls_its_scanners_make(ecosystem, tmp_path):
    path = _write(tmp_path, "s.py",
                  "def scan(context):\n    return {'BTC-USD': 55, 'ZZZZ': 40}\n")
    ecosystem.scanners.specs = [ScannerSpec("watcher", path)]
    report = ecosystem.tick()
    tested = ecosystem.db.query("SELECT * FROM sandbox_ideas")
    assert {(r["publisher"], r["symbol"], r["side"]) for r in tested} == {
        ("watcher", "BTC-USD", "buy")}
    assert len(tested) == 3
    assert any("idea lab: tested 3 new idea(s)" in n for n in report.bot_notes)
    # The next tick on the same bar is the same call, not a new one.
    ecosystem.tick()
    assert len(ecosystem.db.query("SELECT * FROM sandbox_ideas")) == 3


def test_a_synthetic_village_never_asks_alpaca_for_outside_prices(ecosystem):
    assert ecosystem.idea_lab.outside is None


def test_the_switch_on_the_wall_stops_the_lab(ecosystem, tmp_path):
    path = _write(tmp_path, "s.py", "def scan(context):\n    return {'BTC-USD': 55}\n")
    ecosystem.scanners.specs = [ScannerSpec("watcher", path)]
    ecosystem.settings.set("idea_lab", False, by="test")
    assert ecosystem.idea_lab is None
    ecosystem.tick()
    assert ecosystem.db.query("SELECT * FROM sandbox_ideas") == []


def test_the_environment_can_turn_it_off_by_default(monkeypatch):
    monkeypatch.setenv("TRADE_IDEA_LAB", "off")
    assert ideas.on_by_default() is False
    monkeypatch.setenv("TRADE_IDEA_LAB", "on")
    assert ideas.on_by_default() is True


def test_the_tick_survives_a_lab_that_breaks(ecosystem, monkeypatch):
    class Broken:
        def run(self, market, calls):
            raise RuntimeError("boom")

    ecosystem._idea_lab = Broken()
    report = ecosystem.tick()
    assert any("idea lab failed: boom" in n for n in report.bot_notes)


def test_mission_control_shows_the_lab(ecosystem, lab):
    from src.trading.web import _idea_lab_panel, _switches_panel

    assert "Nothing has closed yet" in _idea_lab_panel(ecosystem)
    _, writer = sandbox_handles(ecosystem.store)
    flat = IdeaLab(writer, costs=lab.costs)
    flat.step(OPEN, [buy()], Prices({"AMD": 100}, spy=500))
    flat.step(OPEN + timedelta(hours=1), [], Prices({"AMD": 103}, spy=500))
    html = _idea_lab_panel(ecosystem)
    assert "Held for an hour" in html and "scan" in html and "+$28.00" in html
    assert "too few to tell" in html and "1 of 20 ideas" in html
    assert "Latest to close" in html and "SPY&nbsp;$0.00" in html
    assert "Idea lab" in _switches_panel(ecosystem)
