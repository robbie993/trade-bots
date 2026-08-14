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

from datetime import date
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


def _bars(store, firm, feed, n, start=1):
    """N distinct market bars on a feed.

    Distinct on purpose: `record_bar` is keyed on the bar, so calling it in a
    loop with the same date records one bar. That is the whole fix — the
    counter it replaced would have recorded N.
    """
    from datetime import date, timedelta

    for i in range(n):
        promotion.record_bar(store, firm.id, feed,
                             date(2026, 1, 1) + timedelta(days=start + i))


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
    _bars(store, firm_record, "synthetic", 500)

    verdict = assess(store, firm_record, None, "synthetic")
    names = {c.name for c in verdict.failures}
    assert "Feed is real" in names


def test_bars_are_counted_per_feed(store, firm_record):
    _bars(store, firm_record, "alpaca", 7)
    _bars(store, firm_record, "synthetic", 3)
    assert promotion.bars_on(store, firm_record.id, "alpaca") == 7
    assert promotion.bars_on(store, firm_record.id, "synthetic") == 3
    assert promotion.bars_on(store, firm_record.id, "yahoo") == 0


def test_a_thousand_ticks_on_one_bar_is_one_bar(store, firm_record):
    """The bug this table was rewritten for.

    The counter it replaced was incremented once per scorecard, and a scorecard
    is written once per *tick*. A village left running over lunch reported 136
    bars against a feed whose date had advanced once, and the promotion gate —
    the check a track record is not supposed to be able to fake — read that
    number and cleared.
    """
    from datetime import date

    for _ in range(1000):
        promotion.record_bar(store, firm_record.id, "alpaca", date(2026, 8, 13))
    assert promotion.bars_on(store, firm_record.id, "alpaca") == 1

    promotion.record_bar(store, firm_record.id, "alpaca", date(2026, 8, 14))
    assert promotion.bars_on(store, firm_record.id, "alpaca") == 2


def test_a_tick_that_cannot_name_its_bar_records_nothing(store, firm_record):
    """A blind feed has no bar date. Counting the tick anyway would let an
    outage build a track record."""
    for _ in range(50):
        promotion.record_bar(store, firm_record.id, "alpaca", None)
    assert promotion.bars_on(store, firm_record.id, "alpaca") == 0


def test_the_time_of_day_does_not_start_a_new_bar(store, firm_record):
    """Same trading day, different clock times — still one observation of the
    market, whatever the wall clock thinks."""
    from datetime import datetime, timezone

    for hour in range(24):
        promotion.record_bar(store, firm_record.id, "alpaca",
                             datetime(2026, 8, 13, hour, tzinfo=timezone.utc))
    assert promotion.bars_on(store, firm_record.id, "alpaca") == 1


def test_a_fast_feed_gathers_data_quickly_and_market_slowly(store, firm_record):
    """The gate that stops intraday bars quietly weakening the one beside them.

    Thirty hourly bars is four days of market, and four days of market has seen
    one kind of weather. A bar count measures how much data was gathered; the
    span measures how much market it was gathered across.
    """
    from datetime import datetime, timezone

    from src.trading.resolution import parse

    hourly = parse("1h")
    for day in (12, 13, 14, 15):
        for hour in range(9, 16):
            promotion.record_bar(
                store, firm_record.id, "alpaca",
                datetime(2026, 8, day, hour, tzinfo=timezone.utc), hourly)

    assert promotion.bars_on(store, firm_record.id, "alpaca") == 28
    assert promotion.days_on(store, firm_record.id, "alpaca") == 4

    verdict = assess(store, firm_record, None, "alpaca")
    assert "Days of market" in {c.name for c in verdict.failures}


def test_the_span_is_read_from_the_bars_not_the_clock(store, firm_record):
    """Leaving the process running overnight must add nothing to either
    figure."""
    _bars(store, firm_record, "alpaca", 40)
    assert promotion.days_on(store, firm_record.id, "alpaca") == 40
    for _ in range(200):
        promotion.record_bar(store, firm_record.id, "alpaca", date(2026, 1, 2))
    assert promotion.days_on(store, firm_record.id, "alpaca") == 40


def test_a_firm_with_no_history_spans_no_days(store, firm_record):
    assert promotion.days_on(store, firm_record.id, "alpaca") == 0


def test_a_record_on_one_feed_does_not_qualify_you_on_another(store, firm_record):
    """Switching feeds must not inherit the history. Otherwise a firm
    re-qualifies by changing what it reads."""
    _bars(store, firm_record, "yahoo", 100)
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
    _bars(store, firm_record, "alpaca", 60)

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
    _bars(store, firm_record, "alpaca", 60)
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


