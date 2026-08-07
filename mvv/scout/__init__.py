"""Scout package — product discovery sources."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import Config
from .aliexpress import AliExpressSource
from .base import ProductCandidate, ProductSource, Scout, SourceNotConfigured
from .fixture import FixtureSource
from .temu import TemuSource

__all__ = [
    "AliExpressSource",
    "FixtureSource",
    "ProductCandidate",
    "ProductSource",
    "Scout",
    "SourceNotConfigured",
    "TemuSource",
    "build_scout",
]

SOURCES = {
    "fixture": FixtureSource,
    "aliexpress": AliExpressSource,
    "temu": TemuSource,
}


def build_scout(config: Optional[Config] = None) -> Scout:
    """Build the Scout from `MVV_SCOUT_SOURCE` (comma-separated names)."""
    cfg = config or Config()
    scout = Scout()
    for name in (n.strip().lower() for n in cfg.scout_source.split(",") if n.strip()):
        if name == "fixture":
            scout.add_source(FixtureSource(Path(cfg.scout_fixture_path)))
        elif name in SOURCES:
            scout.add_source(SOURCES[name]())
        else:
            raise ValueError(f"unknown scout source {name!r}; known: {sorted(SOURCES)}")
    return scout
