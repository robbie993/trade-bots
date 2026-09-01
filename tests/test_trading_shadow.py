"""The shadow options desk.

Every test here is a bug that was in this file and shipped. The desk had no
tests at all, which is how a desk that *structurally could not write a put*
ran for an hour looking merely idle.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from src.trading.data.options_feed import Quote
from src.trading.shadow import (
    GENE_DEFAULTS, ShadowDesk, _expiry_of, _seed, _strike_of,
)


def _quote(symbol: str, bid: str, ask: str) -> Quote:
    return Quote(symbol=symbol, bid=Decimal(bid), ask=Decimal(ask),
                 as_of=datetime.now(timezone.utc))


def _desk() -> ShadowDesk:
    desk = ShadowDesk.__new__(ShadowDesk)
    desk.genome = dict(GENE_DEFAULTS)
    desk._sd = Decimal("0.01")          # 1% daily, so 1sd on 100 is 1 point
    return desk


# =========================================================================
# reading an OCC symbol
# =========================================================================
def test_expiry_is_read_from_the_end_not_the_start():
    """Roots run one to six characters, so counting forwards gets SOFI wrong."""
    assert _expiry_of("SPY261016C00773000") == date(2026, 10, 16)
    assert _expiry_of("SOFI260918C00019000") == date(2026, 9, 18)
    assert _expiry_of("A260117P00050000") == date(2026, 1, 17)


def test_a_symbol_that_cannot_be_read_is_not_guessed_at():
    assert _expiry_of("") is None
    assert _expiry_of("NOTANOPTION") is None
    assert _strike_of("garbage") is None


# =========================================================================
# the arms have to be reproducible
# =========================================================================
def test_the_same_bar_seeds_the_same_arms_in_any_process():
    """`hash()` is salted per process. Seeding the arms with it meant the same
    bar graded different genomes on every restart, so no arm could ever
    accumulate the evidence it is judged on — while the docstring claimed
    replays were reproducible."""
    import subprocess
    import sys

    code = ("import sys; sys.path.insert(0, '.');"
            "from src.trading.shadow import _seed;"
            "print([_seed('2026-09-01T05:45', i) for i in (1, 2, 3)])")
    runs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, check=True).stdout.strip()
        for _ in range(3)
    }
    assert len(runs) == 1, f"seeds differed across processes: {runs}"


def test_the_live_arm_is_never_mutated():
    desk = _desk()
    arms = desk.arms("2026-09-01T05:45")
    assert arms["live"] == GENE_DEFAULTS
    assert len(arms) > 1, "there should be counterfactuals to compare against"


# =========================================================================
# both sides — the bug that cost half the strategy
# =========================================================================
def test_a_bullish_reading_writes_a_put_below_the_market():
    desk = _desk()
    quotes = [_quote("SPY261016P00095000", "1.00", "1.02"),
              _quote("SPY261016P00099000", "2.00", "2.02"),
              _quote("SPY261016C00101000", "2.00", "2.02")]
    pick, why = desk._pick(quotes, Decimal("100"), dict(GENE_DEFAULTS), bullish=True)
    assert pick is not None, why
    assert pick.symbol[-9] == "P"
    assert _strike_of(pick.symbol) < Decimal("100")


def test_a_bearish_reading_writes_a_call_above_the_market():
    """The desk wrote puts only, justified by a claim about the account's own
    book — "21 short puts" — that was never checked and was false. It is 10
    puts and 11 calls."""
    desk = _desk()
    quotes = [_quote("SPY261016C00101000", "2.00", "2.02"),
              _quote("SPY261016C00105000", "1.00", "1.02"),
              _quote("SPY261016P00099000", "2.00", "2.02")]
    pick, why = desk._pick(quotes, Decimal("100"), dict(GENE_DEFAULTS), bullish=False)
    assert pick is not None, why
    assert pick.symbol[-9] == "C"
    assert _strike_of(pick.symbol) > Decimal("100")


def test_neither_side_will_write_in_the_money():
    desk = _desk()
    # Only ITM contracts on offer: a put above spot, a call below it.
    pick, _ = desk._pick([_quote("SPY261016P00105000", "6.00", "6.05")],
                         Decimal("100"), dict(GENE_DEFAULTS), bullish=True)
    assert pick is None
    pick, _ = desk._pick([_quote("SPY261016C00095000", "6.00", "6.05")],
                         Decimal("100"), dict(GENE_DEFAULTS), bullish=False)
    assert pick is None


def test_a_contract_wider_than_the_genes_allow_is_refused():
    """The spread is the whole reason this desk exists — the sibling repo's
    insider study died at a 52% round trip after looking like an edge."""
    desk = _desk()
    genome = dict(GENE_DEFAULTS, shadow_spread_cap=5.0)
    wide = _quote("SPY261016P00099000", "1.00", "2.00")   # 66% round trip
    pick, why = desk._pick([wide], Decimal("100"), genome, bullish=True)
    assert pick is None
    assert "%" in why


def test_the_entry_is_the_bid_because_a_writer_is_filled_there():
    desk = _desk()
    quote = _quote("SPY261016P00099000", "2.00", "2.20")
    pick, _ = desk._pick([quote], Decimal("100"), dict(GENE_DEFAULTS), bullish=True)
    assert pick.sell_at == Decimal("2.00")
    assert pick.sell_at < pick.mid, "recording the mid would invent an edge"
