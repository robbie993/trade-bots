"""The real rules of the market.

Written because the village booked equity fills at 2am — 67% of sampled
after-hours equity entries had no two-sided SIP quote within three minutes,
and after-hours was 556 of 1,104 equity entries. Those fills recorded a price
nobody was offering, and the ledger reported profit on them.

The fix is not a ban. A firm may still want to trade at 2am, and in crypto it
can. It just has to be told the truth about what is open and what crossing
costs when it is.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from src.trading.session import (
    AFTER, CLOSED, PRE, REGULAR, cost_bps, early_closes, holidays, is_crypto,
    is_open, session_of, window,
)


def _utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# =========================================================================
# the 2am question
# =========================================================================
def test_no_equity_venue_is_open_at_2am():
    """07:00 UTC is 02:00 ET — outside even the pre-market session."""
    assert session_of(_utc(2026, 9, 15, 7), "AMZN") == CLOSED
    assert not is_open(_utc(2026, 9, 15, 7), "AMZN")


def test_crypto_is_open_at_2am_because_crypto_never_closes():
    assert is_open(_utc(2026, 9, 15, 7), "DOGE-USD")
    assert session_of(_utc(2026, 9, 15, 7), "DOGE-USD") == REGULAR
    assert is_crypto("DOGE-USD") and not is_crypto("AMZN")


def test_the_whole_equity_day_in_order():
    """13:30 UTC = 09:30 ET, the opening bell, on a plain Tuesday."""
    day = (2026, 9, 15)
    assert session_of(_utc(*day, 8), "AMZN") == PRE          # 04:00 ET
    assert session_of(_utc(*day, 13, 29), "AMZN") == PRE     # 09:29 ET
    assert session_of(_utc(*day, 13, 30), "AMZN") == REGULAR # 09:30 ET
    assert session_of(_utc(*day, 19, 59), "AMZN") == REGULAR # 15:59 ET
    assert session_of(_utc(*day, 20), "AMZN") == AFTER       # 16:00 ET
    assert session_of(_utc(*day, 23, 59), "AMZN") == AFTER   # 19:59 ET
    assert session_of(_utc(2026, 9, 16, 0), "AMZN") == CLOSED  # 20:00 ET


def test_the_market_is_shut_at_the_weekend():
    # 2026-09-12 is a Saturday, 2026-09-13 a Sunday.
    assert date(2026, 9, 12).weekday() == 5
    assert session_of(_utc(2026, 9, 12, 15), "AMZN") == CLOSED
    assert session_of(_utc(2026, 9, 13, 15), "AMZN") == CLOSED
    assert is_open(_utc(2026, 9, 12, 15), "BTC-USD"), "crypto trades weekends"


# =========================================================================
# holidays — computed, because a hardcoded table expires silently
# =========================================================================
def test_the_ten_holidays_land_on_the_right_days():
    h = holidays(2026)
    assert len(h) == 10
    assert date(2026, 1, 1) in h                   # New Year's Day
    assert date(2026, 1, 19) in h                  # MLK, 3rd Monday
    assert date(2026, 2, 16) in h                  # Washington, 3rd Monday
    assert date(2026, 5, 25) in h                  # Memorial, last Monday
    assert date(2026, 6, 19) in h                  # Juneteenth
    assert date(2026, 7, 3) in h                   # Jul 4 is a Saturday
    assert date(2026, 9, 7) in h                   # Labor Day, 1st Monday
    assert date(2026, 11, 26) in h                 # Thanksgiving, 4th Thursday
    assert date(2026, 12, 25) in h                 # Christmas


def test_good_friday_moves_and_is_still_caught():
    """Easter 2026 is April 5, so Good Friday is April 3."""
    assert date(2026, 4, 3) in holidays(2026)
    assert date(2027, 3, 26) in holidays(2027)     # Easter 2027 is March 28
    assert session_of(_utc(2026, 4, 3, 15), "SPY") == CLOSED


def test_a_holiday_on_a_weekend_is_observed_on_a_weekday():
    """July 4 2026 falls on a Saturday, so the market shuts on the Friday."""
    assert date(2026, 7, 4).weekday() == 5
    assert date(2026, 7, 3) in holidays(2026)
    assert date(2026, 7, 4) not in holidays(2026)
    # And 2027: Christmas Day is a Saturday, observed Friday the 24th.
    assert date(2027, 12, 24) in holidays(2027)


def test_the_market_is_shut_on_thanksgiving_but_open_the_next_morning():
    assert session_of(_utc(2026, 11, 26, 15), "SPY") == CLOSED
    assert session_of(_utc(2026, 11, 27, 15), "SPY") == REGULAR


def test_a_half_day_closes_at_one_not_four():
    """The day after Thanksgiving 2026 is Nov 27, and the bell rings at 13:00."""
    assert date(2026, 11, 27) in early_closes(2026)
    assert session_of(_utc(2026, 11, 27, 17, 59), "SPY") == REGULAR  # 12:59 ET
    assert session_of(_utc(2026, 11, 27, 18), "SPY") == AFTER        # 13:00 ET
    assert session_of(_utc(2026, 11, 27, 22, 1), "SPY") == CLOSED    # 17:01 ET


# =========================================================================
# what it costs, which is the part that teaches
# =========================================================================
def test_crossing_costs_six_times_more_after_hours_than_at_midday():
    """The flat 7 bps constant erased this, so nothing in the village had any
    reason to prefer a liquid market."""
    midday = cost_bps(_utc(2026, 9, 15, 15), "AMZN")
    evening = cost_bps(_utc(2026, 9, 15, 22), "AMZN")
    assert evening > midday * 5
    assert midday < Decimal(1), "regular hours are genuinely cheap"


def test_the_fee_is_charged_on_top_of_the_spread():
    bare = cost_bps(_utc(2026, 9, 15, 15), "AMZN")
    with_fee = cost_bps(_utc(2026, 9, 15, 15), "AMZN", fee_bps=Decimal(2))
    assert with_fee == bare + 2


def test_pricing_a_closed_market_is_punitive_rather_than_plausible():
    """Nothing should fill here at all. If a caller insists on a price anyway,
    an invented one must never look like a bargain."""
    shut = cost_bps(_utc(2026, 9, 15, 7), "AMZN")
    assert shut > cost_bps(_utc(2026, 9, 15, 22), "AMZN") * 5


def test_the_village_can_say_what_the_market_is_doing():
    assert "closed" in window(_utc(2026, 9, 15, 7), "AMZN").describe()
    assert "crypto" in window(_utc(2026, 9, 15, 7), "DOGE-USD").describe()
    assert window(_utc(2026, 9, 15, 15), "AMZN").open


def test_a_naive_timestamp_is_read_as_utc_not_local_time():
    """Every timestamp the village stores is UTC. Reading one as local time
    shifts the whole session map by hours, silently."""
    naive = datetime(2026, 9, 15, 7)
    assert session_of(naive, "AMZN") == session_of(_utc(2026, 9, 15, 7), "AMZN")


def test_daylight_saving_moves_the_bell_in_utc_terms():
    """14:30 UTC is the open in winter, 13:30 in summer. Hardcoding either one
    puts an hour of every day in the wrong session for half the year."""
    assert session_of(_utc(2026, 1, 14, 14, 30), "SPY") == REGULAR
    assert session_of(_utc(2026, 1, 14, 13, 30), "SPY") == PRE
    assert session_of(_utc(2026, 7, 14, 13, 30), "SPY") == REGULAR
