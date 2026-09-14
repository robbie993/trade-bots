"""When the market is actually open, and what it really costs to trade then.

The village may trade whenever it likes. This module does not forbid anything —
it tells the truth about the conditions, and lets the consequences do the
teaching. Two facts it exists to stop the ledger getting wrong:

**At 2am on a Tuesday, no venue will sell you AMZN.** US equities have no
session at all between 20:00 and 04:00 ET, none on weekends, and none on ten
holidays a year. Recording an equity fill in that window is not a bold trade,
it is a trade that could not have happened, and the profit booked on it is
invented. Crypto is genuinely different — it trades every hour of every day —
so the answer depends on the instrument, not on a blanket rule.

**The cost of trading is not one number.** The village charged a flat 7 bps a
side everywhere. Measured against SIP quotes at the village's own entry
timestamps on 2026-09-12, the real half-spread was 0.82 bps in regular hours,
1.55 pre-market and 5.14 after hours — a 6x spread between sessions that the
flat constant erased completely. That erasure had a direction: it made the
expensive hours look exactly as cheap as the cheap ones, so nothing in the
village ever had a reason to prefer a liquid market.

So a firm that wants to trade at 2am still can, in anything that is open at
2am, and it pays what 2am actually costs. That is a market rule, not a rule of
ours, and it is the difference between a village that learns the shape of the
day and one that is merely banned from half of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from ..money import D

try:                                    # 3.9+, and the village runs 3.14
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover - no tzdata
    EASTERN = None

#: The four states a US equity market can be in.
CLOSED, PRE, REGULAR, AFTER = "closed", "pre", "regular", "after"

#: Session boundaries in Eastern time. Extended hours are real sessions with
#: real (worse) prices, not a grey area — 04:00 and 20:00 are the outer walls.
PRE_OPEN, REGULAR_OPEN = time(4, 0), time(9, 30)
REGULAR_CLOSE, AFTER_CLOSE = time(16, 0), time(20, 0)
#: On a half day the bell rings at 13:00 and the after session ends at 17:00.
EARLY_CLOSE, EARLY_AFTER_CLOSE = time(13, 0), time(17, 0)

#: Measured half-spread per side, by session, from SIP quotes at the village's
#: own fill timestamps (2026-09-12, 14 liquid symbols, n=86). These are what
#: the village pays to cross; the fee is charged on top.
HALF_SPREAD_BPS = {
    REGULAR: D("0.82"),
    PRE: D("1.55"),
    AFTER: D("5.14"),
    #: No quote means no fill, so this is only reached if a caller insists on
    #: pricing one anyway. It is deliberately punitive rather than plausible:
    #: an invented price should never look cheap.
    CLOSED: D("50.0"),
}


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm. Good Friday is a market holiday and it
    moves, so it cannot be a fixed date in a table."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f, g = (b + 8) // 25, (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth given weekday of a month; n=-1 means the last one."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 \
        else date(year, 12, 31)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """A holiday on a Saturday is taken on the Friday, a Sunday on the Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def holidays(year: int) -> set:
    """The ten days the US equity market is shut, for any year.

    Computed rather than tabulated: a hardcoded table silently expires, and
    the failure mode is a village trading happily through Thanksgiving.
    """
    return {
        _observed(date(year, 1, 1)),                     # New Year's Day
        _nth_weekday(year, 1, 0, 3),                     # MLK Jr Day
        _nth_weekday(year, 2, 0, 3),                     # Washington's Birthday
        _easter(year) - timedelta(days=2),               # Good Friday
        _nth_weekday(year, 5, 0, -1),                    # Memorial Day
        _observed(date(year, 6, 19)),                    # Juneteenth
        _observed(date(year, 7, 4)),                     # Independence Day
        _nth_weekday(year, 9, 0, 1),                     # Labor Day
        _nth_weekday(year, 11, 3, 4),                    # Thanksgiving
        _observed(date(year, 12, 25)),                   # Christmas
    }


def early_closes(year: int) -> set:
    """Half days. The bell rings at 13:00 ET and the tape is thin all session."""
    out = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}   # day after Thanksgiving
    for day in (date(year, 7, 3), date(year, 12, 24)):
        if day.weekday() < 5 and day not in holidays(year):
            out.add(day)
    return out


def is_crypto(symbol: str) -> bool:
    """Crypto never closes, which is the whole reason the village can trade at
    2am at all. The village spells it `DOGE-USD`."""
    return "-USD" in (symbol or "").upper()


def session_of(when: datetime, symbol: str = "") -> str:
    """Which session `when` falls in for `symbol`.

    `when` is treated as UTC when it carries no timezone, because every
    timestamp the village stores is UTC.
    """
    if is_crypto(symbol):
        return REGULAR                  # always on, always one price regime
    if EASTERN is None:                 # pragma: no cover - no tzdata installed
        return REGULAR
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    et = when.astimezone(EASTERN)
    day, clock = et.date(), et.time()
    if day.weekday() >= 5 or day in holidays(day.year):
        return CLOSED
    early = day in early_closes(day.year)
    close = EARLY_CLOSE if early else REGULAR_CLOSE
    after_close = EARLY_AFTER_CLOSE if early else AFTER_CLOSE
    if clock < PRE_OPEN or clock >= after_close:
        return CLOSED
    if clock < REGULAR_OPEN:
        return PRE
    if clock < close:
        return REGULAR
    return AFTER


def is_open(when: datetime, symbol: str = "") -> bool:
    """Could this have been traded at all? Extended hours count as open."""
    return session_of(when, symbol) != CLOSED


def cost_bps(when: datetime, symbol: str = "", fee_bps: Decimal = D(0)) -> Decimal:
    """What one side of a trade really costs at this moment, in basis points.

    Half the quoted spread plus the fee — the price of crossing once. Double it
    for a round trip.
    """
    return HALF_SPREAD_BPS[session_of(when, symbol)] + D(fee_bps)


@dataclass
class Window:
    """A human-readable answer to 'what is the market doing right now?'"""

    session: str
    symbol: str
    when: datetime

    @property
    def open(self) -> bool:
        return self.session != CLOSED

    def describe(self) -> str:
        if is_crypto(self.symbol):
            return f"{self.symbol}: open (crypto trades continuously)"
        word = {CLOSED: "closed", PRE: "pre-market", REGULAR: "regular hours",
                AFTER: "after hours"}[self.session]
        cost = HALF_SPREAD_BPS[self.session]
        if self.session == CLOSED:
            return f"{self.symbol}: closed — no venue is quoting"
        return f"{self.symbol}: {word}, ~{float(cost):.2f} bps a side to cross"


def window(when: datetime, symbol: str = "") -> Window:
    return Window(session=session_of(when, symbol), symbol=symbol, when=when)


__all__ = ["CLOSED", "PRE", "REGULAR", "AFTER", "HALF_SPREAD_BPS", "Window",
           "cost_bps", "early_closes", "holidays", "is_crypto", "is_open",
           "session_of", "window"]
