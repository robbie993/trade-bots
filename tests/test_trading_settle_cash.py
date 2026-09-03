"""Cash must be added to, never overwritten.

`settle` computed `new_cash = firm.cash + delta` from the in-memory record and
wrote it as an absolute. Two fills settled against one stale record therefore
produced two fill rows and one cash movement — the second overwrote the first —
and `cash = allocation + sum(cash_delta)` broke by exactly one fill.

Observed 2026-09-03: `firm_a_etf_ii_v` wrote two identical EFA fills of
-$400.28 in a single pass and the ledger came out $400.28 rich;
`firm_d_value_iii` did the same on VZ for $226.68. It is also the best
explanation for two earlier torn ledgers that were blamed on SIGKILL and on a
second process, and that survived the fixes for both.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.trading.models import Fill, FirmRecord, Side


@pytest.fixture
def firm(store):
    return store.upsert_firm(FirmRecord(
        firm_key="cash_test", name="Cash Test", asset_class="Equities",
        allocation=Decimal("10000"), cash=Decimal("10000"), universe=["SPY"],
    ))


def _buy(firm, price="100", qty="1"):
    return Fill(firm_id=firm.id, symbol="SPY", side=Side.BUY.value,
                quantity=Decimal(qty), price=Decimal(price), fee=Decimal("0"))


def test_two_fills_settled_against_one_stale_record_both_move_cash(store, firm):
    """The bug: the caller holds `firm` across both settles, so `firm.cash` is
    stale for the second. Both fills must still be charged."""
    stale = store.get_firm("cash_test")          # one record, used twice
    store.settle(stale, _buy(stale))
    store.settle(stale, _buy(stale))             # same object, cash now stale

    after = store.get_firm("cash_test")
    assert after.cash == Decimal("9800.00"), (
        f"two $100 buys should leave 9800, found {after.cash} — "
        "the second settle overwrote the first"
    )


def test_the_ledger_identity_holds_after_repeated_settles(store, firm):
    """cash == allocation + sum(cash_delta), which is what the reconciler checks."""
    stale = store.get_firm("cash_test")
    for _ in range(5):
        store.settle(stale, _buy(stale))

    after = store.get_firm("cash_test")
    deltas = sum((f.cash_delta for f in store.fills(after.id)), Decimal("0"))
    assert after.cash == Decimal(after.allocation) + deltas, (
        f"identity broken: cash {after.cash} != allocation {after.allocation} "
        f"+ deltas {deltas}"
    )


def test_a_fresh_record_still_settles_correctly(store, firm):
    """The ordinary path — re-reading between fills — must be unchanged."""
    for _ in range(3):
        current = store.get_firm("cash_test")
        store.settle(current, _buy(current))
    after = store.get_firm("cash_test")
    assert after.cash == Decimal("9700.00")
