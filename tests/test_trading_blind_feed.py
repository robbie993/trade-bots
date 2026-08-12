"""A feed that goes dark must not empty the village.

This is a regression test for the worst failure this system has had. A live
village on an Alpaca feed that stopped returning bars marked every position to
zero, showed every firm a total drawdown, and killed all eleven of them in one
pass. `killed` is terminal by design, so the whole village was gone — not
because any strategy lost money, but because nobody could see the prices.

The mechanism is worth stating exactly, because every part of it was working
as designed:

    MarketData.mark() returns ZERO for a symbol with no bars — a fine answer
    to "what is this worth" and a catastrophic one to "has this firm lost
    everything". The evaluator computes equity from marks. The kill switch
    checks drawdown *first*, before the sample gate, deliberately, because
    emptying the account is the thing it must never wait to act on.

So: no bug in any one component, and a dead village. "The system may always
stop the bleeding" is not a licence to amputate on a broken thermometer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.trading.firms.kill_switch import (
    CANNOT_VALUE,
    FirmMetrics,
    should_kill_firm,
)


# =========================================================================
# the guard itself
# =========================================================================
def test_a_total_drawdown_still_kills_when_the_book_can_be_priced():
    """The guard must not become a way for a real blow-up to survive."""
    should, why = should_kill_firm(FirmMetrics(trades=50, drawdown_pct=Decimal("95")))
    assert should is True
    assert "Drawdown" in why


def test_the_same_drawdown_does_not_kill_when_the_book_cannot_be_priced():
    should, why = should_kill_firm(
        FirmMetrics(trades=50, drawdown_pct=Decimal("95"), unpriceable=("SPY",))
    )
    assert should is False
    assert CANNOT_VALUE in why
    assert "SPY" in why


def test_blindness_is_checked_before_every_other_condition():
    """Drawdown, worst trade and consecutive losses all run before the sample
    gate. If any of them is reachable while blind, the village still dies."""
    catastrophic = FirmMetrics(
        trades=99,
        drawdown_pct=Decimal("100"),
        worst_trade_pct=Decimal("100"),
        consecutive_losses=99,
        win_rate_pct=Decimal("0"),
        sharpe=Decimal("-9"),
        unpriceable=("SPY", "QQQ"),
    )
    should, why = should_kill_firm(catastrophic)
    assert should is False
    assert CANNOT_VALUE in why


def test_the_reason_names_the_symbols_but_does_not_run_on_forever():
    metrics = FirmMetrics(unpriceable=tuple(f"S{i}" for i in range(12)))
    why = should_kill_firm(metrics)[1]
    assert "and others" in why
    assert len(why) < 120


def test_a_firm_holding_nothing_is_never_blind():
    """No positions, no marks to be wrong about. A flat firm is judged
    normally, which matters because that is most of them."""
    should, why = should_kill_firm(FirmMetrics(trades=50, drawdown_pct=Decimal("95")))
    assert should is True


# =========================================================================
# the evaluator noticing
# =========================================================================
def _hold(store, firm, symbol, quantity="10", price="400"):
    """Put an open position on the books. `is_open` is quantity != 0."""
    store.db.insert("positions", {
        "firm_id": firm.id, "symbol": symbol,
        "quantity": str(quantity), "avg_price": str(price),
    })


def test_the_scorecard_reports_which_holdings_cannot_be_priced(store, firm_record, market_data):
    from src.trading.brokerage.evaluator import Evaluator
    from src.trading.config import TradingConfig

    _hold(store, firm_record, "SPY")
    _hold(store, firm_record, "PEPE-USD")        # not in the synthetic feed's world
    market_data.unpriceable["PEPE-USD"] = "no exchange lists it"

    card = Evaluator(store, TradingConfig()).evaluate(firm_record, market_data)
    assert card.unpriceable == ("PEPE-USD",)
    assert card.can_be_valued is False


def test_a_fully_priceable_book_is_not_flagged(store, firm_record, market_data):
    from src.trading.brokerage.evaluator import Evaluator
    from src.trading.config import TradingConfig

    _hold(store, firm_record, "SPY")
    card = Evaluator(store, TradingConfig()).evaluate(firm_record, market_data)
    assert card.unpriceable == ()
    assert card.can_be_valued is True


# =========================================================================
# the whole accident, reproduced
# =========================================================================
class BlindFeed:
    """A feed that has stopped answering — an Alpaca outage, in one class."""

    name = "blind"

    def series(self, symbol):
        return []


def test_a_dead_feed_no_longer_kills_the_village(store, firm_record, market_data):
    """The actual failure: eleven firms, one outage, no survivors.

    Reproduced with one firm holding a position it bought when prices worked,
    and a feed that then goes dark.
    """
    from src.trading.brokerage.evaluator import Evaluator
    from src.trading.config import TradingConfig
    from src.trading.data.market_data import MarketData

    _hold(store, firm_record, "SPY", quantity="200", price="450")
    store.db.execute(
        "UPDATE firms SET cash = ?, high_water_mark = ? WHERE id = ?",
        ("10000.00", "100000.00", firm_record.id),
    )
    firm = store.require_firm_by_id(firm_record.id)

    blind = MarketData(BlindFeed(), ["SPY"])
    card = Evaluator(store, TradingConfig()).evaluate(firm, blind)

    # The numbers are still wrong — this does not invent prices it does not
    # have. Equity really does read as cash alone, and the drawdown really
    # does read as catastrophic.
    assert card.equity == Decimal("10000.00")
    assert card.drawdown_pct > Decimal("80")

    # What changed is that nobody acts on them.
    assert card.can_be_valued is False
    should, why = should_kill_firm(card.to_metrics())
    assert should is False, "a blind feed killed a firm again"
    assert CANNOT_VALUE in why


def test_the_brokerage_does_not_even_request_a_kill_while_blind(ecosystem):
    """Pausing is autonomous and killing needs a human — but a kill *request*
    on a blind book still puts a decision in front of somebody with nothing
    real behind it, and a human who approves eleven of those has been misled
    by their own system."""
    from src.trading.data.market_data import MarketData

    firm = ecosystem.store.firms()[0]
    _hold(ecosystem.store, firm, "SPY", quantity="200", price="450")
    ecosystem.store.db.execute(
        "UPDATE firms SET cash = ?, high_water_mark = ? WHERE id = ?",
        ("100.00", "50000.00", firm.id),
    )

    blind = MarketData(BlindFeed(), ["SPY"])
    cards = ecosystem.brokerage.evaluator.evaluate_all(ecosystem.store.firms(), blind)
    paused, requested = ecosystem.brokerage.check_kills(ecosystem.store.firms(), cards)
    assert paused == []
    assert requested == []


# =========================================================================
# getting the village back
# =========================================================================
def test_a_firm_killed_while_blind_can_be_revived_once_prices_return(ecosystem):
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.kill_firm(firm.firm_key, "Drawdown 100.0% exceeds 40%")
    assert ecosystem.store.get_firm(firm.firm_key).is_killed

    result = ecosystem.brokerage.revive_firm(firm.firm_key, "robbie", ecosystem.market())
    assert result["status"] == "active"
    assert ecosystem.store.get_firm(firm.firm_key).is_active


def test_reviving_refuses_while_the_feed_is_still_blind(ecosystem):
    """Reviving into blindness would just re-run the accident next tick."""
    from src.trading.data.market_data import MarketData

    firm = ecosystem.store.firms()[0]
    _hold(ecosystem.store, firm, "SPY")
    ecosystem.brokerage.kill_firm(firm.firm_key, "Drawdown 100.0% exceeds 40%")

    with pytest.raises(ValueError, match="cannot price"):
        ecosystem.brokerage.revive_firm(
            firm.firm_key, "robbie", MarketData(BlindFeed(), ["SPY"])
        )


def test_reviving_refuses_a_firm_that_would_be_killed_again(ecosystem):
    """The guard that stops this being a general appeal against the verdict:
    a firm that really did blow up gets killed again on today's numbers, so
    the kill stands."""
    firm = ecosystem.store.firms()[0]
    ecosystem.store.db.execute(
        "UPDATE firms SET cash = ?, high_water_mark = ?, consecutive_losses = ? WHERE id = ?",
        ("100.00", "50000.00", 99, firm.id),
    )
    ecosystem.brokerage.kill_firm(firm.firm_key, "Drawdown 99% exceeds 40%")

    with pytest.raises(ValueError, match="would be killed again"):
        ecosystem.brokerage.revive_firm(firm.firm_key, "robbie", ecosystem.market())
    assert ecosystem.store.get_firm(firm.firm_key).is_killed


def test_reviving_does_not_hand_capital_back(ecosystem):
    """A kill releases the firm's capital. Giving it back is an increase in
    risk, which needs a human at the gate exactly as it always did — reviving
    restores the status and nothing else."""
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.kill_firm(firm.firm_key, "Drawdown 100.0% exceeds 40%")
    after_kill = ecosystem.store.get_firm(firm.firm_key).allocation

    ecosystem.brokerage.revive_firm(firm.firm_key, "robbie", ecosystem.market())
    assert ecosystem.store.get_firm(firm.firm_key).allocation == after_kill


def test_resume_still_refuses_a_killed_firm_and_says_where_to_go(ecosystem):
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.kill_firm(firm.firm_key, "whatever")
    with pytest.raises(ValueError, match="revive"):
        ecosystem.brokerage.resume_firm(firm.firm_key, "robbie")


def test_revive_refuses_a_firm_that_is_merely_paused(ecosystem):
    from src.trading.models import FirmStatus

    firm = ecosystem.store.firms()[0]
    ecosystem.store.set_firm_status(firm.id, FirmStatus.PAUSED.value)
    with pytest.raises(ValueError, match="not killed"):
        ecosystem.brokerage.revive_firm(firm.firm_key, "robbie", ecosystem.market())


def test_the_revive_command_can_sweep_a_whole_village(ecosystem, capsys, monkeypatch):
    """The case it exists for: an outage does not kill one firm, it kills
    every firm holding anything."""
    from src.trading.cli import cmd_revive

    for firm in ecosystem.store.firms():
        ecosystem.brokerage.kill_firm(firm.firm_key, "Drawdown 100.0% exceeds 40%")
    dead = sum(1 for f in ecosystem.store.firms() if f.is_killed)
    assert dead >= 2

    monkeypatch.setattr("src.trading.cli._ecosystem", lambda args: ecosystem)

    class Args:
        firm = None
        all = True
        by = "robbie"

    assert cmd_revive(Args()) == 0
    assert f"{dead} revived" in capsys.readouterr().out
    assert sum(1 for f in ecosystem.store.firms() if f.is_killed) == 0


# =========================================================================
# why the feed went blind in the first place
# =========================================================================
def test_no_feed_needs_a_third_party_http_client():
    """The original cause, guarded against directly.

    Both real feeds did `import requests` and raised a tidy "pip install
    requests" when it was missing. `requests` is in requirements.txt and *not*
    in pyproject's dependencies, and the setup instructions say
    `pip install -e .` — so the documented install produced a village whose
    every real feed refused, per symbol, silently, presenting as a market that
    had ceased to exist.

    A price feed is core, and the core of this repository runs on the standard
    library. Adding `requests` to pyproject would have fixed this install and
    left the trap for the next one.
    """
    from pathlib import Path

    source = Path("src/trading/data/feeds.py").read_text()
    offenders = [
        line.strip() for line in source.splitlines()
        if "import requests" in line and not line.strip().startswith(("#", "*"))
    ]
    assert offenders == [], offenders


def test_a_feed_that_cannot_be_reached_says_so_in_status(ecosystem, capsys, monkeypatch):
    """`as of: None` on its own is a symptom with no cause attached."""
    from src.trading.cli import _feed_trouble
    from src.trading.data.feeds import FeedNotConfigured

    def refuse(symbol):
        raise FeedNotConfigured("alpaca refused the credentials for SPY (403)")

    monkeypatch.setattr(ecosystem.feed, "series", refuse)
    report = _feed_trouble(ecosystem)
    assert "NO PRICES" in report
    assert "refused the credentials" in report
    assert "revive" in report


def test_a_working_feed_says_the_problem_is_elsewhere(ecosystem):
    from src.trading.cli import _feed_trouble

    assert "can price" in _feed_trouble(ecosystem)
