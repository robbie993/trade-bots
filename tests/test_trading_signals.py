"""Scanners — the bots that spot things rather than trade them.

The adapter's tests are about what happens when a stranger's file gets to
propose orders. These are about the seat below that: a file that can only
publish a number, and the two properties that make listening to it safe.

    1. There is no path from a published reading to a fill. A scanner
       screaming +100 is one vote at one seat, and every gate downstream is
       untouched by its existence.
    2. A stale reading is silence. A scanner that crashed, was deleted or
       never ran does not keep voting from the bar it last spoke on.

Everything else here is the usual blast-radius work: a scanner that raises,
hangs, or returns rubbish costs one bar's readings and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.trading import signals
from src.trading.signals import Reading, ScannerError, ScannerSpec, Scanners, SignalBoard

BAR = datetime(2026, 3, 4, tzinfo=timezone.utc)
LATER = datetime(2026, 3, 5, tzinfo=timezone.utc)


def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return path


# =========================================================================
# config
# =========================================================================
def test_a_scanner_can_be_written_as_just_a_path():
    spec = ScannerSpec.from_mapping("whales", "bots/whales.py")
    assert str(spec.path) == "bots/whales.py"
    assert spec.universe == ()          # empty means "the whole village"
    assert spec.enabled is True


def test_a_scanner_with_no_file_is_refused():
    with pytest.raises(ScannerError, match="no `bot:` path"):
        ScannerSpec.from_mapping("whales", {"universe": ["SPY"]})


def test_a_scanner_universe_is_upper_cased_and_may_be_a_string():
    spec = ScannerSpec.from_mapping("s", {"bot": "x.py", "universe": "spy, qqq"})
    assert spec.universe == ("SPY", "QQQ")


def test_a_disabled_scanner_stays_configured_but_silent(market, tmp_path):
    """Turning one off must not mean deleting the line that documents it."""
    path = _write(tmp_path, "s.py", "def scan(context):\n    return {'SPY': 90}\n")
    spec = ScannerSpec.from_mapping("s", {"bot": str(path), "enabled": False})
    board = _board()
    Scanners(board, [spec]).run(market, BAR)
    assert board.latest("SPY", BAR) == []


def test_no_scanners_block_is_not_an_error(tmp_path):
    path = _write(tmp_path, "firms.yaml", "firms:\n  a:\n    universe: [SPY]\n")
    assert signals.load_scanner_specs(path) == []


def test_a_missing_config_file_is_not_an_error(tmp_path):
    assert signals.load_scanner_specs(tmp_path / "nope.yaml") == []


def test_an_empty_scanners_block_is_not_a_broken_one(tmp_path):
    """`scanners: {}` reaches the fallback YAML parser as a string."""
    path = _write(tmp_path, "firms.yaml", "firms:\n  a:\n    universe: [SPY]\nscanners: {}\n")
    assert signals.load_scanner_specs(path) == []


def test_scanners_are_read_from_the_firm_config(tmp_path):
    path = _write(
        tmp_path,
        "firms.yaml",
        "firms:\n"
        "  a:\n"
        "    universe: [SPY]\n"
        "scanners:\n"
        "  whales:\n"
        "    bot: bots/whales.py\n"
        "    universe: [SPY, QQQ]\n",
    )
    specs = signals.load_scanner_specs(path)
    assert [s.name for s in specs] == ["whales"]
    assert specs[0].universe == ("SPY", "QQQ")


def test_a_scanner_is_never_mistaken_for_a_firm(tmp_path):
    """A config with no `firms:` header is the firm mapping itself, which was
    fine until the file grew a second top-level block."""
    from src.trading.firms.spec import load_firm_specs

    path = _write(
        tmp_path,
        "firms.yaml",
        "a:\n  universe: [SPY]\nscanners:\n  whales:\n    bot: bots/whales.py\n",
    )
    assert [s.firm_key for s in load_firm_specs(path)] == ["a"]


# =========================================================================
# loading — and staying out of the strategy adapter's lane
# =========================================================================
@pytest.mark.parametrize("name", ["scan", "signals", "rank", "watch", "publish"])
def test_any_of_the_scanner_names_works(tmp_path, name):
    from src.trading.adapter import load

    path = _write(tmp_path, f"{name}_s.py", f"def {name}(context):\n    return []\n")
    assert callable(load(path, entry_points=signals.ENTRY_POINTS))


def test_a_strategy_file_is_not_silently_run_as_a_scanner(tmp_path):
    """`propose` means it wants to trade. Wire it up as a firm's bot instead."""
    from src.trading.adapter import AdapterError, load

    path = _write(tmp_path, "strategy.py", "def propose(context):\n    return []\n")
    with pytest.raises(AdapterError, match="defines none of"):
        load(path, entry_points=signals.ENTRY_POINTS)


