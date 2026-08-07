"""Temu source (via the Temu Open Cooperation Platform).

STATUS: adapter skeleton, not a working connector — same honesty note as
src/agents/sources/aliexpress.py. `parse_item` is real and tested; `fetch_raw` needs a
TOCP app key/secret and an access token that this repository does not have.

Supply a transport to make it live without editing this file:

    source = TemuSource(transport=lambda limit: my_tocp_client.goods_list(limit))
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from ...money import D
from ..scout import ProductCandidate, SourceNotConfigured


class TemuSource:
    platform = "temu"

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        transport: Optional[Callable[[int], list[dict]]] = None,
    ):
        self.app_key = app_key or os.environ.get("MVV_TEMU_APP_KEY", "")
        self.app_secret = app_secret or os.environ.get("MVV_TEMU_APP_SECRET", "")
        self.access_token = access_token or os.environ.get("MVV_TEMU_ACCESS_TOKEN", "")
        self.transport = transport

    @classmethod
    def parse_item(cls, item: dict) -> ProductCandidate:
        product_id = str(
            item.get("product_id") or item.get("goodsId") or item.get("skuId") or item.get("id") or ""
        ).strip()
        if not product_id:
            raise ValueError(f"temu payload has no product id: {item!r}")

        # TOCP quotes money in minor units (cents) on most goods endpoints.
        unit_cost = item.get("unit_cost")
        if unit_cost is None:
            minor = item.get("supplierPrice") or item.get("price") or item.get("goodsPrice")
            unit_cost = D(minor) / D(100) if minor is not None else 0
        shipping = item.get("shipping_cost")
        if shipping is None:
            minor = item.get("shippingFee")
            shipping = D(minor) / D(100) if minor is not None else 0

        return ProductCandidate(
            product_id=product_id,
            product_name=str(item.get("product_name") or item.get("goodsName") or ""),
            source_platform=cls.platform,
            supplier=str(item.get("supplier") or item.get("mallName") or item.get("mallId") or ""),
            unit_cost=D(unit_cost),
            shipping_cost=D(shipping),
            url=str(item.get("url") or item.get("goodsUrl") or ""),
            raw=item,
        )

    def discover(self, limit: int = 20) -> list[ProductCandidate]:
        out = []
        for item in self.fetch_raw(limit):
            try:
                out.append(self.parse_item(item))
            except (ValueError, KeyError) as exc:
                print(f"[scout:{self.platform}] skipping malformed item: {exc}")
        return out

    def fetch_raw(self, limit: int = 20) -> list[dict]:
        if self.transport is not None:
            return list(self.transport(limit))
        raise SourceNotConfigured(
            "Temu TOCP connector is not implemented. Set MVV_TEMU_APP_KEY, "
            "MVV_TEMU_APP_SECRET and MVV_TEMU_ACCESS_TOKEN and implement "
            "TemuSource.fetch_raw(), or pass transport=<callable returning list[dict]>. "
            "Until then use the fixture source (MVV_SCOUT_SOURCE=fixture)."
        )
