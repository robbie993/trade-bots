"""Whether a firm has earned real money.

Everywhere else the hard question is when to stop. This is the only code that
asks when to *start*, which makes it the one place where the house rule points
the other way — and the one place where a bug costs money rather than
opportunity.

So the tests are almost entirely about refusals, and about one specific refusal
above all: a strategy measured against a seeded random walk has demonstrated
that it can trade a seeded random walk. Nobody is offering to pay for that.
"""

from __future__ import annotations

from decimal import Decimal

from src.trading import promotion
from src.trading.promotion import LiveReadiness, assess, expectancy_t


# =========================================================================
# is the edge distinguishable from luck
# =========================================================================
def test_too_few_trades_has_no_statistic():
    """Below three, the number would be noise wearing a number's clothes."""
    assert expectancy_t([]) is None
    assert expectancy_t([Decimal("10"), Decimal("20")]) is None


def test_a_reliable_small_edge_beats_a_lucky_large_one():
    """The whole reason this is a t-statistic and not a sum. Both made money;
    only one of them made it repeatably."""
    steady = [Decimal("5")] * 20 + [Decimal("4"), Decimal("6")] * 5
    lucky = [Decimal("-90"), Decimal("300"), Decimal("-80"), Decimal("-40"),
             Decimal("-60")] * 6
    assert sum(lucky) > 0, "the lucky one really did make money"
    assert expectancy_t(steady) > expectancy_t(lucky)


def test_a_losing_firm_is_negative():
    assert expectancy_t([Decimal("-5")] * 30) < 0


def test_identical_trades_do_not_divide_by_zero():
    """Real for a fixed-payoff strategy, and a hallmark of synthetic data."""
    assert expectancy_t([Decimal("7")] * 40) > 0
    assert expectancy_t([Decimal("-7")] * 40) < 0
    assert expectancy_t([Decimal("0.00")] * 40) is None or True   # never raises


# =========================================================================
# the criteria, against a real firm
# =========================================================================
def _fills(store, firm, pnls, symbol="SPY"):
    """Closed trades with a given realised P&L each."""
    for pnl in pnls:
        store.db.insert("fills", {
            "firm_id": firm.id, "symbol": symbol, "side": "sell",
            "quantity": "1", "price": "100", "realized_pnl": str(pnl),
        })


def test_a_fresh_firm_meets_almost_nothing(store, firm_record, market_data):
    from src.trading.brokerage.evaluator import Evaluator
    from src.trading.config import TradingConfig

    card = Evaluator(store, TradingConfig()).evaluate(firm_record, market_data)
    verdict = assess(store, firm_record, card, "alpaca")
    assert verdict.ready is False
    unmet = {c.name for c in verdict.failures}
    assert "Closed trades" in unmet
    assert "Expectancy (t)" in unmet


def test_every_criterion_is_reported_pass_or_fail(store, firm_record, market_data):
    """A decision you can only see the first failing reason for is a decision
    you cannot audit — and this is the one that spends money."""
    verdict = assess(store, firm_record, None, "alpaca")
    assert len(verdict.checks) >= 8
    assert len(promotion.table(verdict)) == len(verdict.checks)
    assert all(row["met"] in ("yes", "NO") for row in promotion.table(verdict))


def test_a_synthetic_record_is_never_evidence(store, firm_record):
    """The check a track record cannot fake. A firm can be perfect on a random
    walk — no gaps, no halts, no earnings, no liquidity — and know nothing."""
    _fills(store, firm_record, [Decimal("50")] * 200)
    for _ in range(500):
        promotion.record_bar(store, firm_record.id, "synthetic")

    verdict = assess(store, firm_record, None, "synthetic")
    names = {c.name for c in verdict.failures}
    assert "Feed is real" in names


def test_bars_are_counted_per_feed(store, firm_record):
    for _ in range(7):
        promotion.record_bar(store, firm_record.id, "alpaca")
    for _ in range(3):
        promotion.record_bar(store, firm_record.id, "synthetic")
    assert promotion.bars_on(store, firm_record.id, "alpaca") == 7
    assert promotion.bars_on(store, firm_record.id, "synthetic") == 3
    assert promotion.bars_on(store, firm_record.id, "yahoo") == 0


def test_a_record_on_one_feed_does_not_qualify_you_on_another(store, firm_record):
    """Switching feeds must not inherit the history. Otherwise a firm
    re-qualifies by changing what it reads."""
    for _ in range(100):
        promotion.record_bar(store, firm_record.id, "yahoo")
    verdict = assess(store, firm_record, None, "alpaca")
    unmet = {c.name for c in verdict.failures}
    assert "Bars on alpaca" in unmet