# =========================================================================
# what came back
# =========================================================================
def test_a_plain_mapping_of_symbol_to_score_is_the_easiest_shape():
    readings, complaints = signals.to_readings({"AAPL": 80, "MSFT": -20}, ["AAPL", "MSFT"])
    assert complaints == []
    assert [(r.symbol, r.score) for r in readings] == [
        ("AAPL", Decimal("80")), ("MSFT", Decimal("-20"))
    ]


def test_an_unstated_confidence_is_the_strength_of_the_score_capped():
    readings, _ = signals.to_readings({"AAPL": 100}, ["AAPL"])
    assert readings[0].confidence == signals.IMPLIED_CONFIDENCE_CAP
    readings, _ = signals.to_readings({"AAPL": 20}, ["AAPL"])
    assert readings[0].confidence == Decimal("20")


def test_a_mapping_of_symbol_to_reading_works_too():
    readings, complaints = signals.to_readings(
        {"AAPL": {"score": 40, "confidence": 12, "note": "whale bought"}}, ["AAPL"]
    )
    assert complaints == []
    assert readings[0].confidence == Decimal("12")
    assert readings[0].note == "whale bought"


def test_a_list_of_readings_works_and_so_does_a_single_one():
    many, _ = signals.to_readings([{"symbol": "AAPL", "score": 5}], ["AAPL"])
    one, _ = signals.to_readings({"symbol": "AAPL", "score": 5}, ["AAPL"])
    assert len(many) == len(one) == 1


def test_scores_and_confidences_are_clamped_not_trusted():
    readings, _ = signals.to_readings(
        {"AAPL": {"score": 5000, "confidence": 900}}, ["AAPL"]
    )
    assert readings[0].score == Decimal("100")
    assert readings[0].confidence == Decimal("100")


def test_a_bare_ticker_is_refused_because_it_cannot_vote():
    """A name with no direction is not a signal, and guessing one would be the
    system inventing an opinion about money."""
    readings, complaints = signals.to_readings(["AAPL", "MSFT"], ["AAPL", "MSFT"])
    assert readings == []
    assert len(complaints) == 2
    assert "no score" in complaints[0]


def test_a_symbol_outside_the_scanners_universe_is_dropped():
    readings, complaints = signals.to_readings({"TSLA": 90}, ["AAPL"])
    assert readings == []
    assert "not in this scanner's universe" in complaints[0]


def test_nonsense_scores_are_complaints_not_exceptions():
    readings, complaints = signals.to_readings({"AAPL": "very bullish"}, ["AAPL"])
    assert readings == []
    assert "is not a number" in complaints[0]


def test_one_bad_reading_does_not_lose_the_good_ones():
    readings, complaints = signals.to_readings(
        {"AAPL": 50, "MSFT": "nope", "TSLA": -10}, ["AAPL", "MSFT", "TSLA"]
    )
    assert {r.symbol for r in readings} == {"AAPL", "TSLA"}
    assert len(complaints) == 1


def test_a_runaway_scanner_is_capped_and_says_so():
    flood = {f"S{i}": 10 for i in range(signals.MAX_READINGS + 40)}
    readings, complaints = signals.to_readings(flood, list(flood))
    assert len(readings) == signals.MAX_READINGS
    assert "only the first" in complaints[-1]


def test_something_that_is_not_readings_at_all_is_a_complaint():
    readings, complaints = signals.to_readings(42, ["AAPL"])
    assert readings == []
    assert "expected readings" in complaints[0]


