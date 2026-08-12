"""The village running itself: the arena, the bazaar and the tavern.

Three quarters were on the map and in nothing that runs. These tests cover the
wiring that brought them into the tick, and — more importantly — the two lines
that wiring is not allowed to cross:

* it may not move cash, so it never files a capital transfer;
* it may not break a tick, so every quarter fails into a note rather than an
  exception.

The third property is determinism: replay the same market twice and the same
things happen on the same days, because the clock is the bar date rather than
the wall clock.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from src.trading.living import Living, LivingConfig, _clock, _roll


# =========================================================================
# config
# =========================================================================
def test_it_is_off_unless_asked_for(monkeypatch):
    """Autonomy you did not turn on is autonomy you did not agree to."""
    monkeypatch.delenv("TRADE_LIVING", raising=False)
    assert LivingConfig().enabled is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON"])
def test_the_switch_accepts_the_usual_spellings(monkeypatch, value):
    monkeypatch.setenv("TRADE_LIVING", value)
    assert LivingConfig().enabled is True


def test_a_nonsense_interval_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("TRADE_LIVING", "on")
    monkeypatch.setenv("TRADE_SEASON_EVERY", "not a number")
    assert LivingConfig().season_every == 10


# =========================================================================
# the clock
# =========================================================================
def test_the_clock_is_the_market_not_the_wall(monkeypatch):
    """A replay has to produce the same village, so the bar date is the clock."""
    from datetime import datetime, timezone

    class Feed:
        def as_of(self):
            return datetime(2026, 3, 1, 16, 0, tzinfo=timezone.utc)

    first = _clock(Feed())
    second = _clock(Feed())
    assert first == second == datetime(2026, 3, 1).date().toordinal()


def test_a_feed_with_no_bars_is_not_an_error():
    class Empty:
        def as_of(self):
            return None

    class Broken:
        def as_of(self):
            raise RuntimeError("no feed")

    assert _clock(Empty()) is None
    assert _clock(Broken()) is None


def test_the_roll_is_stable_and_spread():
    assert _roll(7, "ally", 100) == _roll(7, "ally", 100)
    assert _roll(7, "ally", 100) != _roll(7, "ally", 101)
    assert all(0.0 <= _roll(7, "x", n) < 1.0 for n in range(50))


# =========================================================================
# the tick
# =========================================================================
@pytest.fixture
def eco(ecosystem, monkeypatch):
    return ecosystem


def _living(eco):
    """Turn the village on for an ecosystem the fixture built with it off."""
    eco.config = replace(eco.config, living=LivingConfig())
    eco._living = None
    return eco


def test_nothing_happens_when_it_is_off(eco, monkeypatch):
    monkeypatch.delenv("TRADE_LIVING", raising=False)
    report = eco.tick()
    assert report.village == []


def test_the_quarters_wake_up_when_it_is_on(eco, monkeypatch):
    """Over a stretch of days at least one of the three should do something."""
    monkeypatch.setenv("TRADE_LIVING", "on")
    _living(eco)

    reports = eco.simulate(40)
    happenings = [line for r in reports for line in r.village]
    assert happenings, "40 days and the village never did anything"


def test_a_broken_quarter_does_not_break_the_tick(eco, monkeypatch):
    """Decoration that can take the system down is not decoration."""
    monkeypatch.setenv("TRADE_LIVING", "on")
    living = Living(eco)
    living.living = LivingConfig()

    class Report:
        village: list = []

    report = Report()
    report.village = []

    def explode(*_args):
        raise RuntimeError("the bazaar caught fire")

    living._guard("bazaar", report, explode)
    assert report.village == ["bazaar: RuntimeError: the bazaar caught fire"]


# =========================================================================
# the line it may not cross
# =========================================================================
def test_it_never_lists_or_buys_capital(eco, monkeypatch):
    """Cash assets stop at the gate. The village does not queue work there.

    A capital listing is not forbidden — a human may still create one, and it
    still requests approval before a dollar moves. What is forbidden is the
    village filing those requests on its own, which would turn the gate from a
    decision into an inbox to be cleared.
    """
    monkeypatch.setenv("TRADE_LIVING", "on")
    _living(eco)

    eco.simulate(40)

    listings = eco.black_market.listings(status="active") + eco.black_market.listings(
        status="sold"
    )
    assert listings == [] or all(l.currency == "tokens" for l in listings)
    assert all(l.asset_type != "capital" for l in listings)


def test_the_gate_gains_nothing_from_the_village(eco, monkeypatch):
    """No approval request in the ledger may name a market transfer."""
    monkeypatch.setenv("TRADE_LIVING", "on")
    _living(eco)

    eco.simulate(40)

    # There may legitimately be no requests at all here — two firms over forty
    # days need not earn a raise or trip a kill. The claim under test is the
    # negative one: whatever is in the gate, none of it came from the bazaar.
    rows = eco.db.query("SELECT action, reason, details FROM human_approvals") or []
    assert not any(
        "capital_transfer" in str(row.get("details") or "")
        or "capital_transfer" in str(row.get("reason") or "")
        for row in rows
    )


def test_the_tavern_writes_only_where_it_is_allowed(eco, monkeypatch):
    """The sandbox guard is the real enforcement; this is the proof it holds."""
    monkeypatch.setenv("TRADE_LIVING", "on")
    _living(eco)

    eco.simulate(40)

    # Alliances and espionage may well have happened. None of it may claim to
    # have changed anything real — `real_effect` is the sandbox's own record of
    # that promise, and the guard in sandbox/guard.py is what enforces it.
    events = eco.db.query("SELECT * FROM sandbox_events") or []
    assert all(not row.get("real_effect") for row in events)


# =========================================================================
# determinism
# =========================================================================
def test_two_runs_of_the_same_history_agree(tmp_path, firms_yaml, notifier, monkeypatch):
    """Same seed, same bars, same village."""
    monkeypatch.setenv("TRADE_LIVING", "on")

    def village_of(name):
        from src.config import Config
        from src.db import Database
        from src.trading.config import DataConfig, TradingConfig
        from src.trading.ecosystem import Ecosystem

        db = Database.from_url(f"sqlite:///{tmp_path / name}")
        db.init_schema()
        config = TradingConfig(
            firms_config=firms_yaml,
            audit_vault=tmp_path / f"vault-{name}",
            vendor_dir=tmp_path / f"vendor-{name}",
            data=DataConfig(source="synthetic", seed=12345, history_days=180),
            living=LivingConfig(),
        )
        eco = Ecosystem(
            db, config,
            Config(database_url=db.url, notification_log=tmp_path / "n.log"),
            notifier,
        )
        eco.init_firms()
        out = [line for r in eco.simulate(30) for line in r.village]
        db.close()
        return out

    assert village_of("one.db") == village_of("two.db")


# =========================================================================
# money never touches a float
# =========================================================================
def test_the_token_floor_is_a_decimal():
    from src.trading.living import TOKEN_FLOOR

    assert isinstance(TOKEN_FLOOR, Decimal)
