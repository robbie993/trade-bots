"""The scribe reads the dead, and refuses to speak on one firm's word.

Six postmortems exist on the real ledger and every one of them names a worst
symbol. No symbol is named twice. So the honest output today is silence with a
reason — and the test that matters most here is the one asserting it stays
silent, because a scribe that manufactured confident lessons from a sample its
own sources call too small would be the exact failure those sources warn about,
wearing the authority of having read them.
"""

import json
from decimal import Decimal

import pytest

from src.money import D
from src.trading.models import FirmRecord
from src.trading.scribe import MIN_CORROBORATION, Verdict, read_the_dead


def _bankrupt(store, firm_key, symbol, net, reason="Drawdown 27%"):
    firm = store.upsert_firm(FirmRecord(
        firm_key=firm_key, allocation=Decimal("1000"),
        initial_allocation=Decimal("1000"), universe=[symbol],
    ))
    store.db.insert("trade_memory", {
        "firm_id": firm.id,
        "symbol": "",
        "memory_type": "bankruptcy",
        "summary": f"{firm_key} went bankrupt: {reason}",
        "payload": json.dumps({
            "kill_reason": reason,
            "worst_symbols": [{"symbol": symbol, "net": str(net), "closed": 3}],
        }),
        "outcome": "loss",
        "reward": str(net),
    })
    return firm


def test_one_firms_loss_is_not_a_pattern(store):
    """The property the whole module exists for."""
    _bankrupt(store, "alpha", "WIF-USD", -400)
    verdict = read_the_dead(store)
    assert verdict.lessons_read == 1
    assert verdict.readings == [], "one firm's bad run must not become a warning"
    assert "WIF-USD" in verdict.withheld
    assert any("only one bankrupt firm" in n for n in verdict.notes)


def test_two_independent_firms_are_the_beginning_of_evidence(store):
    _bankrupt(store, "alpha", "WIF-USD", -400)
    _bankrupt(store, "beta", "WIF-USD", -250)
    verdict = read_the_dead(store)
    assert len(verdict.readings) == 1
    reading = verdict.readings[0]
    assert reading.symbol == "WIF-USD"
    assert reading.score < 0, "a name that killed two firms reads bearish"
    assert reading.confidence > 0


def test_the_scribe_never_sounds_certain(store):
    """Survivorship-shaped evidence about failures is not a forecast."""
    for n in range(6):
        _bankrupt(store, f"firm_{n}", "WIF-USD", -400)
    verdict = read_the_dead(store)
    assert verdict.readings
    assert verdict.readings[0].confidence <= D(35), (
        "six dead firms is still not certainty; the cap exists on purpose"
    )


def test_one_firm_losing_repeatedly_still_counts_once(store):
    """The question is how many strategies a name hurt, not how often.

    Otherwise a single firm that traded one symbol badly forty times would
    look like forty independent confirmations of the same lesson.
    """
    firm = _bankrupt(store, "alpha", "WIF-USD", -400)
    for extra in (-100, -200, -300):
        store.db.insert("trade_memory", {
            "firm_id": firm.id, "symbol": "", "memory_type": "bankruptcy",
            "summary": "again", "outcome": "loss", "reward": str(extra),
            "payload": json.dumps(
                {"worst_symbols": [{"symbol": "WIF-USD", "net": str(extra)}]}
            ),
        })
    verdict = read_the_dead(store)
    assert verdict.readings == [], "one firm cannot corroborate itself"


def test_a_symbol_that_made_money_is_not_a_warning(store):
    _bankrupt(store, "alpha", "NVDA", 500)
    _bankrupt(store, "beta", "NVDA", 300)
    verdict = read_the_dead(store)
    assert verdict.readings == []


def test_no_bankruptcies_is_silence_not_an_error(store):
    verdict = read_the_dead(store)
    assert verdict.lessons_read == 0
    assert verdict.readings == []
    assert any("nothing to learn from yet" in n for n in verdict.notes)
