"""Fixture source — products from a JSON file.

This is the source the loop runs on out of the box, and the one the tests use.
It exists so the whole system (calculator, ledger, gate, orchestrator, cash)
can be exercised end-to-end without credentials, without hitting a supplier's
servers, and without a scraper's flakiness masquerading as a business result.

It is also the honest way to run Phase 1 manually: paste products you found by
hand into `data/products.sample.json` and the rest of the machine treats them
exactly like scraped ones.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..scout import ProductCandidate, SourceNotConfigured


class FixtureSource:
    platform = "fixture"

    def __init__(self, path: Path, platform: Optional[str] = None):
        self.path = Path(path)
        if platform:
            self.platform = platform

    def discover(self, limit: int = 20) -> list[ProductCandidate]:
        if not self.path.exists():
            raise SourceNotConfigured(f"fixture file not found: {self.path}")
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError as exc:
            raise SourceNotConfigured(f"fixture file is not valid JSON: {exc}") from exc

        items = data["products"] if isinstance(data, dict) else data
        out = []
        for item in items[:limit]:
            out.append(
                ProductCandidate(
                    product_id=str(item["product_id"]),
                    product_name=item.get("product_name", ""),
                    source_platform=item.get("source_platform", self.platform),
                    supplier=item.get("supplier", ""),
                    unit_cost=item.get("unit_cost", 0),
                    shipping_cost=item.get("shipping_cost", 0),
                    selling_price=item.get("selling_price", 0),
                    url=item.get("url", ""),
                    raw=item,
                )
            )
        return out
