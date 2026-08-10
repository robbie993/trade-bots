"""Running somebody else's bot as a firm's strategy.

The importer translates a foreign bot into seven genes, which fails quietly
when the strategy is not seven genes — and most are not, because the logic
lives in a function. The adapter inverts that: the file decides, the village
governs.

That means executing a file, which nothing else here does. So most of these
tests are about the blast radius rather than the happy path: a bot that raises,
hangs, returns nonsense, or reaches for something it should not have must cost
one firm one quiet tick and nothing else.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.trading import adapter
from src.trading.models import Position


# =========================================================================
# which firms run a bot at all
# =========================================================================
def test_only_an_explicit_bot_prefix_counts():
    """Nothing runs by being in a directory. A firm has to name the file."""
    assert adapter.is_bot("bot:bots/mine.py") is True
    assert adapter.is_bot("momentum") is False
    assert adapter.is_bot("") is False
    assert adapter.is_bot(None) is False


def test_the_path_comes_back_out():
    assert str(adapter.bot_path("bot:bots/mine.py")) == "bots/mine.py"
    assert str(adapter.bot_path("bot:  bots/mine.py  ")) == "bots/mine.py"


def test_the_config_key_produces_the_prefix():
    from src.trading.firms.spec import _strategy

    assert _strategy({"bot": "bots/mine.py"}) == "bot:bots/mine.py"
    assert _strategy({"strategy": "momentum"}) == "momentum"


# =========================================================================
# loading
# =========================================================================
def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return path


def test_a_missing_file_is_an_adapter_error(tmp_path):
    with pytest.raises(adapter.AdapterError, match="does not exist"):
        adapter.load(tmp_path / "nope.py")


def test_a_non_python_file_is_refused(tmp_path):
    path = _write(tmp_path, "bot.yaml", "genome: {}\n")
    with pytest.raises(adapter.AdapterError, match=r"\.py file"):
        adapter.load(path)


def test_a_file_that_explodes_on_import_is_an_adapter_error(tmp_path):
    path = _write(tmp_path, "bad.py", "raise RuntimeError('boom at import')\n")
    with pytest.raises(adapter.AdapterError, match="failed while loading"):
        adapter.load(path)


def test_a_file_with_no_entry_point_says_what_it_needs(tmp_path):
    path = _write(tmp_path, "empty.py", "X = 1\n")
    with pytest.raises(adapter.AdapterError, match="defines none of"):
        adapter.load(path)


@pytest.mark.parametrize("name", ["propose", "on_bar", "decide", "strategy"])
def test_any_of_the_accepted_names_works(tmp_path, name):
    path = _write(tmp_path, f"{name}_bot.py", f"def {name}(context):\n    return []\n")
    assert callable(adapter.load(path))


# =========================================================================
# calling it without letting it take the tick down
# =========================================================================
def test_a_bot_that_raises_is_reported_not_propagated():
    def explode(context):
        raise ValueError("I am a bad bot")

    value, error = adapter.call(explode, None)
    assert value is None
    assert "ValueError: I am a bad bot" in error


def test_a_bot_that_hangs_is_abandoned():
    """Python cannot kill a thread. It can stop waiting for one."""
    import threading

    forever = threading.Event()

    def hang(context):
        forever.wait(30)

    value, error = adapter.call(hang, None, timeout=0.3)
    assert value is None
    assert "took longer than" in error
    forever.set()


def test_a_bot_that_raises_something_that_is_not_an_exception_is_caught():
    def rude(context):
        raise KeyboardInterrupt("not an Exception subclass")

    value, error = adapter.call(rude, None)
    assert value is None
    assert "KeyboardInterrupt" in error


def test_a_well_behaved_bot_just_returns():
    value, error = adapter.call(lambda context: [{"symbol": "SPY"}], None)
    assert error == ""
    assert value == [{"symbol": "SPY"}]


# =========================================================================
# what the bot can see — and what it cannot
# =========================================================================
@pytest.fixture
def context(firm_record, market_data):
    positions = [
        Position(firm_id=firm_record.id, symbol="SPY",
                 quantity=Decimal("10"), avg_price=Decimal("400"))
    ]
    return adapter.build_context(firm_record, market_data, positions, Decimal("100000"))


def test_the_context_carries_prices_and_the_book(context):
    assert "SPY" in context.universe
    assert context.price("SPY") > 0
    assert len(context.closes("SPY", 20)) == 20
    assert context.quantity("SPY") == Decimal("10")
    assert context.quantity("QQQ") == 0
    assert context.position("QQQ") is None


def test_the_context_is_not_a_way_into_the_ledger(context):
    """A strategy needs prices and its own book. Anything more is a route
    around the parts of this system that make it trustworthy."""
    for forbidden in ("store", "db", "database", "gate", "ecosystem", "firms"):
        assert not hasattr(context, forbidden)


def test_the_context_cannot_be_edited(context):
    with pytest.raises(Exception):
        context.cash = Decimal("999999999")


def test_an_unknown_symbol_is_a_none_price_not_a_crash(context):
    assert context.price("NOTREAL") is None
    assert context.closes("NOTREAL") == []


# =========================================================================
# validating what came back
# =========================================================================
@pytest.fixture
def checked(firm_record, context):
    def check(result):
        return adapter.to_proposals(result, firm_record, context, None)
    return check


def test_a_plain_order_becomes_a_proposal(checked):
    proposals, complaints = checked([
        {"symbol": "SPY", "side": "buy", "quantity": 3, "rationale": "because"}
    ])
    assert complaints == []
    assert len(proposals) == 1
    assert proposals[0].symbol == "SPY"
    assert proposals[0].quantity == Decimal("3")
    assert proposals[0].rationale == "because"


def test_a_notional_is_turned_into_a_quantity(checked, context):
    """A bot may say "$1000 of SPY" and let the village work out the size.

    The quantity is quantised on the way in, so the round-trip is what matters:
    size times price comes back to the notional, within rounding.
    """
    proposals, _ = checked([{"symbol": "SPY", "side": "buy", "notional": 1000}])
    spent = proposals[0].quantity * context.price("SPY")
    assert abs(spent - Decimal(1000)) < Decimal("0.01")


def test_a_symbol_outside_the_universe_is_dropped_with_a_reason(checked):
    proposals, complaints = checked([{"symbol": "TSLA", "side": "buy", "quantity": 1}])
    assert proposals == []
    assert "not in this firm's universe" in complaints[0]


@pytest.mark.parametrize("quantity", [0, -5, "banana", None])
def test_a_quantity_that_is_not_a_positive_number_is_dropped(checked, quantity):
    proposals, complaints = checked([
        {"symbol": "SPY", "side": "buy", "quantity": quantity}
    ])
    assert proposals == []
    assert complaints


def test_an_unrecognised_side_is_dropped(checked):
    proposals, complaints = checked([{"symbol": "SPY", "side": "yolo", "quantity": 1}])
    assert proposals == []
    assert "not buy or sell" in complaints[0]


def test_the_good_orders_survive_the_bad_ones(checked):
    """One malformed order must not throw away the rest of the list."""
    proposals, complaints = checked([
        {"symbol": "TSLA", "side": "buy", "quantity": 1},     # not in universe
        {"symbol": "SPY", "side": "buy", "quantity": 2},      # fine
        {"symbol": "SPY", "side": "buy", "quantity": -1},     # negative
    ])
    assert [p.symbol for p in proposals] == ["SPY"]
    assert len(complaints) == 2


def test_a_runaway_loop_is_capped(checked):
    proposals, complaints = checked(
        [{"symbol": "SPY", "side": "buy", "quantity": 1}] * 500
    )
    assert len(proposals) == adapter.MAX_PROPOSALS
    assert any("only the first" in c for c in complaints)


def test_returning_nothing_is_a_quiet_day_not_an_error(checked):
    assert checked(None) == ([], [])
    assert checked([]) == ([], [])


def test_returning_the_wrong_shape_complains_rather_than_crashing(checked):
    proposals, complaints = checked("buy everything")
    assert proposals == []
    assert "expected a list" in complaints[0]


def test_a_single_dict_is_treated_as_one_order(checked):
    proposals, _ = checked({"symbol": "SPY", "side": "buy", "quantity": 1})
    assert len(proposals) == 1


# =========================================================================
# end to end, through a real firm
# =========================================================================
def _bot_firm(tmp_path, firms_yaml, body):
    bot = tmp_path / "mybot.py"
    bot.write_text(body)
    firms_yaml.write_text(
        "firms:\n"
        "  desk:\n"
        "    name: Desk\n"
        "    asset_class: ETF\n"
        "    capital_allocation: 50000\n"
        "    universe: [SPY, QQQ]\n"
        "    analysts: [technical]\n"
        f"    bot: {bot}\n"
    )
    return bot


def test_a_bot_firm_actually_trades(db, tmp_path, firms_yaml, notifier):
    from src.config import Config
    from src.trading.config import DataConfig, TradingConfig
    from src.trading.ecosystem import Ecosystem

    _bot_firm(tmp_path, firms_yaml, (
        "def propose(context):\n"
        "    out = []\n"
        "    for s in context.universe:\n"
        "        if context.quantity(s) == 0 and context.price(s):\n"
        "            out.append({'symbol': s, 'side': 'buy', 'notional': 1000,\n"
        "                        'rationale': 'buy and hold'})\n"
        "    return out\n"
    ))
    eco = Ecosystem(
        db,
        TradingConfig(firms_config=firms_yaml, audit_vault=tmp_path / "v",
                      vendor_dir=tmp_path / "d",
                      data=DataConfig(source="synthetic", seed=12345, history_days=180)),
        Config(database_url=db.url, notification_log=tmp_path / "n.log"),
        notifier,
    )
    eco.init_firms()
    reports = eco.simulate(10)

    assert sum(r.filled for r in reports) > 0
    assert not any(r.bot_notes for r in reports)


def test_a_broken_bot_costs_one_firm_a_quiet_tick(db, tmp_path, firms_yaml, notifier):
    """The property that makes running foreign code acceptable."""
    from src.config import Config
    from src.trading.config import DataConfig, TradingConfig
    from src.trading.ecosystem import Ecosystem

    _bot_firm(tmp_path, firms_yaml, (
        "def propose(context):\n"
        "    raise RuntimeError('the bot is broken')\n"
    ))
    eco = Ecosystem(
        db,
        TradingConfig(firms_config=firms_yaml, audit_vault=tmp_path / "v",
                      vendor_dir=tmp_path / "d",
                      data=DataConfig(source="synthetic", seed=12345, history_days=180)),
        Config(database_url=db.url, notification_log=tmp_path / "n.log"),
        notifier,
    )
    eco.init_firms()
    reports = eco.simulate(5)

    assert reports, "the tick still ran"
    assert sum(r.filled for r in reports) == 0
    notes = [n for r in reports for n in r.bot_notes]
    assert notes and "the bot is broken" in notes[0]
    # and the books still reconcile
    assert all(not r.errors for r in reports)


def test_a_bot_order_still_meets_the_risk_manager(db, tmp_path, firms_yaml, notifier):
    """A bot's order is not privileged. It gets cut like any other."""
    from src.config import Config
    from src.trading.config import DataConfig, TradingConfig
    from src.trading.ecosystem import Ecosystem

    _bot_firm(tmp_path, firms_yaml, (
        "def propose(context):\n"
        "    return [{'symbol': 'SPY', 'side': 'buy', 'notional': 10_000_000,\n"
        "             'rationale': 'all of it'}]\n"
    ))
    eco = Ecosystem(
        db,
        TradingConfig(firms_config=firms_yaml, audit_vault=tmp_path / "v",
                      vendor_dir=tmp_path / "d",
                      data=DataConfig(source="synthetic", seed=12345, history_days=180)),
        Config(database_url=db.url, notification_log=tmp_path / "n.log"),
        notifier,
    )
    eco.init_firms()
    eco.simulate(3)

    firm = eco.store.firms()[0]
    # It asked for $10m against a $50k book and did not get it.
    assert eco.store.cash_view(firm).withdrawable >= 0
    proposals = eco.store.proposals(firm.id, limit=20)
    assert proposals, "it did propose"
    assert all(p.risk_verdict for p in proposals), "every one was reviewed"
