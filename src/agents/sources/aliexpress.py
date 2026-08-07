"""AliExpress source (via a DSers-style integration).

STATUS: adapter skeleton, not a working connector.

What is real here: the payload -> `ProductCandidate` mapping (`parse_item`),
which is unit-tested, and the credential handling. What is not: the network
call. `fetch_raw()` raises `SourceNotConfigured` until credentials are present
and a transport is supplied, because DSers' product API requires an
authenticated merchant account that this repository does not have, and a
scraper written against a page structure nobody has looked at would be a
plausible-looking lie rather than a component.

To finish it, do exactly one thing — implement `fetch_raw()` to return a list
of dicts from whichever endpoint your account can reach — and everything
downstream (dedup, calculator, ledger, gate) already works. Or pass
`transport=` a callable with the same contract and keep this file untouched.

    source = AliExpressSource(transport=my_dsers_client.search_products)

Adding a platform is a human-approved action (spec section 6): the
orchestrator will refuse candidates from a platform that is not in
MVV_APPROVED_PLATFORMS even if this adapter returns them.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from ...money import D
from ..scout import ProductCandidate, SourceNotConfigured


class AliExpressSource:
    platform = "aliexpress"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        transport: Optional[Callable[[int], list[dict]]] = None,
        keyword: str = "",
    ):
        self.api_key = api_key or os.environ.get("MVV_DSERS_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("MVV_DSERS_API_SECRET", "")
        self.transport = transport
        self.keyword = keyword or os.environ.get("MVV_SCOUT_KEYWORD", "")

    # -- the part that is real ------------------------------------------
    @classmethod
    def parse_item(cls, item: dict) -> ProductCandidate:
        """Map one supplier payload to a candidate.

        Field names follow the DSers/AliExpress open-platform vocabulary and
        fall back to plain snake_case, so a hand-assembled dict works too.
        """
        product_id = str(
            item.get("product_id") or item.get("productId") or item.get("id") or ""
        ).strip()
        if not product_id:
            raise ValueError(f"supplier payload has no product id: {item!r}")

        unit_cost = _first(
            item, ("unit_cost", "target_sale_price", "sale_price", "price", "min_price")
        )
        shipping = _first(item, ("shipping_cost", "ship_to_price", "logistics_cost"), default=0)
        return ProductCandidate(
            product_id=product_id,
            product_name=str(
                item.get("product_name") or item.get("product_title") or item.get("title") or ""
            ),
            source_platform=cls.platform,
            supplier=str(item.get("supplier") or item.get("store_name") or item.get("shop_id") or ""),
            unit_cost=D(unit_cost),
            shipping_cost=D(shipping),
            url=str(item.get("url") or item.get("product_detail_url") or ""),
            raw=item,
        )

    def discover(self, limit: int = 20) -> list[ProductCandidate]:
        items = self.fetch_raw(limit)
        out = []
        for item in items:
            try:
                out.append(self.parse_item(item))
            except (ValueError, KeyError) as exc:
                print(f"[scout:{self.platform}] skipping malformed item: {exc}")
        return out

    # -- the part that needs your credentials ----------------------------
    def fetch_raw(self, limit: int = 20) -> list[dict]:
        if self.transport is not None:
            return list(self.transport(limit))
        raise SourceNotConfigured(
            "AliExpress/DSers connector is not implemented. Set MVV_DSERS_API_KEY and "
            "MVV_DSERS_API_SECRET and implement AliExpressSource.fetch_raw(), or pass "
            "transport=<callable returning list[dict]>. Until then use the fixture "
            "source (MVV_SCOUT_SOURCE=fixture)."
        )


def _first(item: dict, keys: tuple, default=0):
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return default
