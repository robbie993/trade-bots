"""The paper venue, once it knows what day it is.

The village booked 499 of 2,039 fills (24.5%) into a shut market and reported
P&L on every one. The venue had no way to know — it priced whatever it was
handed, at one flat spread, at any hour of any day.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.trading.config import DataConfig
from src.trading.execution.paper import MarketClosed, PaperVenue, _when
from src.trading.models import Side, TradeProposal


def _venue(bar="15m"):
    """An intraday venue. The session rules only apply where a timestamp
    names a moment rather than a whole day — see `session_aware`."""
    return PaperVenue(DataConfig(bar=bar))


def _proposal(symbol="AMZN", as_of="2026-09-15T15:00:00Z", side="buy"):
    return TradeProposal(firm_id=1, symbol=symbol, side=side,
                         quantity=Decimal(10), reference_price=Decimal(100),
                         as_of=as_of)


# =========================================================================
# the trade that could not have happened
# =========================================================================
def test_an_equity_fill_at_2am_is_refused():
    """07:00 UTC is 02:00 ET. No US venue is open, so there is nothing to
    fill against and no price to book."""
    with pytest.raises(MarketClosed) as exc:
        _venue().execute(_proposal(as_of="2026-09-15T07:00:00Z"))
    assert "closed" in str(exc.value)


def test_crypto_at_2am_fills_normally():
    """The rule is the market's, not ours. Crypto has no closing bell, so a
    2am decision there is a real trade and must go through."""
    fill = _venue().execute(
        _proposal(symbol="DOGE-USD", as_of="2026-09-15T07:00:00Z"))
    assert fill.price > 0


def test_a_weekend_equity_fill_is_refused():
    # 2026-09-12 is a Saturday.
    with pytest.raises(MarketClosed):
        _venue().execute(_proposal(as_of="2026-09-12T15:00:00Z"))


def test_a_holiday_fill_is_refused():
    """Thanksgiving 2026 is November 26, a weekday the market is shut."""
    with pytest.raises(MarketClosed):
        _venue().execute(_proposal(as_of="2026-11-26T15:00:00Z"))


def test_extended_hours_still_trade():
    """Pre- and after-hours are real sessions. Refusing them would be us
    restricting the village rather than the market doing it."""
    for stamp in ("2026-09-15T12:00:00Z", "2026-09-15T22:00:00Z"):
        assert _venue().execute(_proposal(as_of=stamp)).price > 0


# =========================================================================
# what it costs, which is the part that teaches
# =========================================================================
def test_crossing_after_hours_costs_more_than_at_midday():
    venue = _venue()
    midday = venue.execute(_proposal(as_of="2026-09-15T15:00:00Z"))
    evening = venue.execute(_proposal(as_of="2026-09-15T22:00:00Z"))
    assert evening.price > midday.price, (
        "a thin market must fill a buy worse, or nothing ever learns to "
        "prefer a liquid one"
    )
    assert evening.slippage > midday.slippage


def test_a_sell_is_hurt_in_the_other_direction():
    """Slippage must always work against the trader. If a wider spread helped
    sells, the village could earn a profit by trading at 3am."""
    venue = _venue()
    midday = venue.execute(_proposal(as_of="2026-09-15T15:00:00Z", side="sell"))
    evening = venue.execute(_proposal(as_of="2026-09-15T22:00:00Z", side="sell"))
    assert evening.price < midday.price


def test_the_session_cost_replaces_the_flat_constant_not_the_fee():
    venue = _venue()
    when = datetime(2026, 9, 15, 15, tzinfo=timezone.utc)
    assert venue.slippage_bps_for("AMZN", when) < venue.config.slippage_bps
    # and with no timestamp at all, the old constant still governs
    assert venue.slippage_bps_for("AMZN", None) == venue.config.slippage_bps


# =========================================================================
# reading the timestamp
# =========================================================================
def test_an_unreadable_timestamp_never_becomes_now():
    """Defaulting to `now` would price a weekend trade at Tuesday-lunchtime
    spreads and wave it straight through."""
    assert _when("not a date") is None
    assert _when("") is None
    assert _when(None) is None


def test_a_proposal_with_no_timestamp_keeps_the_old_behaviour():
    """Existing callers and the backtester must be untouched by this."""
    fill = _venue().execute(_proposal(as_of=""))
    assert fill.price > 0


def test_both_stamp_spellings_are_understood():
    z = _when("2026-09-15T07:00:00Z")
    spaced = _when("2026-09-15 07:00:00")
    assert z is not None and spaced is not None
    assert z.hour == spaced.hour == 7


# =========================================================================
# a daily bar is a date, not a moment
# =========================================================================
def test_a_daily_bar_is_not_read_as_a_midnight_trade():
    """Daily bars are stamped `2026-04-04 00:00:00+00:00`, which as an instant
    is 20:00 ET the evening before — shut. Applying the clock to them marked
    every daily bar closed and refused the whole backtest: 29 tests went red
    in one run, including a Saturday-stamped SPY bar.
    """
    daily = _venue(bar="1d")
    assert not daily.session_aware
    # a Saturday-stamped daily bar must still fill
    fill = daily.execute(_proposal(as_of="2026-04-04T00:00:00Z"))
    assert fill.price > 0


def test_a_daily_bar_is_charged_regular_hours_not_a_shut_market():
    """The trade is taken at the close, so it pays what the close costs."""
    from src.trading import session as market_session

    daily = _venue(bar="1d")
    charged = daily.slippage_bps_for("SPY", datetime(2026, 4, 4, tzinfo=timezone.utc))
    assert charged == market_session.HALF_SPREAD_BPS[market_session.REGULAR]


def test_an_intraday_venue_still_enforces_the_clock():
    """The daily exemption must not leak into intraday, which is where the
    499 impossible fills actually happened."""
    assert _venue(bar="15m").session_aware
    with pytest.raises(MarketClosed):
        _venue(bar="15m").execute(_proposal(as_of="2026-09-15T07:00:00Z"))
