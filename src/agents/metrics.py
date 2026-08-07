"""Metrics ingestion — the `fetch_traffic` / `fetch_orders` calls in spec 11.2.

Phase 1 has no ad-platform or storefront credentials, so there are two
providers and neither pretends to be an API client:

* `ManualMetricsProvider` — reads whatever the operator last typed via
  `mvv metrics`. This is the default, and it is a legitimate way to run Phase 1:
  copying seven numbers a day out of the ads dashboard is cheaper than an
  integration for a system whose whole point is to be killable.
* `FileMetricsProvider` — reads a JSON file keyed by product_id, so an export
  or a cron-dumped report can feed the loop unattended.

Both return a `MetricsSnapshot`. When a real Meta/TikTok/Shopify integration
arrives it implements the same three methods and nothing else changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional, Protocol

from ..money import D


@dataclass
class MetricsSnapshot:
    """Cumulative-to-date metrics for one experiment."""

    impressions: Optional[int] = None
    clicks: Optional[int] = None
    sessions: Optional[int] = None
    orders: Optional[int] = None
    revenue: Optional[Decimal] = None
    ad_spend: Optional[Decimal] = None
    refunds: Optional[int] = None
    chargebacks: Optional[int] = None
    avg_delivery_days: Optional[Decimal] = None

    def is_empty(self) -> bool:
        return all(getattr(self, f) is None for f in self.__dataclass_fields__)

    @classmethod
    def from_dict(cls, data: dict) -> "MetricsSnapshot":
        def num(key):
            return int(data[key]) if data.get(key) is not None else None

        def dec(key):
            return D(data[key]) if data.get(key) is not None else None

        return cls(
            impressions=num("impressions"),
            clicks=num("clicks"),
            sessions=num("sessions"),
            orders=num("orders"),
            revenue=dec("revenue"),
            ad_spend=dec("ad_spend"),
            refunds=num("refunds"),
            chargebacks=num("chargebacks"),
            avg_delivery_days=dec("avg_delivery_days"),
        )


class MetricsProvider(Protocol):
    def fetch(self, experiment) -> MetricsSnapshot: ...


@dataclass
class ManualMetricsProvider:
    """No-op provider: the ledger already holds what the operator entered."""

    name: str = "manual"

    def fetch(self, experiment) -> MetricsSnapshot:
        return MetricsSnapshot()


@dataclass
class FileMetricsProvider:
    """Reads `{product_id: {impressions: ..., ...}}` from a JSON file."""

    path: Path
    name: str = "file"

    def fetch(self, experiment) -> MetricsSnapshot:
        path = Path(self.path)
        if not path.exists():
            return MetricsSnapshot()
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"[metrics] {path} is not valid JSON: {exc}")
            return MetricsSnapshot()
        entry = data.get(experiment.product_id)
        return MetricsSnapshot.from_dict(entry) if entry else MetricsSnapshot()


@dataclass
class StaticMetricsProvider:
    """Test double: a dict of product_id -> MetricsSnapshot."""

    snapshots: dict

    def fetch(self, experiment) -> MetricsSnapshot:
        return self.snapshots.get(experiment.product_id, MetricsSnapshot())


def build_metrics_provider(name: str = "manual", path: Optional[Path] = None) -> MetricsProvider:
    if name == "file":
        if path is None:
            raise ValueError("file metrics provider needs a path")
        return FileMetricsProvider(Path(path))
    return ManualMetricsProvider()
