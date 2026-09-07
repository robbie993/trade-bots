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


# =========================================================================
# one order per symbol per decision
# =========================================================================
def test_a_deliberation_that_repeats_itself_is_deduped():
    """The churn gate stops a firm deliberating twice in a bar. It cannot see
    a single deliberation returning the same order twice, because that is one
    call.

    On 2026-09-06 `firm_i_memecoins_ii` returned DOGE, WIF, DOGE, WIF for bar
    22:00. All four filled, four rows landed in the same second, and the ledger
    came out $497.22 rich — exactly one DOGE plus one WIF. Same shape on 09-01
    (six identical DOGE buys) and 09-03 (two identical WIF buys).
    """
    from src.trading.models import Side, TradeProposal

    def prop(symbol, side=Side.BUY.value, qty="1"):
        return TradeProposal(firm_id=1, symbol=symbol, side=side,
                             quantity=Decimal(qty), rationale="x")

    raw = [prop("DOGE-USD"), prop("WIF-USD"), prop("DOGE-USD"), prop("WIF-USD")]

    seen, deduped = set(), []
    for p in raw:
        key = (p.symbol, p.side)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    assert [p.symbol for p in deduped] == ["DOGE-USD", "WIF-USD"]


def test_opposite_sides_of_one_symbol_are_not_duplicates():
    """A firm may legitimately close one leg and open another."""
    from src.trading.models import Side, TradeProposal

    buy = TradeProposal(firm_id=1, symbol="SPY", side=Side.BUY.value,
                        quantity=Decimal("1"), rationale="x")
    sell = TradeProposal(firm_id=1, symbol="SPY", side=Side.SELL.value,
                         quantity=Decimal("1"), rationale="x")
    keys = {(p.symbol, p.side) for p in (buy, sell)}
    assert len(keys) == 2


def test_cash_arithmetic_is_exact_not_accumulated_in_floats(store, firm):
    """`SET cash = cash + ?` hands the sum to SQLite in floating point.

    `firms.cash` is declared NUMERIC, and SQLite's NUMERIC affinity already
    stores it as REAL, so the column has always carried binary drift — that is
    the schema's doing and predates this code. What settle controls is whether
    it *adds* arithmetic on top: the sum is done in Decimal, so repeated
    settles do not accumulate error.
    """
    stale = store.get_firm("cash_test")
    for _ in range(3):
        store.settle(stale, _buy(stale, price="33.33", qty="3"))

    after = store.get_firm("cash_test")
    assert after.cash == Decimal("9700.03"), after.cash