def test_returning_nothing_is_fine_and_means_nothing_caught_its_eye():
    assert signals.to_readings(None, ["AAPL"]) == ([], [])


# =========================================================================
# the board
# =========================================================================
def _board():
    from src.db.connection import Database

    db = Database("sqlite:///:memory:")
    db.init_schema()
    return SignalBoard(db)


def test_a_published_reading_comes_back_on_its_own_bar():
    board = _board()
    board.publish("whales", [Reading("SPY", Decimal("60"), Decimal("50"), "big")], BAR)
    signal = board.reading("SPY", BAR)
    assert signal.score == Decimal("60")
    assert signal.confidence == Decimal("50")
    assert "whales" in signal.note


def test_a_reading_from_an_earlier_bar_is_silence_not_a_stale_vote():
    """The whole safety property. A scanner that stopped running stops voting."""
    board = _board()
    board.publish("whales", [Reading("SPY", Decimal("90"), Decimal("90"))], BAR)
    assert board.reading("SPY", LATER) is None


def test_a_confidence_of_zero_is_silence_not_neutrality():
    board = _board()
    board.publish("quiet", [Reading("SPY", Decimal("80"), Decimal("0"))], BAR)
    assert board.reading("SPY", BAR) is None


def test_scanners_are_averaged_by_how_sure_they_are():
    board = _board()
    board.publish("loud", [Reading("SPY", Decimal("100"), Decimal("90"))], BAR)
    board.publish("meek", [Reading("SPY", Decimal("0"), Decimal("10"))], BAR)
    signal = board.reading("SPY", BAR)
    assert signal.score == Decimal("90")     # (100*90 + 0*10) / 100


def test_publishers_that_disagree_about_direction_lose_confidence():
    board = _board()
    board.publish("bull", [Reading("SPY", Decimal("80"), Decimal("60"))], BAR)
    board.publish("bear", [Reading("SPY", Decimal("-80"), Decimal("60"))], BAR)
    signal = board.reading("SPY", BAR)
    assert signal.confidence == Decimal("30")   # mean 60, halved for the split


def test_a_publisher_that_speaks_twice_on_one_bar_is_heard_once():
    board = _board()
    board.publish("whales", [Reading("SPY", Decimal("10"), Decimal("50"))], BAR)
    board.publish("whales", [Reading("SPY", Decimal("90"), Decimal("50"))], BAR)
    signal = board.reading("SPY", BAR)
    assert signal.score == Decimal("90")       # the newer row, not both averaged
    assert "1 scanner" in signal.note


def test_the_board_remembers_who_has_already_spoken_for_a_bar():
    board = _board()
    assert board.published("whales", BAR) is False
    board.publish("whales", [Reading("SPY", Decimal("10"), Decimal("50"))], BAR)
    assert board.published("whales", BAR) is True
    assert board.published("whales", LATER) is False


def test_a_board_over_an_un_migrated_database_is_silent_rather_than_broken():
    from src.db.connection import Database

    board = SignalBoard(Database("sqlite:///:memory:"))   # no init_schema
    assert board.publish("w", [Reading("SPY", Decimal("1"), Decimal("1"))], BAR) == 0
    assert board.reading("SPY", BAR) is None
    assert board.recent() == []


# =========================================================================
# running them
# =========================================================================
def test_a_scanner_publishes_what_it_returns(market, tmp_path):
    path = _write(
        tmp_path, "s.py",
        "def scan(context):\n"
        "    return {'SPY': 55}\n",
    )
    board = _board()
    notes = Scanners(board, [ScannerSpec("s", path)]).run(market, BAR)
    assert board.reading("SPY", BAR).score == Decimal("55")
    assert any("published 1 reading" in n for n in notes)


def test_a_scanner_sees_prices_and_no_money(market, tmp_path):
    """It has no firm, so it has no cash, no equity and no positions."""
    context = signals.build_context(["SPY"], market)
    assert context.cash == 0
    assert context.equity == 0
    assert context.positions == []
    assert context.price("SPY") > 0
    assert len(context.closes("SPY")) > 0


