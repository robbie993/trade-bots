"""Where the money went.

"We are losing money" is a feeling; this turns it into an identity with one
line per cause. The four causes look identical on an equity curve and want
completely different fixes, so the tests are about telling them apart —
and about not diagnosing anything at all from six trades.
"""

from __future__ import annotations

from decimal import Decimal

from src.trading import postmortem
from src.trading.postmortem import examine


def _fill(store, firm, symbol="SPY", pnl="0", fee="0", slippage="0"):
    store.db.insert("fills", {
        "firm_id": firm.id, "symbol": symbol, "side": "sell",
        "quantity": "1", "price": "100", "fee": str(fee),
        "slippage": str(slippage), "realized_pnl": str(pnl),
    })


# =========================================================================
# telling the four causes apart
# =========================================================================
def test_costs_are_named_when_turnover_is_eating_the_edge(store, firm_record):
    """The failure that arrives after a change nobody thought was risky.
    Trading twice as often pays twice the fees for the same edge."""
    for _ in range(30):
        _fill(store, firm_record, pnl="10", fee="4")
    pm = examine(store, firm_record)

    assert pm.cost_share >= postmortem.COST_ALARM_PCT
    assert any("COSTS ARE THE PROBLEM" in n for n in pm.notes)
    assert any("trade less often" in n for n in pm.notes)


def test_a_broken_exit_is_named_even_when_most_trades_win(store, firm_record):
    """The one better entries cannot repair. More than half the trades are
    profitable and the account still drains, because the losers are eight
    times the size of the winners."""
    for _ in range(20):
        _fill(store, firm_record, pnl="40")
    for _ in range(12):
        _fill(store, firm_record, pnl="-340")
    pm = examine(store, firm_record)

    assert pm.win_rate > Decimal(50), "most trades won"
    assert pm.payoff >= postmortem.PAYOFF_ALARM
    assert any("THE EXIT IS THE PROBLEM" in n for n in pm.notes)


def test_the_win_rate_is_judged_against_what_the_payoff_needs(store, firm_record):
    """"55% of trades win" means nothing until you know whether 55% is enough."""
    for _ in range(10):
        _fill(store, firm_record, pnl="50")
    for _ in range(15):
        _fill(store, firm_record, pnl="-100")
    pm = examine(store, firm_record)

    assert pm.win_rate < pm.breakeven_win_rate
    assert any("needs" in n and "break even" in n for n in pm.notes)


def test_one_bad_symbol_is_pointed_at(store, firm_record):
    """Narrowing the universe is cheaper than re-tuning the strategy."""
    for _ in range(20):
        _fill(store, firm_record, symbol="SPY", pnl="20")
    for _ in range(20):
        _fill(store, firm_record, symbol="DOGE-USD", pnl="-90")
    pm = examine(store, firm_record)

    assert pm.symbols[0].symbol == "DOGE-USD"
    assert any("DOGE-USD" in n for n in pm.notes)


def test_a_healthy_firm_is_not_given_a_disease(store, firm_record):
    for _ in range(40):
        _fill(store, firm_record, pnl="50", fee="1")
    for _ in range(20):
        _fill(store, firm_record, pnl="-40", fee="1")
    pm = examine(store, firm_record)

    assert not any("PROBLEM" in n for n in pm.notes)
    assert any("No single cause stands out" in n for n in pm.notes)


# =========================================================================
# and the rule the whole village runs on
# =========================================================================
def test_it_refuses_to_diagnose_below_the_sample_gate(store, firm_record):
    """A run of bad luck and a broken strategy look identical at six trades.
    A post-mortem that ignored the sample gate would be the loudest place in
    the village it got broken."""
    for _ in range(6):
        _fill(store, firm_record, pnl="-100")
    pm = examine(store, firm_record)

    assert any("below the sample gate" in n for n in pm.notes)
    assert any("description, not diagnosis" in n for n in pm.notes)


def test_a_firm_that_never_traded_has_no_loss_to_explain(store, firm_record):
    pm = examine(store, firm_record)
    assert pm.fills == 0
    assert pm.notes == ["Nothing has traded yet. There is no loss to explain."]


def test_the_identity_adds_up(store, firm_record):
    """Every line is read from the ledger. A post-mortem that disagrees with
    the books is a second opinion nobody asked for."""
    for _ in range(10):
        _fill(store, firm_record, pnl="100", fee="2", slippage="1")
    for _ in range(5):
        _fill(store, firm_record, pnl="-60", fee="2")
    pm = examine(store, firm_record)

    assert pm.gross_won == Decimal("1000.00")
    assert pm.gross_lost == Decimal("-300.00")
    assert pm.realized == pm.gross_won + pm.gross_lost
    assert pm.costs == Decimal("40.00")          # 10*(2+1) + 5*2
    assert pm.closed == 15 and pm.wins == 10 and pm.losses == 5
    assert len(postmortem.table(pm)) == 8


def test_slippage_counts_as_a_cost_not_a_rounding_error(store, firm_record):
    for _ in range(25):
        _fill(store, firm_record, pnl="10", fee="0", slippage="5")
    pm = examine(store, firm_record)
    assert pm.costs == Decimal("125.00")
    assert any("COSTS ARE THE PROBLEM" in n for n in pm.notes)
