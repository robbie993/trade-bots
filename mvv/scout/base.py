"""Scout — spec component 1. Product discovery.

Discovery is the one part of the loop that costs nothing and risks nothing, so
it is fully autonomous (spec section 6). What it produces is a
`ProductCandidate`: a claim about a product, not a decision to sell it.

A source is anything with `.platform` and `.discover(limit)`. Adding a *new*
platform is a human-approved action (spec section 6) — `mvv.orchestrator`
checks the candidate's platform against `GateConfig.approved_platforms` before
anything is written to the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Protocol

from ..money import D, ZERO, money


class SourceNotConfigured(RuntimeError):
    """A source needs credentials or an SDK that this environment lacks."""


@dataclass
class ProductCandidate:
    """One product a source says exists, at a price it says it costs."""

    product_id: str
    product_name: str
    source_platform: str
    supplier: str = ""
    unit_cost: Decimal = ZERO
    shipping_cost: Decimal = ZERO
    selling_price: Decimal = ZERO  # 0 = let the calculator suggest one
    url: str = ""
    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.unit_cost = money(D(self.unit_cost))
        self.shipping_cost = money(D(self.shipping_cost))
        self.selling_price = money(D(self.selling_price))
        if not self.product_id:
            raise ValueError("a candidate needs a stable product_id for deduplication")


class ProductSource(Protocol):
    platform: str

    def discover(self, limit: int = 20) -> list[ProductCandidate]: ...


class Scout:
    """Runs one or more sources and de-duplicates what they return."""

    def __init__(self, sources: Optional[list] = None):
        self.sources = list(sources or [])

    def add_source(self, source) -> "Scout":
        self.sources.append(source)
        return self

    def discover(self, limit: int = 20) -> list[ProductCandidate]:
        seen: set[str] = set()
        out: list[ProductCandidate] = []
        for source in self.sources:
            try:
                candidates = source.discover(limit=limit)
            except SourceNotConfigured as exc:
                print(f"[scout] {getattr(source, 'platform', source)}: {exc}")
                continue
            except Exception as exc:  # a flaky scraper must not kill the loop
                print(f"[scout] {getattr(source, 'platform', source)} failed: {exc}")
                continue
            for candidate in candidates:
                key = f"{candidate.source_platform}:{candidate.product_id}"
                if key in seen:
                    continue
                seen.add(key)
                out.append(candidate)
        return out