def test_a_scanner_that_raises_costs_one_bar_and_says_why(market, tmp_path):
    path = _write(tmp_path, "s.py", "def scan(context):\n    raise ValueError('boom')\n")
    board = _board()
    notes = Scanners(board, [ScannerSpec("s", path)]).run(market, BAR)
    assert board.reading("SPY", BAR) is None
    assert any("ValueError: boom" in n for n in notes)


def test_a_scanner_that_hangs_is_abandoned(market, tmp_path):
    path = _write(
        tmp_path, "s.py",
        "import time\n"
        "def scan(context):\n"
        "    time.sleep(30)\n",
    )
    board = _board()
    notes = Scanners(board, [ScannerSpec("s", path)], timeout=0.3).run(market, BAR)
    assert any("took longer than" in n for n in notes)


def test_a_scanner_that_will_not_load_is_reported_not_raised(market, tmp_path):
    board = _board()
    notes = Scanners(board, [ScannerSpec("s", tmp_path / "gone.py")]).run(market, BAR)
    assert any("does not exist" in n for n in notes)


def test_one_broken_scanner_does_not_silence_a_working_one(market, tmp_path):
    bad = _write(tmp_path, "bad.py", "def scan(context):\n    raise RuntimeError('x')\n")
    good = _write(tmp_path, "good.py", "def scan(context):\n    return {'SPY': 30}\n")
    board = _board()
    Scanners(board, [ScannerSpec("bad", bad), ScannerSpec("good", good)]).run(market, BAR)
    assert board.reading("SPY", BAR).score == Decimal("30")


def test_a_scanner_is_asked_once_per_bar_not_once_per_tick(market, tmp_path):
    """A daily bar lasts many ticks, and a deterministic scanner asked twice
    gives the same answer twice."""
    counter = tmp_path / "count.txt"
    path = _write(
        tmp_path, "s.py",
        f"COUNT = {str(counter)!r}\n"
        "def scan(context):\n"
        "    open(COUNT, 'a').write('x')\n"
        "    return {'SPY': 10}\n",
    )
    board = _board()
    desk = Scanners(board, [ScannerSpec("s", path)])
    desk.run(market, BAR)
    desk.run(market, BAR)
    assert counter.read_text() == "x"
    desk.run(market, LATER)
    assert counter.read_text() == "xx"


def test_a_broken_scanner_config_nags_every_pass(market):
    desk = Scanners(_board(), [], error="scanner config unusable: bad indentation")
    assert "unusable" in desk.run(market, BAR)[0]
    assert "unusable" in desk.run(market, BAR)[0]


def test_with_no_bars_the_scanners_have_nothing_to_read(feed, tmp_path):
    from src.trading.data.market_data import MarketData

    path = _write(tmp_path, "s.py", "def scan(context):\n    return {'SPY': 1}\n")
    empty = MarketData(feed, [])            # nothing registered, so no as_of
    notes = Scanners(_board(), [ScannerSpec("s", path)]).run(empty)
    assert "no bars yet" in notes[0]


# =========================================================================
# the seat in the debate
# =========================================================================
def test_the_signals_seat_is_silent_without_a_board(market):
    from src.trading.firms.analysts import build_analysts

    seat = build_analysts(["signals"])[0]
    reading = seat.analyse("SPY", market, {})
    assert reading.is_silent
    assert "no signal board" in reading.note


def test_the_signals_seat_repeats_what_was_published(market):
    from src.trading.firms.analysts import build_analysts

    board = _board()
    board.publish("whales", [Reading("SPY", Decimal("70"), Decimal("40"))], market.as_of())
    seat = build_analysts(["signals"], board=board)[0]
    reading = seat.analyse("SPY", market, {})
    assert reading.score == Decimal("70")
    assert reading.confidence == Decimal("40")


def test_the_signals_seat_is_silent_when_nobody_published(market):
    from src.trading.firms.analysts import build_analysts

    seat = build_analysts(["signals"], board=_board())[0]
    assert seat.analyse("SPY", market, {}).is_silent


