"""A dead firm gets one successor, however many times it is wound up.

Written after 2026-09-01, when the village went from 17 firms to 46. Twenty-two
of those were unfunded heirs with no fills and no kill reason — whole lineages
(`firm_h_global_iii` through `_vi`) that had never traded and never could,
because an heir is filed broke and only a human can fund one.

`file_successor` documented the guard that would have prevented it — "Returns
None when an heir already exists" — but implemented the opposite: it asked
`_successor_key` for a name and gave up only when the *names* ran out, and that
helper returns the first unused suffix. So `_ii` existing produced `_iii`, then
`_iv`, `_v`, `_vi`, five deep, and stopped only because the list ended.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.trading.firms.bankruptcy import _successor_key, file_successor
from src.trading.models import FirmRecord


class _Store:
    """Just enough store to file an heir against."""

    def __init__(self, firms):
        self._firms = list(firms)
        self.created = []

    def firms(self):
        return list(self._firms)

    def upsert_firm(self, record):
        record.id = len(self._firms) + 1
        self._firms.append(record)
        self.created.append(record)
        return record


class _PM:
    realized = Decimal("-100")
    costs = Decimal("5")
    notes = ["lost money"]


@pytest.fixture
def dead():
    return FirmRecord(id=1, firm_key="firm_x", name="X", asset_class="Equities",
                      allocation=Decimal("0"), cash=Decimal("0"),
                      universe=["SPY"], genome={"a": 1})


def test_the_first_wind_up_files_an_heir(dead):
    store = _Store([dead])
    heir = file_successor(store, dead, _PM(), "a lesson", ["technical"])
    assert heir is not None
    assert heir.firm_key == "firm_x_ii"
    assert Decimal(heir.allocation) == 0, "an heir is filed broke"


def test_winding_the_same_firm_up_again_files_nothing(dead):
    """The bug: this used to return firm_x_iii, then _iv, _v, _vi."""
    store = _Store([dead])
    first = file_successor(store, dead, _PM(), "a lesson", ["technical"])
    assert first is not None
    for _ in range(5):
        assert file_successor(store, dead, _PM(), "a lesson", ["technical"]) is None
    assert len(store.created) == 1, f"bred a queue: {[f.firm_key for f in store.created]}"


def test_the_heir_records_who_it_came_from(dead):
    """The guard reads this, so it is load-bearing rather than decoration."""
    store = _Store([dead])
    heir = file_successor(store, dead, _PM(), "a lesson", ["technical"])
    assert (heir.genome or {}).get("inherited_from") == "firm_x"


def test_a_different_firm_still_gets_its_own_heir(dead):
    """The guard is per-parent, not a global stop on succession."""
    other = FirmRecord(id=2, firm_key="firm_y", name="Y", asset_class="Equities",
                       allocation=Decimal("0"), cash=Decimal("0"),
                       universe=["QQQ"], genome={})
    store = _Store([dead, other])
    assert file_successor(store, dead, _PM(), "l", ["technical"]) is not None
    assert file_successor(store, other, _PM(), "l", ["technical"]) is not None
    assert [f.firm_key for f in store.created] == ["firm_x_ii", "firm_y_ii"]


def test_successor_key_still_avoids_collisions():
    """Naming is still naming — an heir must not reuse a live key."""
    assert _successor_key("firm_x", set()) == "firm_x_ii"
    assert _successor_key("firm_x", {"firm_x_ii"}) == "firm_x_iii"
    assert _successor_key("firm_x", {"firm_x_ii", "firm_x_iii", "firm_x_iv",
                                     "firm_x_v", "firm_x_vi"}) == ""