def test_a_book_that_cannot_be_valued_is_refused(store, firm_record, market_data):
    """Every criterion below is computed from the numbers, so this one gates
    what the rest of them mean."""
    from src.trading.brokerage.evaluator import Scorecard

    card = Scorecard(firm_key=firm_record.firm_key, firm_id=firm_record.id,
                     unpriceable=("SPY",))
    verdict = assess(store, firm_record, card, "alpaca")
    assert "Book is measurable" in {c.name for c in verdict.failures}


def test_an_unreconciled_village_promotes_nobody(store, firm_record):
    verdict = assess(store, firm_record, None, "alpaca", reconciled=False)
    assert "Books reconcile" in {c.name for c in verdict.failures}


def test_a_paused_firm_is_refused(store, firm_record):
    from src.trading.models import FirmStatus

    store.set_firm_status(firm_record.id, FirmStatus.PAUSED.value)
    firm = store.require_firm_by_id(firm_record.id)
    assert "Status" in {c.name for c in assess(store, firm, None, "alpaca").failures}


def test_the_drawdown_bar_is_higher_than_the_kill_bar(store, firm_record):
    """The level at which you would shut a firm down is not the level at which
    you would hand it money."""
    from src.trading.config import FirmKillConfig

    assert LiveReadiness().max_drawdown_pct < FirmKillConfig().max_drawdown_pct


def test_the_trade_bar_is_higher_than_the_sample_gate(store):
    """Twenty trades settles "is this obviously broken". It does not settle
    "is this edge real"."""
    from src.trading.config import FirmKillConfig

    assert LiveReadiness().min_closed_trades > FirmKillConfig().minimum_trades


# =========================================================================
# the one yes
# =========================================================================
def test_a_firm_with_real_evidence_passes(store, firm_record, market_data):
    """Assembled deliberately: a long, reliable record, gathered on the feed it
    would trade, on a book that can be valued."""
    from src.trading.brokerage.evaluator import Scorecard

    _fills(store, firm_record, [Decimal("40"), Decimal("30"), Decimal("50"),
                                Decimal("-10")] * 20)
    for _ in range(60):
        promotion.record_bar(store, firm_record.id, "alpaca")

    card = Scorecard(
        firm_key=firm_record.firm_key, firm_id=firm_record.id,
        drawdown_pct=Decimal("4.0"), win_rate_pct=Decimal("75.0"),
    )
    verdict = assess(store, firm_record, card, "alpaca")
    assert verdict.ready, [c.name for c in verdict.failures]
    assert "every criterion met" in verdict.summary()


def test_the_first_mandate_is_small(store, firm_record, market_data):
    """The first live run is for finding what paper cannot show you — real
    slippage, partial fills, rejects, halts — not for profit."""
    verdict = assess(store, firm_record, None, "alpaca")
    assert verdict.start_capital <= LiveReadiness().max_start_capital
    assert verdict.start_capital < Decimal(firm_record.allocation)


def test_readiness_grants_nothing(store, firm_record, market_data):
    """The module reads and reports. It has no path to a venue, an allocation
    or the gate — promotion goes through the same door as every other increase
    in risk."""
    from src.trading.brokerage.evaluator import Scorecard

    before = store.require_firm_by_id(firm_record.id)
    _fills(store, firm_record, [Decimal("40")] * 100)
    for _ in range(60):
        promotion.record_bar(store, firm_record.id, "alpaca")
    assess(store, firm_record, Scorecard(
        firm_key=firm_record.firm_key, firm_id=firm_record.id,
        drawdown_pct=Decimal("1"), win_rate_pct=Decimal("90"),
    ), "alpaca")

    after = store.require_firm_by_id(firm_record.id)
    assert after.venue == before.venue
    assert after.allocation == before.allocation
    assert after.status == before.status


# =========================================================================
# the tick records what it was measured on
# =========================================================================
def test_a_tick_counts_a_bar_against_the_live_feed(ecosystem):
    ecosystem.tick()
    firm = ecosystem.store.firms()[0]
    name = ecosystem.feed.name
    assert promotion.bars_on(ecosystem.store, firm.id, name) >= 1


def test_the_source_module_never_writes_to_firms():
    """Guarded by reading the file: this is the module standing between a paper
    village and real money, and it must stay a reporter."""
    from pathlib import Path

    source = Path("src/trading/promotion.py").read_text()
    for forbidden in ("set_firm_status", "update_firm_fields", "upsert_firm",
                      "gate.request", "settle("):
        assert forbidden not in source, forbidden
