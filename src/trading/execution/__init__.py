"""Execution venues.

``paper`` is the default and the only one that trades without a human. Every
other name here routes through ``live.LiveVenue``, which refuses to send an
order until a ``live_trading`` approval exists for that specific venue.
"""

from __future__ import annotations

from typing import Optional

from ..config import TradingConfig
from .live import (
    LIVE_VENUES,
    AlpacaVenue,
    BinanceVenue,
    LiveTradingNotApproved,
    LiveVenue,
    VenueNotConfigured,
    WebullVenue,
)
from .paper import PaperVenue, Quote


def build_venue(name: str, config: Optional[TradingConfig] = None, gate=None):
    """Resolve a venue by name. Unknown names raise rather than fall back.

    Falling back to paper would be the friendlier behaviour and the wrong one:
    an operator who typed `alpacca` should find out immediately, not discover
    a week of "live" trades were simulated.
    """
    cfg = config or TradingConfig()
    key = (name or "paper").strip().lower()
    if key == "paper":
        return PaperVenue(cfg.data)
    if key in LIVE_VENUES:
        return LIVE_VENUES[key](gate=gate)
    raise VenueNotConfigured(
        f"unknown venue {name!r}; expected paper, {', '.join(sorted(LIVE_VENUES))}"
    )


__all__ = [
    "AlpacaVenue",
    "BinanceVenue",
    "LIVE_VENUES",
    "LiveTradingNotApproved",
    "LiveVenue",
    "PaperVenue",
    "Quote",
    "VenueNotConfigured",
    "WebullVenue",
    "build_venue",
]
