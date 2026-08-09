"""The black market — firms trading with each other.

Genomes, data and compute change hands for tokens and settle here. Capital
does not: a capital sale is a cut on one firm and a raise on another, and a
raise needs a human, so the market requests one and applies both legs together
once it is granted. Otherwise this module would be a way around the only gate
in the system that stops capital growing without a person signing for it.
"""

from __future__ import annotations

from .market import ASSET_TYPES, BlackMarket, CASH_ASSETS, Listing, MarketError, TOKEN_ASSETS

__all__ = [
    "ASSET_TYPES",
    "BlackMarket",
    "CASH_ASSETS",
    "Listing",
    "MarketError",
    "TOKEN_ASSETS",
]
