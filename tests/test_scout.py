"""Spec component 1 — the Scout and its sources."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.scout import Scout, SourceNotConfigured
from src.agents.sources import FixtureSource
from src.agents.sources.aliexpress import AliExpressSource
from src.agents.sources.temu import TemuSource


def test_fixture_source_reads_products(fixture_file):
    candidates = FixtureSource(fixture_file).discover()
    assert [c.product_id for c in candidates] == ["ae-1", "ae-2", "x-1"]
    assert candidates[0].unit_cost == Decimal("5.00")
    assert isinstance(candidates[0].unit_cost, Decimal)


def test_fixture_source_honours_the_limit(fixture_file):
    assert len(FixtureSource(fixture_file).discover(limit=2)) == 2


def test_missing_fixture_is_reported_not_crashed(tmp_path):
    with pytest.raises(SourceNotConfigured):
        FixtureSource(tmp_path / "nope.json").discover()


def test_scout_deduplicates_across_sources(fixture_file):
    scout = Scout([FixtureSource(fixture_file), FixtureSource(fixture_file)])
    assert len(scout.discover()) == 3


def test_a_broken_source_does_not_stop_the_others(fixture_file):
    class Exploding:
        platform = "boom"

        def discover(self, limit=20):
            raise RuntimeError("supplier site changed its HTML again")

    scout = Scout([Exploding(), FixtureSource(fixture_file)])
    assert len(scout.discover()) == 3


def test_unconfigured_source_is_skipped_quietly(fixture_file):
    scout = Scout([AliExpressSource(), FixtureSource(fixture_file)])
    assert len(scout.discover()) == 3


def test_candidates_require_a_product_id():
    from src.agents.scout import ProductCandidate

    with pytest.raises(ValueError):
        ProductCandidate(product_id="", product_name="x", source_platform="temu")


# -- adapter payload mapping (the part that is real without credentials) --
def test_aliexpress_parses_a_supplier_payload():
    candidate = AliExpressSource.parse_item(
        {
            "productId": "10050012",
            "product_title": "Magnetic cable",
            "target_sale_price": "3.10",
            "ship_to_price": "1.40",
            "store_name": "Yuxin",
            "product_detail_url": "https://example.invalid/p/1",
        }
    )
    assert candidate.product_id == "10050012"
    assert candidate.source_platform == "aliexpress"
    assert candidate.unit_cost == Decimal("3.10")
    assert candidate.shipping_cost == Decimal("1.40")
    assert candidate.supplier == "Yuxin"


def test_aliexpress_rejects_a_payload_with_no_id():
    with pytest.raises(ValueError):
        AliExpressSource.parse_item({"product_title": "mystery"})


def test_temu_converts_minor_units_to_dollars():
    candidate = TemuSource.parse_item(
        {"goodsId": 88213, "goodsName": "Sunset lamp", "supplierPrice": 640, "shippingFee": 200}
    )
    assert candidate.unit_cost == Decimal("6.40")
    assert candidate.shipping_cost == Decimal("2.00")
    assert candidate.source_platform == "temu"


def test_temu_prefers_explicit_dollar_fields_when_present():
    candidate = TemuSource.parse_item({"goodsId": 1, "unit_cost": "9.99", "supplierPrice": 111})
    assert candidate.unit_cost == Decimal("9.99")


def test_adapters_say_what_they_need(monkeypatch):
    for source in (AliExpressSource(), TemuSource()):
        with pytest.raises(SourceNotConfigured) as exc:
            source.fetch_raw()
        assert "fixture" in str(exc.value)


def test_a_transport_makes_an_adapter_live():
    source = AliExpressSource(
        transport=lambda limit: [{"product_id": "1", "title": "x", "price": "2.00"}]
    )
    candidates = source.discover()
    assert candidates[0].unit_cost == Decimal("2.00")


def test_malformed_items_are_skipped_not_fatal():
    source = TemuSource(
        transport=lambda limit: [{"no": "id"}, {"goodsId": "7", "supplierPrice": 500}]
    )
    candidates = source.discover()
    assert [c.product_id for c in candidates] == ["7"]
