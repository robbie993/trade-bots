"""Three strikes, served in bars, one strike per slump.

Both of the load-bearing properties here were settled by measuring the
village's own history, not by taste:

* **Per episode, not per bar.** Drawdown persists — a firm 27% under water is
  in breach on every bar until it recovers. Counting per bar gives
  firm_c_crypto 54 strikes and terminates it in three hours over one slump.
  Per episode gives it 2.
* **Served in bars, not on a clock.** Twenty bars is twenty hours to a crypto
  desk and three trading days to a bonds desk. A sentence stored as a time
  would treat those two identically while differing by 3.5x.
"""

from decimal import Decimal

import pytest

from src.money import D
from src.trading.firms.strikes import (
    StrikeConfig,
    clear_breach,
    record_breach,
    serve_sentence,
)
from src.trading.models import FirmRecord, FirmStatus


@pytest.fixture
def firm(store):
    return store.upsert_firm(FirmRecord(
        firm_key="striker",
        allocation=Decimal("50000"),
        initial_allocation=Decimal("50000"),
        cash=Decimal("50000"),
        universe=["SPY"],
    ))


def _reload(store, firm):
    return store.require_firm_by_id(firm.id)


def _state(store, firm):
    """Strike state lives in its own table, not on the firm record."""
    return store.strike_state(firm.id)


# =========================================================================
# a strike is an episode
# =========================================================================
def test_one_slump_earns_one_strike_however_long_it_lasts(store, firm):
    """The property that stops 54 strikes from a single drawdown."""
    config = StrikeConfig()
    first = record_breach(store, firm, "Drawdown 27% exceeds 20%", config)
    assert first is not None and first.strikes == 1

    for _ in range(50):
        again = record_breach(store, _reload(store, firm), "Drawdown 27% exceeds 20%", config)
        assert again is None, "the same slump must not earn a second strike"
    assert _state(store, firm)["strikes"] == 1


def test_recovering_then_failing_again_is_a_second_strike(store, firm):
    config = StrikeConfig()
    record_breach(store, firm, "Drawdown 27%", config)
    clear_breach(store, _reload(store, firm))          # climbed back out
    second = record_breach(store, _reload(store, firm), "Drawdown 24%", config)
    assert second is not None and second.strikes == 2
    assert second.terminated is False


def test_the_third_strike_terminates(store, firm):
    config = StrikeConfig()
    for _ in range(2):
        record_breach(store, _reload(store, firm), "Drawdown 27%", config)
        clear_breach(store, _reload(store, firm))
    third = record_breach(store, _reload(store, firm), "Drawdown 27%", config)
    assert third is not None
    assert third.terminated is True
    assert third.strikes == 3


# =========================================================================
# the sentence is served in bars
# =========================================================================
def test_a_sentence_counts_bars_not_ticks(store, firm):
    """Ticking a hundred times on one bar must serve exactly one bar."""
    config = StrikeConfig(gulag_bars_first=3)
    record_breach(store, firm, "Drawdown 27%", config)
    assert _state(store, firm)["gulag_bars_left"] == 3

    for _ in range(100):
        serve_sentence(store, _reload(store, firm), "2026-08-29T06")
    assert _state(store, firm)["gulag_bars_left"] == 2, (
        "a hundred ticks on one bar served more than one bar of the sentence"
    )


def test_the_sentence_ends_and_the_firm_is_released(store, firm):
    config = StrikeConfig(gulag_bars_first=3)
    record_breach(store, firm, "Drawdown 27%", config)

    out = None
    for bar in ("2026-08-29T06", "2026-08-29T07", "2026-08-29T08"):
        out = serve_sentence(store, _reload(store, firm), bar)
    assert out is not None and out.released is True
    assert _state(store, firm)["gulag_bars_left"] == 0


def test_no_bar_serves_no_time(store, firm):
    """A feed outage must not quietly serve a firm's sentence for it."""
    config = StrikeConfig(gulag_bars_first=3)
    record_breach(store, firm, "Drawdown 27%", config)
    for _ in range(20):
        serve_sentence(store, _reload(store, firm), "")
    assert _state(store, firm)["gulag_bars_left"] == 3


def test_the_second_strike_is_the_longer_sentence(store, firm):
    config = StrikeConfig(gulag_bars_first=20, gulag_bars_second=40)
    first = record_breach(store, firm, "Drawdown 27%", config)
    assert first.bars_left == 20
    clear_breach(store, _reload(store, firm))
    # Serve it off so the next strike starts from a clean sentence.
    store.release_from_gulag(firm.id)
    second = record_breach(store, _reload(store, firm), "Drawdown 24%", config)
    assert second.bars_left == 40
