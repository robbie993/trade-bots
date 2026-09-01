"""How long a bar is, and everything that depends on knowing.

This module exists because the same mistake has now been made three times in
this repository, each time in a different place, and each time it looked like a
different bug:

* the **Sharpe kill** — annualising per-tick observations as though each were a
  trading day, which turned a firm with an 80% win rate into a confident
  negative number and killed it;
* the **bar count** — incrementing a counter once per tick and calling the
  result "bars measured on alpaca", which let two hours of uptime clear the
  check standing in front of real money;
* the **borrow charge** — billing ``rate / 365`` once per tick, which on a
  sixty-second loop charges a year of borrow every six hours.

They are one mistake: **treating elapsed wall-clock time, or the number of
times a loop went round, as though it were new information about the market.**

The fix is to have exactly one place that knows how long a bar is, and to make
every rate, every count and every annualisation ask it. Adding a resolution
means adding a row to ``RESOLUTIONS``; it does not mean auditing the codebase
for hidden 252s.

**On ``bars_per_day``.** These are US equity-session figures — 6.5 hours, 390
minutes. Crypto trades around the clock, so an hourly crypto series really has
24 bars a day rather than 6.5, and a mixed-universe village annualises crypto's
volatility about 2.3× too low. That is a real limitation and it is stated here
rather than hidden: the number is a property of the *session*, not of the feed,
and this system has one clock for all of its firms. ``TRADE_BARS_PER_DAY``
overrides it for a village trading only crypto.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..money import D

# Trading days in a year. The only place this constant belongs.
TRADING_DAYS = 252

# US equity session, in minutes. The basis for every intraday `bars_per_day`.
SESSION_MINUTES = 390


@dataclass(frozen=True)
class Resolution:
    """One bar size, and the conversions that follow from it."""

    name: str
    seconds: int
    #: Bars in one trading session. Fractional for hourly (6.5), by design.
    bars_per_day: Decimal
    #: What each provider calls this bar size.
    alpaca: str
    ccxt: str
    yahoo: str
    #: How many characters of an ISO timestamp identify one bar. A daily bar is
    #: `2026-08-13`; an hourly one needs `2026-08-13T14`; a minute bar needs the
    #: minutes too. This is what stops a thousand ticks looking like a thousand
    #: bars — see migration 021.
    key_len: int

    @property
    def is_intraday(self) -> bool:
        return self.seconds < 86_400

    @property
    def bars_per_year(self) -> Decimal:
        """The annualisation factor for anything computed per bar.

        Overridable, because a village trading nothing but crypto has a
        24-hour session and this default assumes an exchange that closes.
        """
        override = os.environ.get("TRADE_BARS_PER_DAY", "").strip()
        per_day = self.bars_per_day
        if override:
            try:
                per_day = D(override)
            except Exception:  # noqa: BLE001 - a bad override is not a crash
                per_day = self.bars_per_day
        return per_day * D(TRADING_DAYS)

    @property
    def calendar_days(self) -> Decimal:
        """The fraction of a calendar day one bar covers.

        Calendar, not trading: borrow, financing and interest accrue on the
        wall clock and do not care that the exchange is shut. A daily bar is
        one day; an hourly bar is a twenty-fourth of one.
        """
        return D(self.seconds) / D(86_400)

    def days_for_bars(self, bars: int) -> int:
        """Calendar days of history to request to get roughly `bars` of them.

        Padded for weekends and holidays on intraday resolutions, where asking
        for exactly the arithmetic answer reliably comes back short.
        """
        if bars <= 0:
            return 1
        sessions = D(bars) / self.bars_per_day
        return max(1, int(sessions * (D("1.5") if self.is_intraday else D(1))) + 2)

    def bar_key(self, as_of) -> str:
        """The string that identifies one bar. Empty when there is no bar.

        An unknown bar is never guessed at. Every caller of this treats the
        empty string as "record nothing", because a bar nobody can name is not
        evidence of anything.
        """
        if as_of is None:
            return ""
        text = getattr(as_of, "isoformat", lambda: str(as_of))()
        return str(text).replace(" ", "T")[: self.key_len]


RESOLUTIONS = {
    "1m":  Resolution("1m",     60, D(SESSION_MINUTES),      "1Min",  "1m", "1m", 16),
    "5m":  Resolution("5m",    300, D(SESSION_MINUTES) / 5,  "5Min",  "5m", "5m", 16),
    "15m": Resolution("15m",   900, D(SESSION_MINUTES) / 15, "15Min", "15m", "15m", 16),
    "1h":  Resolution("1h",   3600, D("6.5"),                "1Hour", "1h", "1h", 13),
    "1d":  Resolution("1d",  86_400, D(1),                   "1Day",  "1d", "1d", 10),
}

DAILY = RESOLUTIONS["1d"]

# What people actually type.
ALIASES = {
    "minute": "1m", "1min": "1m", "min": "1m",
    "5min": "5m", "15min": "15m",
    "hour": "1h", "hourly": "1h", "60m": "1h", "1hour": "1h",
    "day": "1d", "daily": "1d", "1day": "1d",
}


def parse(name: Optional[str]) -> Resolution:
    """Resolve a bar size by name. Unknown names fall back to daily.

    Deliberately forgiving rather than fatal: a typo in an environment variable
    should not stop a village that is holding positions. It falls back to the
    slowest, most conservative resolution, which is also the one every existing
    deployment is already on.
    """
    key = str(name or "").strip().lower()
    key = ALIASES.get(key, key)
    return RESOLUTIONS.get(key, DAILY)


def nearest(seconds: float) -> Optional[Resolution]:
    """The known resolution closest to an observed bar spacing.

    For telling what the data in front of you actually is, rather than what a
    config file says it should be. Those two disagreed twice in one afternoon —
    borrow billed at 1/96th of what was borrowed, and every Sharpe in the
    village silently `None` — because both readers took the constant on faith.

    Matched on log distance so "closest" means closest by ratio: 20 minutes is
    nearer 15m than 1h, and an hour of drift should not make daily bars look
    intraday. Returns `None` for a spacing no known resolution is within 2x of,
    which is the honest answer for data nobody configured.
    """
    if not seconds or seconds <= 0:
        return None
    best, best_ratio = None, None
    for resolution in RESOLUTIONS.values():
        ratio = max(seconds / resolution.seconds, resolution.seconds / seconds)
        if best_ratio is None or ratio < best_ratio:
            best, best_ratio = resolution, ratio
    return best if best_ratio is not None and best_ratio <= 2 else None


def from_env() -> Resolution:
    return parse(os.environ.get("TRADE_BAR", "1d"))


__all__ = [
    "ALIASES", "DAILY", "RESOLUTIONS", "Resolution", "SESSION_MINUTES",
    "TRADING_DAYS", "from_env", "nearest", "parse",
]