# =========================================================================
# the flow: asking, carrying out, and coming back
# =========================================================================
def _make_ready(store, firm, feed="alpaca"):
    from src.trading.brokerage.evaluator import Scorecard

    _fills(store, firm, [Decimal("40"), Decimal("30"), Decimal("50"),
                         Decimal("-10")] * 20)
    _bars(store, firm, feed, 60)
    return Scorecard(firm_key=firm.firm_key, firm_id=firm.id,
                     drawdown_pct=Decimal("4.0"), win_rate_pct=Decimal("75.0"))


def _buy(store, firm, symbol="SPY", quantity="10", price="100"):
    """Open a position the way the village opens one — through the ledger.

    Inserting a row into `positions` directly conjures stock out of nothing
    and the reconciler is right to refuse the tick that follows, so every
    test that needs a live firm holding something buys it properly.
    """
    from src.trading.models import Fill, Side

    return store.settle(store.get_firm(firm.firm_key), Fill(
        firm_id=firm.id, symbol=symbol, side=Side.BUY.value,
        quantity=quantity, price=price, fee="0",
    ))


def test_an_unready_firm_cannot_even_be_requested(ecosystem):
    """An approval put in front of somebody is an implicit claim that the
    evidence supports it. Filing nine unsupported ones is how a human is
    misled by their own system."""
    import pytest as _pytest

    firm = ecosystem.store.firms()[0]
    verdict = assess(ecosystem.store, firm, None, "alpaca")
    with _pytest.raises(ValueError, match="does not meet the criteria"):
        ecosystem.brokerage.request_promotion(firm.firm_key, verdict, "alpaca", "robbie")
    assert ecosystem.gate.pending() == []


def test_a_ready_firm_files_a_request_and_nothing_more(ecosystem):
    firm = ecosystem.store.firms()[0]
    card = _make_ready(ecosystem.store, firm)
    verdict = assess(ecosystem.store, firm, card, "alpaca")

    approval = ecosystem.brokerage.request_promotion(
        firm.firm_key, verdict, "alpaca", "robbie")
    assert approval is not None
    # Requested, not granted.
    assert ecosystem.store.get_firm(firm.firm_key).venue == "paper"


def test_the_mandate_is_capped_not_transferred(ecosystem):
    """A firm running $50,000 on paper does not get $50,000 of real money
    because it did well at pretend."""
    firm = ecosystem.store.firms()[0]
    assert Decimal(firm.allocation) > LiveReadiness().max_start_capital

    ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca",
                                     LiveReadiness().max_start_capital, "robbie")
    after = ecosystem.store.get_firm(firm.firm_key)
    assert after.venue == "alpaca"
    assert Decimal(after.allocation) == LiveReadiness().max_start_capital


def test_the_cap_takes_the_buying_power_with_it(ecosystem, market):
    """Capping the allocation and leaving $50,000 of cash in the account
    would be a cap in the ledger and no cap at all in the market."""
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")

    after = ecosystem.store.get_firm(firm.firm_key)
    assert Decimal(after.cash) == Decimal("500.00")
    # And the books still balance afterwards, which is the whole reason the
    # money is moved rather than the number edited.
    assert ecosystem.brokerage.reconciler.check([after], market).ok


def test_a_firm_goes_live_flat_or_not_at_all(ecosystem):
    """Its paper positions were bought with money that does not exist, and
    the live venue has never heard of them."""
    import pytest as _pytest

    firm = ecosystem.store.firms()[0]
    _buy(ecosystem.store, firm)
    with _pytest.raises(ValueError, match="goes live flat"):
        ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")
    assert ecosystem.store.get_firm(firm.firm_key).venue == "paper"


def test_coming_back_needs_nobody(ecosystem):
    """The other half of the asymmetry, and the reason the first half is
    acceptable."""
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")
    result = ecosystem.brokerage.demote_firm(firm.firm_key, "because I said so")
    assert result["changed"] is True
    assert ecosystem.store.get_firm(firm.firm_key).venue == "paper"
    assert ecosystem.gate.pending() == []


def test_one_button_pulls_everything_back(ecosystem):
    for firm in ecosystem.store.firms():
        ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")
    assert len(ecosystem.brokerage.live_firms()) == len(ecosystem.store.firms())

    ecosystem.brokerage.all_to_paper("three in the morning")
    assert ecosystem.brokerage.live_firms() == []


