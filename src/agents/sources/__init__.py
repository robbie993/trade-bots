"""Product sources for the Scout.

`FixtureSource` is the one that works out of the box. The platform adapters
carry real payload mapping and credential handling; their network calls need
accounts this repository does not have. See src/agents/sources/aliexpress.py.
"""

from __future__ import annotations

from .aliexpress import AliExpressSource
from .fixture import FixtureSource
from .temu import TemuSource

__all__ = ["AliExpressSource", "FixtureSource", "TemuSource"]

#: Name -> class, used by `build_scout` to read MVV_SCOUT_SOURCE.
SOURCES = {
    "fixture": FixtureSource,
    "aliexpress": AliExpressSource,
    "temu": TemuSource,
}
