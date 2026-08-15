"""Did it beat just buying the index?

The stated goal is to beat SPY, and until this existed the village could not
tell whether it was. It measured return, drawdown, win rate and Sharpe — and a
firm up 8% in a year when SPY did 12% would have shown a positive return, a
healthy score, and a proposed raise.

Most of these tests are about the comparison being honest, because that is
where a benchmark goes wrong: same window, same money, costs on the way in,
and a refusal when it cannot be measured.
"""

from __future__ import annotations

from decimal import Decimal

from src.trading import benchmark
from src.trading.benchmark import MIN_BARS, buy_and_hold, compare


class _Market:
    """A benchmark that rose 10% over 100 bars."""

    def __init__(self, series=None):
        self.series = series or [Decimal(100) + Decimal(i) / Decimal(10)
                                 for i in range(101)]

    def closes(self, symbol):
        if symbol == "MISSING":
            return []
        if symbol == "BROKEN":
            raise RuntimeError("no bars")
        return list(self.series)


class _Card:
    def __init__(self, equity, can_be_valued=True):
        self.equity = Decimal(equity)
        self.can_be_valued = can_be_valued


class _Firm:
    def __init__(self, key="alpha", capital="100000"):
        self.firm_key = key
        self.initial_allocation = Decimal(capital)
        self.allocation = Decimal(capital)
        self.cash = Decimal(capital)


# =========================================================================
# the arithmetic
# =========================================================================
def test_buy_and_hold_grows_with_the_index():
    held = buy_and_hold(_Market(), "SPY", Decimal("100000"), 100)
    assert held > Decimal("109000") and held < Decimal("111000")


def test_buying_the_index_pays_to_get_in():
    """A benchmark that fills for free is a benchmark tilted against the firm."""
    free = buy_and_hold(_Market(), "SPY", Decimal("100000"), 100)
    costed = buy_and_hold(_Market(), "SPY", Decimal("100000"), 100,
                          slippage_bps=Decimal(50), fee_bps=Decimal(25))
    assert costed < free


def test_it_does_not_pay_to_get_out():
    """The alternative being modelled is that you bought it and still hold it.
    Charging an exit the firm has not paid tilts it the other way."""
    once = buy_and_hold(_Market(), "SPY", Decimal("100000"), 100,
                        slippage_bps=Decimal(100), fee_bps=Decimal(100))
    # Two round trips' worth of cost would be roughly twice as far from free.
    free = buy_and_hold(_Market(), "SPY", Decimal("100000"), 100)
    assert (free - once) < free * Decimal("0.03")


# =========================================================================
# the refusals
# =========================================================================
def test_an_unpriceable_benchmark_is_a_refusal_not_a_zero():
    for symbol in ("MISSING", "BROKEN"):
        assert buy_and_hold(_Market(), symbol, Decimal("100000"), 100) is None

    c = compare(_Firm(), _Card("110000"), _Market(), 100, benchmark="MISSING")
    assert c.measurable is False
    assert "could not be priced" in c.why_not
    assert c.excess_pct is None, "no number at all, rather than a wrong one"


def test_a_firm_whose_own_book_cannot_be_valued_is_not_compared():
    c = compare(_Firm(), _Card("110000", can_be_valued=False), _Market(), 100)
    assert c.measurable is False
    assert "cannot be valued" in c.why_not


def test_a_window_longer_than_the_history_refuses():
    assert buy_and_hold(_Market(), "SPY", Decimal("100000"), 5000) is None


def test_a_firm_that_has_not_traded_is_not_compared():
    c = compare(_Firm(), _Card("100000"), _Market(), 0)
    assert c.measurable is False
    assert "not traded a single bar" in c.why_not


# =========================================================================
# the verdict, and what it refuses to call
# =========================================================================
def test_beating_the_index_is_stated_plainly():
    c = compare(_Firm(), _Card("130000"), _Market(), 100)
    assert c.excess_pct > 0
    assert "BEATING" in c.verdict()


def test_losing_to_the_index_is_stated_just_as_plainly():
    """The failure this exists to catch: a positive return that is still a
    loss against doing nothing."""
    c = compare(_Firm(), _Card("105000"), _Market(), 100)
    assert c.firm_return_pct > 0, "it made money"
    assert c.excess_pct < 0, "and still lost to the index"
    assert "LOSING" in c.verdict()


def test_a_fortnight_of_luck_is_not_a_verdict():
    c = compare(_Firm(), _Card("200000"), _Market(), 10)
    assert c.excess_pct > 0
    assert c.enough_data is False
    assert "not a verdict" in c.verdict()
    assert str(MIN_BARS) in c.verdict()


# =========================================================================
# the village total
# =========================================================================
def test_the_village_total_counts_only_what_it_could_measure():
    """A headline propped up by firms nobody could measure is exactly the kind
    of number this repository refuses everywhere else."""
    good = compare(_Firm("a"), _Card("130000"), _Market(), 100)
    bad = compare(_Firm("b"), _Card("130000"), _Market(), 100, benchmark="MISSING")

    result = benchmark.village([good, bad])
    assert result["measurable"] is True
    assert result["firms"] == 1 and result["skipped"] == 1
    assert "could not be compared" in result["summary"]


def test_a_village_with_nothing_measurable_says_so():
    bad = compare(_Firm(), _Card("1"), _Market(), 100, benchmark="MISSING")
    result = benchmark.village([bad])
    assert result["measurable"] is False
    assert result["firms"] == 0


def test_the_village_headline_names_the_direction():
    losers = [compare(_Firm(f"f{i}"), _Card("101000"), _Market(), 100)
              for i in range(3)]
    result = benchmark.village(losers)
    assert result["excess_pct"] < 0
    assert "LOSING to the index" in result["summary"]
    assert result["beating"] == 0


def test_the_table_never_prints_a_number_it_refused_to_compute():
    bad = compare(_Firm(), _Card("1"), _Market(), 100, benchmark="MISSING")
    [row] = benchmark.table([bad])
    assert row["excess"] == "—"
    assert row["verdict"] == "not yet"


# =========================================================================
# same window, which is where flattering comparisons are built
# =========================================================================
def test_a_firm_is_compared_over_the_bars_it_actually_lived(store, firm_record):
    """A firm that started in March is compared against what the index did
    from March, not from January."""
    assert benchmark.bars_lived(store, firm_record.id) == 0
    for day in range(1, 8):
        store.db.insert("firm_performance", {
            "firm_id": firm_record.id, "as_of": f"2026-03-{day:02}T00:00:00Z",
            "equity": "100000",
        })
    assert benchmark.bars_lived(store, firm_record.id) == 7


def test_repeated_readings_of_one_bar_are_one_bar(store, firm_record):
    for _ in range(20):
        store.db.insert("firm_performance", {
            "firm_id": firm_record.id, "as_of": "2026-03-01T00:00:00Z",
            "equity": "100000",
        })
    assert benchmark.bars_lived(store, firm_record.id) == 1