def test_a_firm_can_turn_the_trust_down_without_dropping_the_seat(market):
    from src.trading.firms.analysts import build_analysts

    board = _board()
    board.publish("whales", [Reading("SPY", Decimal("70"), Decimal("40"))], market.as_of())
    seat = build_analysts(["signals"], board=board)[0]
    assert seat.analyse("SPY", market, {"signal_trust": 50}).confidence == Decimal("20")
    assert seat.analyse("SPY", market, {"signal_trust": 0}).is_silent


@pytest.mark.parametrize("alias", ["scanner", "scanners", "signal", "Signals"])
def test_the_seat_answers_to_what_people_actually_write(alias):
    from src.trading.firms.analysts import SignalAnalyst, build_analysts

    assert isinstance(build_analysts([alias])[0], SignalAnalyst)


# =========================================================================
# the blast radius
# =========================================================================
def test_a_scanner_cannot_reach_the_ledger(market, tmp_path):
    """The context is prices and nothing else. There is no store on it, no db,
    no gate — the same rule the strategy adapter follows, one level stricter
    because there is no book to look at either."""
    context = signals.build_context(["SPY"], market)
    for forbidden in ("store", "db", "gate", "ecosystem", "firm", "record"):
        assert not hasattr(context, forbidden)


def test_a_screaming_scanner_buys_nothing_by_itself(ecosystem, tmp_path):
    """The property the whole design rests on: publishing +100 on everything
    moves one seat's vote, and every gate downstream is untouched."""
    path = _write(
        tmp_path, "shout.py",
        "def scan(context):\n"
        "    return {s: {'score': 100, 'confidence': 100} for s in context.universe}\n",
    )
    from src.trading.signals import ScannerSpec as Spec

    ecosystem.scanners.specs = [Spec("shout", path)]
    report = ecosystem.tick()

    # It published, and it was heard.
    assert any("shout published" in n for n in report.signals)
    # And every proposal it contributed to still went through risk review.
    for proposal in ecosystem.store.proposals(limit=50):
        assert proposal.risk_verdict in ("allow", "reduce", "block")


def test_the_shipped_config_registers_a_scanner_that_exists(tmp_path, monkeypatch):
    """The example is registered on purpose, so the seat is visibly working
    rather than working once you find the switch. If it ships registered, the
    file it names has to be there."""
    from pathlib import Path

    from src.trading.firms.spec import load_firm_specs

    config = Path("config/firm_config.yaml")
    specs = signals.load_scanner_specs(config)
    assert specs, "the shipped config should register the example scanner"
    for spec in specs:
        assert spec.path.exists(), spec.path

    listening = [
        f.firm_key for f in load_firm_specs(config)
        if "signals" in {str(a).lower() for a in f.analysts}
    ]
    assert listening, "a registered scanner nobody hears is a scanner that does nothing"


def test_mission_control_shows_what_the_scanners_published(ecosystem, tmp_path, monkeypatch):
    """The panel exists to answer 'is my screener doing anything', which has
    two halves people conflate: did it publish, and was it for this bar."""
    from src.trading.web import _signals_panel

    path = _write(tmp_path, "s.py", "def scan(context):\n    return {'SPY': 44}\n")
    from src.trading.signals import ScannerSpec as Spec

    ecosystem.scanners.specs = [Spec("watcher", path)]
    market = ecosystem.market()
    ecosystem.scanners.run(market)

    html = _signals_panel(ecosystem, market)
    assert "watcher" in html
    assert "+44.00" in html
    assert "this bar" in html
    assert "Nobody is listening" in html      # the fixture firms have no signals seat


def test_mission_control_has_no_scanner_panel_when_there_are_none(ecosystem):
    from src.trading.web import _signals_panel

    ecosystem.scanners.specs = []
    ecosystem.scanners.error = ""
    assert _signals_panel(ecosystem, ecosystem.market()) == ""


def test_the_tick_survives_a_scanner_that_explodes(ecosystem, tmp_path):
    path = _write(tmp_path, "bad.py", "def scan(context):\n    raise SystemExit(1)\n")
    from src.trading.signals import ScannerSpec as Spec

    ecosystem.scanners.specs = [Spec("bad", path)]
    report = ecosystem.tick()
    assert any("bad:" in n for n in report.signals)
    assert report.errors == [] or all("bad.py" not in e for e in report.errors)
