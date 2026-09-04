"""Two ways the village traded things it could not trade.

Both found on 2026-09-04 by looking at the walk-the-village page overnight and
asking how a firm was selling EFA with the US market shut.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.trading.models import Bar, Position


# =========================================================================
# 1. a price nobody has traded on for hours
# =========================================================================
def _md(bars_by_symbol):
    from src.trading.data.market_data import MarketData

    md = MarketData.__new__(MarketData)
    md._series_cache = dict(bars_by_symbol)
    md._cursor = -1
    md.unpriceable = {}
    md.symbols = list(bars_by_symbol)
    md._series = lambda s: bars_by_symbol.get(s.upper(), [])
    return md


def _bar(when, close="100"):
    return Bar(symbol="X", as_of=when, open=Decimal(close), high=Decimal(close),
               low=Decimal(close), close=Decimal(close), volume=Decimal("1"))


def test_a_bar_older_than_the_limit_is_stale():
    """The check `lagging` structurally cannot make.

    `lagging` asks "is this behind its peers?", so when the whole equity market
    is shut and every equity is equally stale, nothing is behind anything and
    nothing is flagged. Measured at 06:22 UTC on 2026-09-04 it flagged only
    JNJ while EFA was 9.69h old and being traded at a price unchanged since the
    previous close.
    """
    now = datetime(2026, 9, 4, 6, 22, tzinfo=timezone.utc)
    md = _md({
        "EFA": [_bar(now - timedelta(hours=9.7))],      # yesterday's close
        "SOL-USD": [_bar(now - timedelta(minutes=11))],  # crypto, still live
    })
    stale = md.stale(now=now)
    assert "EFA" in stale, "a 9.7h-old equity bar must not be tradeable"
    assert "SOL-USD" not in stale, "a live crypto bar must stay tradeable"
    assert stale["EFA"] == pytest.approx(9.7, abs=0.05)


def test_extended_hours_trading_stays_open():
    """The user's requirement: this is a freshness rule, not a clock.

    A symbol printing in the pre-market or after-hours has a fresh bar and must
    never reach the stale branch, whatever the time of day.
    """
    premarket = datetime(2026, 9, 4, 9, 30, tzinfo=timezone.utc)   # 05:30 ET
    md = _md({"SPY": [_bar(premarket - timedelta(minutes=20))]})
    assert md.stale(now=premarket) == {}, (
        "a symbol with a 20-minute-old bar is tradeable at any hour"
    )


def test_a_thin_etf_inside_a_session_is_not_punished():
    """The threshold that muted `firm_h_global`'s whole universe once already.

    A thin ETF can go three hours without a print in a normal session. Six
    hours clears that and still catches an overnight by a wide margin.
    """
    now = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)
    md = _md({"VGK": [_bar(now - timedelta(hours=3))]})
    assert md.stale(now=now) == {}


# =========================================================================
# 2. a holding too small to sell, sold forever
# =========================================================================
def test_dust_is_not_an_open_position():
    """`firm_a_etf_ii_v` held 0.00000024 EFA and sold half of it every bar —
    3.78e-06, 1.89e-06, 9.4e-07, 4.7e-07, 2.4e-07 — each fill moving zero cash
    and zero fee, each counted as a trade. Halving never reaches zero."""
    dust = Position(firm_id=1, symbol="EFA", quantity=Decimal("2.3e-07"),
                    avg_price=Decimal("107.26"))
    assert not dust.is_open, "a holding worth 0.000026 of a cent is flat"


def test_a_real_position_is_still_open():
    for qty in ("0.000001", "0.001", "1.5", "-5"):
        p = Position(firm_id=1, symbol="SPY", quantity=Decimal(qty),
                     avg_price=Decimal("100"))
        assert p.is_open, f"{qty} is a real position"


def test_dust_is_flat_in_both_directions():
    short = Position(firm_id=1, symbol="SPY", quantity=Decimal("-2.3e-07"),
                     avg_price=Decimal("100"))
    assert not short.is_open
