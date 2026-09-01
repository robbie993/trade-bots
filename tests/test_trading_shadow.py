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


def test_an_arm_is_named_after_every_gene_that_varies():
    """The name is the arm's identity and results are pooled under it, so a
    gene left out of the name merges two different strategies into one row.
    `shadow_confidence` was the omission: it mutates from ~1 to ~40, which is
    the difference between writing on almost any reading and almost never."""
    desk = _desk()
    arms = desk.arms("2026-09-01T07:36")
    for name, genome in arms.items():
        if name == "live":
            continue
        for gene in GENE_DEFAULTS:
            assert str(genome[gene]) in name, f"{gene} missing from arm name {name}"


def test_two_arms_differing_only_in_selectivity_get_different_names():
    from src.trading.shadow import ShadowDesk

    desk = _desk()
    a = dict(GENE_DEFAULTS, shadow_confidence=5.0)
    b = dict(GENE_DEFAULTS, shadow_confidence=45.0)
    key = lambda g: ("sd{shadow_strike_sd}/dte{shadow_dte_min}-{shadow_dte_max}"
                     "/cap{shadow_spread_cap}/conf{shadow_confidence}").format(**g)
    assert key(a) != key(b)


# =========================================================================
# closing a written contract
# =========================================================================
def test_a_written_contract_is_held_rather_than_bought_straight_back():
    """A premium seller is paid for time and has to spend some.

    `_settle` used to buy back on the very next bar it could quote the
    contract: sold at the bid, bought at the ask, no decay collected and the
    whole round trip paid. At this desk's 3% median spread that is a machine
    for losing 3% a trade, and every arm converges on the same loss while the
    leaderboard ranks the noise between them.

    It never fired only because a second bug hid it — the lookup asked for an
    unfiltered chain, which is the first hundred contracts in symbol order, so
    the desk's own contracts were never in the window and nothing ever closed.
    Fixing the lookup alone would have switched the real bug on.
    """
    from datetime import date, timedelta

    from src.trading.shadow import EXIT_DTE, _expiry_of

    far = date.today() + timedelta(days=30)
    near = date.today() + timedelta(days=1)
    assert (far - date.today()).days > EXIT_DTE, "a 30-day contract must be held"
    assert (near - date.today()).days <= EXIT_DTE, "a 1-day contract must be closed"


def test_settle_asks_for_the_contract_it_actually_holds(monkeypatch):
    """The lookup must be filtered to the contract's own expiry and side."""
    from src.trading.shadow import ShadowDesk

    asked = {}

    class Feed:
        last_error: dict = {}

        def chain(self, underlying, **kw):
            asked.update(kw, underlying=underlying)
            return []

    desk = ShadowDesk.__new__(ShadowDesk)
    desk.feed = Feed()
    desk.store = None
    from datetime import date, timedelta
    expiry = date.today() + timedelta(days=1)
    occ = f"SPY{expiry:%y%m%d}C00500000"
    desk.open_trades = lambda: [{
        "id": 1, "contract": occ, "underlying": "SPY",
        "entry_price": "1.00", "quantity": 1,
    }]
    desk._settle("bar", type("R", (), {"closed": []})())
    assert asked.get("expiry_gte") == expiry.isoformat()
    assert asked.get("expiry_lte") == expiry.isoformat()
    assert asked.get("right") == "call"


# =========================================================================
# the bar the village acts on must not run backwards
# =========================================================================
def test_a_sentence_is_not_served_twice_when_the_bar_oscillates():
    """`market.as_of()` is not monotonic and the gulag paid for it.

    It reports `max(bar.as_of)` across symbols, and a partial bar is present in
    one fetch and absent from the next — so the maximum flips back. Measured
    live on 2026-09-01 the village alternated 16:00 / 16:15 / 16:00 / 16:15
    within single minutes, and because the guard tested equality, every flip
    counted as a fresh bar: an 80-bar sentence drained in about two hours
    instead of twenty.
    """
    from src.trading.firms import strikes

    state = {"strikes": 1, "gulag_bars_left": 10, "gulag_last_bar": None,
             "breach_open": 1, "last_strike_reason": "x"}

    class Store:
        def strike_state(self, _): return dict(state)
        def set_gulag(self, _, left, bar):
            state["gulag_bars_left"] = left
            if bar is not None:
                state["gulag_last_bar"] = bar
        def release_from_gulag(self, _): state["gulag_bars_left"] = 0

    firm = type("F", (), {"id": 1, "firm_key": "f"})()
    store = Store()

    strikes.serve_sentence(store, firm, "2026-09-01T16:15")
    assert state["gulag_bars_left"] == 9
    # The feed drops the partial bar and the maximum falls back an interval.
    strikes.serve_sentence(store, firm, "2026-09-01T16:00")
    assert state["gulag_bars_left"] == 9, "time ran backwards and cost a bar"
    # Same bar again: still nothing.
    strikes.serve_sentence(store, firm, "2026-09-01T16:15")
    assert state["gulag_bars_left"] == 9
    # A genuinely new bar advances it.
    strikes.serve_sentence(store, firm, "2026-09-01T16:30")
    assert state["gulag_bars_left"] == 8