def test_a_tick_pulls_a_live_firm_off_real_money_when_it_cannot_be_valued(ecosystem):
    """No approval, no delay, no asking. The tick notices and acts."""
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")
    _buy(ecosystem.store, firm, quantity="1", price="100")
    real = ecosystem.feed

    class RefusesSPY:
        name = "picky"

        def series(self, symbol):
            if symbol == "SPY":
                raise RuntimeError("no bars")
            return real.series(symbol)

    ecosystem._feed = RefusesSPY()
    report = ecosystem.tick()

    assert ecosystem.store.get_firm(firm.firm_key).venue == "paper"
    assert any("PULLED OFF REAL MONEY" in e for e in report.errors)


def test_a_healthy_live_firm_is_left_alone(ecosystem):
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")
    ecosystem.tick()
    assert ecosystem.store.get_firm(firm.firm_key).venue == "alpaca"


def test_demotion_reports_real_stock_and_sells_none_of_it(ecosystem):
    """Selling automatically is how a data outage becomes a realised loss —
    which is the exact accident this whole area of the code exists because of.
    Coming off the venue is instant; closing the book is a person's job."""
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")
    _buy(ecosystem.store, firm, quantity="1", price="100")

    result = ecosystem.brokerage.demote_firm(firm.firm_key, "the feed went dark")
    assert result["still_open"] == ["SPY"]
    assert result["was_venue"] == "alpaca"
    held = ecosystem.store.positions(firm.id)
    assert [p.symbol for p in held if p.is_open] == ["SPY"], "nothing was sold"


def test_a_tick_says_out_loud_what_is_still_held(ecosystem):
    firm = ecosystem.store.firms()[0]
    ecosystem.brokerage.promote_firm(firm.firm_key, "alpaca", Decimal("500"), "robbie")
    _buy(ecosystem.store, firm, quantity="1", price="100")
    real = ecosystem.feed

    class RefusesSPY:
        name = "picky"

        def series(self, symbol):
            if symbol == "SPY":
                raise RuntimeError("no bars")
            return real.series(symbol)

    ecosystem._feed = RefusesSPY()
    report = ecosystem.tick()
    assert any("STILL HOLDS SPY" in e for e in report.errors)


# =========================================================================
# the gate is the only door
# =========================================================================
def test_an_approved_request_is_what_moves_a_firm_onto_real_money(ecosystem):
    firm = ecosystem.store.firms()[0]
    card = _make_ready(ecosystem.store, firm)
    verdict = assess(ecosystem.store, firm, card, "alpaca")
    approval = ecosystem.brokerage.request_promotion(
        firm.firm_key, verdict, "alpaca", "robbie")

    # Pending: still paper. The request alone is not permission.
    assert ecosystem.apply_approvals() == []
    assert ecosystem.store.get_firm(firm.firm_key).venue == "paper"

    ecosystem.gate.approve(approval.id, approved_by="robbie")
    done = ecosystem.apply_approvals()
    assert any("is LIVE on alpaca" in line for line in done)
    after = ecosystem.store.get_firm(firm.firm_key)
    assert after.venue == "alpaca"
    assert Decimal(after.cash) == LiveReadiness().max_start_capital

    # And it cannot be carried out twice.
    assert ecosystem.apply_approvals() == []


def test_an_approval_that_no_longer_fits_the_firm_is_refused_not_forced(ecosystem):
    """The human approved a firm as it stood when the request was filed. If it
    has opened a position since, the answer is no — never 'go live anyway'."""
    firm = ecosystem.store.firms()[0]
    card = _make_ready(ecosystem.store, firm)
    verdict = assess(ecosystem.store, firm, card, "alpaca")
    approval = ecosystem.brokerage.request_promotion(
        firm.firm_key, verdict, "alpaca", "robbie")
    ecosystem.gate.approve(approval.id, approved_by="robbie")

    _buy(ecosystem.store, firm)
    done = ecosystem.apply_approvals()
    assert any("REFUSED going live" in line for line in done)
    assert ecosystem.store.get_firm(firm.firm_key).venue == "paper"


def test_the_council_never_rules_on_real_money(ecosystem):
    """It has no panel for it, by construction, and this must stay true even
    with every autonomy switch on."""
    import dataclasses

    from src.trading.config import AutonomyConfig

    ecosystem.config = dataclasses.replace(
        ecosystem.config, autonomy=AutonomyConfig(mode="council"))
    assert ecosystem.config.autonomy.council_decides is True

    firm = ecosystem.store.firms()[0]
    card = _make_ready(ecosystem.store, firm)
    verdict = assess(ecosystem.store, firm, card, "alpaca")
    approval = ecosystem.brokerage.request_promotion(
        firm.firm_key, verdict, "alpaca", "robbie")

    ecosystem.hold_council()
    assert ecosystem.gate.get(approval.id).status == "pending"
    assert ecosystem.store.get_firm(firm.firm_key).venue == "paper"
