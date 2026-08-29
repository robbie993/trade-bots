"""The brokerage: reconciliation, scoring, allocation, the ecosystem stop.

The behaviour under test that matters most is the asymmetry — the brokerage
cuts capital by itself and can never add it by itself — and the refusal to do
anything at all on books that do not reconcile.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.agents.human_gate import ApprovalAction
from src.money import D, money
from src.trading.brokerage.allocator import Allocator
from src.trading.brokerage.brokerage import Brokerage
from src.trading.brokerage.evaluator import Evaluator, Scorecard
from src.trading.brokerage.leaderboard import Leaderboard
from src.trading.brokerage.reconciliation import LedgerNotReconciled, Reconciler
from src.trading.config import BrokerageConfig
from src.trading.models import Fill, FirmRecord, FirmStatus, Side
from src.trading.store import TradingLedgerError


def fill_for(firm, symbol="SPY", side=Side.BUY, quantity="10", price="100", fee="1"):
    return Fill(
        firm_id=firm.id,
        symbol=symbol,
        side=side.value,
        quantity=quantity,
        price=price,
        fee=fee,
    )


# =========================================================================
# reconciliation
# =========================================================================
def test_a_fresh_firm_reconciles(store, firm_record, market, trading_config):
    report = Reconciler(store, trading_config.brokerage).check([firm_record], market)
    assert report.ok
    assert "no breaks" in report.summary()


def test_the_identities_hold_after_trading(store, firm_record, market, trading_config):
    market.seek(150)
    store.settle(firm_record, fill_for(firm_record))
    firm = store.require_firm_by_id(firm_record.id)
    store.settle(firm, fill_for(firm, side=Side.SELL, quantity="4", price="110"))
    report = Reconciler(store, trading_config.brokerage).check(
        [store.require_firm_by_id(firm_record.id)], market
    )
    assert report.ok, report.summary()


def test_tampering_with_cash_is_caught(store, firm_record, market, trading_config):
    store.settle(firm_record, fill_for(firm_record))
    store.db.update("firms", firm_record.id, {"cash": Decimal("999999.00")})
    report = Reconciler(store, trading_config.brokerage).check(
        [store.require_firm_by_id(firm_record.id)], market
    )
    assert not report.ok
    assert "cash" in report.summary()


def test_a_deleted_fill_breaks_the_equity_identity(store, firm_record, market, trading_config):
    fill = store.settle(firm_record, fill_for(firm_record))
    store.db.execute("DELETE FROM fills WHERE id = ?", (fill.id,))
    report = Reconciler(store, trading_config.brokerage).check(
        [store.require_firm_by_id(firm_record.id)], market
    )
    assert not report.ok


def test_the_tolerance_grows_with_the_number_of_fills(store, trading_config):
    reconciler = Reconciler(store, trading_config.brokerage)
    assert reconciler.tolerance_for(0) == trading_config.brokerage.reconcile_tolerance
    assert reconciler.tolerance_for(500) == money(D("5.00"))


# =========================================================================
# settlement
# =========================================================================
def test_settlement_moves_cash_position_and_pnl_together(store, firm_record):
    fill = store.settle(firm_record, fill_for(firm_record))
    assert fill.cash_delta == Decimal("-1001.00")  # 10 * 100 + 1 fee
    firm = store.require_firm_by_id(firm_record.id)
    assert firm.cash == Decimal("98999.00")
    position = store.get_position(firm.id, "SPY")
    assert position.quantity == Decimal("10")


def test_settlement_refuses_to_overdraw(store, firm_record):
    with pytest.raises(TradingLedgerError) as exc:
        store.settle(firm_record, fill_for(firm_record, quantity="10000", price="100"))
    assert "overdraw" in str(exc.value)
    # Nothing was written: no fill, no position.
    assert store.fills(firm_record.id) == []
    assert store.positions(firm_record.id) == []


def test_consecutive_losses_are_counted_and_reset(store, firm_record):
    store.settle(firm_record, fill_for(firm_record, quantity="10", price="100"))
    firm = store.require_firm_by_id(firm_record.id)
    store.settle(firm, fill_for(firm, side=Side.SELL, quantity="5", price="90"))
    assert store.require_firm_by_id(firm_record.id).consecutive_losses == 1

    firm = store.require_firm_by_id(firm_record.id)
    store.settle(firm, fill_for(firm, side=Side.SELL, quantity="5", price="120"))
    assert store.require_firm_by_id(firm_record.id).consecutive_losses == 0


def test_capital_cannot_be_withdrawn_from_positions(store, firm_record):
    """The bug this guards: withdrawing more than the uninvested cash drives
    cash negative, which then blocks the very sells that would free it."""
    store.settle(firm_record, fill_for(firm_record, quantity="900", price="100"))
    firm = store.require_firm_by_id(firm_record.id)
    with pytest.raises(TradingLedgerError) as exc:
        store.set_allocation(firm.id, Decimal("50000"), Decimal("-50000"))
    assert "uninvested" in str(exc.value)


def test_a_capital_move_carries_the_high_water_mark_with_it(store, firm_record):
    """Otherwise taking $90k off a firm reads as a 90% drawdown."""
    store.set_allocation(firm_record.id, Decimal("10000"), Decimal("-90000"))
    firm = store.require_firm_by_id(firm_record.id)
    assert firm.high_water_mark == Decimal("10000.00")


# =========================================================================
# scoring
# =========================================================================
def test_a_firm_below_the_gate_is_marked_insufficient(store, firm_record, market, trading_config):
    card = Evaluator(store, trading_config).evaluate(firm_record, market)
    assert card.sufficient_data is False
    assert card.score == Decimal("50.00")  # base only: no return, no drawdown


def test_returns_are_measured_against_the_capital_given(store, firm_record, market, trading_config):
    """A firm's return must not improve merely because capital was withdrawn."""
    market.seek(150)
    store.settle(firm_record, fill_for(firm_record, quantity="10", price="100"))
    firm = store.require_firm_by_id(firm_record.id)
    store.settle(firm, fill_for(firm, side=Side.SELL, quantity="10", price="200"))

    evaluator = Evaluator(store, trading_config)
    before = evaluator.evaluate(store.require_firm_by_id(firm_record.id), market)
    store.set_allocation(firm_record.id, Decimal("50000"), Decimal("-50000"))
    after = evaluator.evaluate(store.require_firm_by_id(firm_record.id), market)
    assert before.return_pct == after.return_pct


def test_score_components_are_reported_with_the_score(store, trading_config):
    evaluator = Evaluator(store, trading_config)
    card = Scorecard(
        firm_key="x",
        firm_id=1,
        return_pct=D("10"),
        drawdown_pct=D("5"),
        win_rate_pct=D("70"),
        sharpe=D("2"),
        sufficient_data=True,
    )
    score, components = evaluator.score(card)
    assert set(components) == {"base", "return", "drawdown", "win_rate", "sharpe"}
    assert score == Decimal("85.00")  # 50 + 20 - 5 + 10 + 10


def test_score_is_clamped_to_the_band(store, trading_config):
    evaluator = Evaluator(store, trading_config)
    huge = Scorecard(firm_key="x", firm_id=1, return_pct=D("900"), sufficient_data=True)
    awful = Scorecard(firm_key="y", firm_id=1, return_pct=D("-900"), drawdown_pct=D("99"))
    assert evaluator.score(huge)[0] <= Decimal("100")
    assert evaluator.score(awful)[0] >= Decimal("0")


def test_statistics_are_absent_below_the_gate(store, trading_config):
    evaluator = Evaluator(store, trading_config)
    card = Scorecard(firm_key="x", firm_id=1, win_rate_pct=D("90"), sharpe=D("3"))
    _, components = evaluator.score(card)
    assert "win_rate" not in components and "sharpe" not in components


# =========================================================================
# allocation
# =========================================================================
def card_for(firm, score, sufficient=True):
    return Scorecard(
        firm_key=firm.firm_key, firm_id=firm.id, score=D(score), sufficient_data=sufficient
    )


def test_a_bad_score_cuts_capital_without_asking(store, firm_record, gate, trading_config):
    allocator = Allocator(store, trading_config.brokerage, gate)
    changes = allocator.apply(allocator.plan([card_for(firm_record, 10)], [firm_record]))
    assert changes[0].applied is True
    assert changes[0].new_allocation == Decimal("90000.00")
    assert gate.pending() == []
    assert store.require_firm_by_id(firm_record.id).allocation == Decimal("90000.00")


def test_a_good_score_only_requests_more_capital(store, firm_record, gate, trading_config):
    allocator = Allocator(store, trading_config.brokerage, gate)
    changes = allocator.apply(allocator.plan([card_for(firm_record, 90)], [firm_record]))
    assert changes[0].applied is False
    assert changes[0].approval_id is not None
    # The money has NOT moved.
    assert store.require_firm_by_id(firm_record.id).allocation == Decimal("100000.00")
    pending = gate.pending()
    assert pending[0].action == ApprovalAction.ALLOCATE_CAPITAL.value


def test_an_approved_increase_is_the_only_way_capital_grows(store, firm_record, trading_config):
    allocator = Allocator(store, trading_config.brokerage)
    change = allocator.apply_approved_increase("test_firm", Decimal("110000"))
    firm = store.require_firm_by_id(firm_record.id)
    assert change.applied
    assert firm.allocation == Decimal("110000.00")
    assert firm.cash == Decimal("110000.00")  # the new money arrives as cash


def test_the_ledger_says_whether_a_raise_was_earned_or_a_correction(
    store, firm_record, trading_config
):
    """Two very different things arrive through this one door.

    A firm given more money because it earned it, and money put back because
    the village took it by mistake, are both increases and both need a human.
    Only one is evidence about the strategy, and a reader six months from now
    cannot tell them apart from "approved by human" alone.
    """
    allocator = Allocator(store, trading_config.brokerage)
    allocator.apply_approved_increase(
        "test_firm", Decimal("110000"),
        reason="12 of 13 cuts were the per-tick bug", by="robbie")

    [event] = [e for e in store.events(firm_record.id)
               if e.get("event_type") == "allocation"]
    assert "per-tick bug" in event["detail"]
    assert "[by robbie]" in event["detail"]


def test_an_unattributed_raise_never_claims_a_human(
    store, firm_record, trading_config
):
    """A gap in the record is survivable. A false entry is not.

    The default used to read "approved by human", and `apply_approvals` never
    passed `by` for an allocate_capital row — so six council-granted raises
    that took a firm from $100,000 to $177,156 were all written into the
    ledger as human decisions. Nobody audits a line that already says the
    right thing.
    """
    allocator = Allocator(store, trading_config.brokerage)
    allocator.apply_approved_increase("test_firm", Decimal("110000"))

    [event] = [e for e in store.events(firm_record.id)
               if e.get("event_type") == "allocation"]
    assert "human" not in event["detail"].lower()
    assert json.loads(event["payload"])["by"] == "", "unknown, and saying so"


def test_two_firms_each_get_their_own_approval(store, gate, trading_config):
    firms = [
        store.upsert_firm(
            FirmRecord(firm_key=key, allocation=D("100000"), initial_allocation=D("100000"),
                       cash=D("100000"), universe=["SPY"])
        )
        for key in ("a", "b")
    ]
    allocator = Allocator(store, trading_config.brokerage, gate)
    cards = [card_for(f, 90) for f in firms]
    allocator.apply(allocator.plan(cards, firms))
    assert len({a.id for a in gate.pending()}) == 2


def test_a_firm_below_the_gate_is_left_alone(store, firm_record, gate, trading_config):
    allocator = Allocator(store, trading_config.brokerage, gate)
    assert allocator.plan([card_for(firm_record, 5, sufficient=False)], [firm_record]) == []


def test_cuts_are_limited_to_uninvested_cash(store, firm_record, gate, trading_config):
    store.settle(firm_record, fill_for(firm_record, quantity="950", price="100"))
    firm = store.require_firm_by_id(firm_record.id)  # ~5,050 cash left
    allocator = Allocator(store, trading_config.brokerage, gate)
    changes = allocator.apply(allocator.plan([card_for(firm, 10)], [firm]))
    assert changes[0].applied
    assert store.require_firm_by_id(firm.id).cash >= 0
    assert "cut limited to" in changes[0].reason


def test_a_fully_invested_firm_has_its_cut_deferred(store, firm_record, gate, trading_config):
    store.settle(firm_record, fill_for(firm_record, quantity="999", price="100"))
    firm = store.require_firm_by_id(firm_record.id)
    store.set_allocation(firm.id, D(firm.allocation) - D(firm.cash), -D(firm.cash))
    firm = store.require_firm_by_id(firm_record.id)
    allocator = Allocator(store, trading_config.brokerage, gate)
    assert allocator.plan([card_for(firm, 10)], [firm]) == []
    assert store.events(event_type="allocation_deferred")


def test_total_capital_is_a_hard_ceiling(store, gate):
    config = BrokerageConfig(total_capital=D("100000"))
    firm = store.upsert_firm(
        FirmRecord(firm_key="a", allocation=D("100000"), initial_allocation=D("100000"),
                   cash=D("100000"), universe=["SPY"])
    )
    allocator = Allocator(store, config, gate)
    assert allocator.plan([card_for(firm, 95)], [firm]) == []


def test_allocations_never_grow_past_the_multiple(store, gate):
    config = BrokerageConfig(max_allocation_multiple=D("1.05"))
    firm = store.upsert_firm(
        FirmRecord(firm_key="a", allocation=D("100000"), initial_allocation=D("100000"),
                   cash=D("100000"), universe=["SPY"])
    )
    allocator = Allocator(store, config, gate)
    change = allocator.plan([card_for(firm, 95)], [firm])[0]
    assert change.new_allocation == Decimal("105000.00")


def test_cuts_stop_at_the_minimum(store, gate):
    config = BrokerageConfig(min_allocation=D("95000"))
    firm = store.upsert_firm(
        FirmRecord(firm_key="a", allocation=D("100000"), initial_allocation=D("100000"),
                   cash=D("100000"), universe=["SPY"])
    )
    allocator = Allocator(store, config, gate)
    change = allocator.plan([card_for(firm, 5)], [firm])[0]
    assert change.new_allocation == Decimal("95000.00")


# =========================================================================
# when capital may move — the thirteen cuts in four seconds
# =========================================================================
def test_capital_moves_once_per_bar_however_often_the_loop_runs(store, gate, trading_config):
    """The bug this file exists to keep fixed.

    ``firm_a_etf`` took thirteen 10% cuts inside four wall-clock seconds and
    came out holding 25.4% of its capital (0.9^13 = 0.254). Nothing about the
    firm changed in those four seconds; the tick loop simply asked the same
    question thirteen times, and the allocator charged for each answer.
    """
    firm = store.upsert_firm(
        FirmRecord(firm_key="a", allocation=D("100000"), initial_allocation=D("100000"),
                   cash=D("100000"), universe=["SPY"])
    )
    allocator = Allocator(store, trading_config.brokerage, gate)
    bar = "2026-08-14"

    for _ in range(13):
        current = store.require_firm_by_id(firm.id)
        allocator.apply(allocator.plan([card_for(current, 10)], [current], bar))

    # One cut, not thirteen. 0.9^13 would have left $25,418.66.
    assert store.require_firm_by_id(firm.id).allocation == Decimal("90000.00")

    # The next bar is a new judgement, and is allowed to act.
    current = store.require_firm_by_id(firm.id)
    allocator.apply(allocator.plan([card_for(current, 10)], [current], "2026-08-15"))
    assert store.require_firm_by_id(firm.id).allocation == Decimal("81000.00")


def test_a_deferred_cut_still_uses_up_the_bar(store, gate, trading_config):
    """"Not today" is an answer. Re-asking four seconds later cannot improve it."""
    firm = store.upsert_firm(
        FirmRecord(firm_key="a", allocation=D("100000"), initial_allocation=D("100000"),
                   cash=D("0"), universe=["SPY"])
    )
    allocator = Allocator(store, trading_config.brokerage, gate)
    assert allocator.plan([card_for(firm, 10)], [firm], "2026-08-14") == []

    # Cash frees up mid-bar. The firm has still had its review for this bar,
    # so the cut waits for the next one rather than firing the moment the
    # money lands — which, at one tick a second, is the compounding again.
    firm.cash = D("100000")
    store.upsert_firm(firm)
    assert allocator.plan([card_for(firm, 10)], [firm], "2026-08-14") == []
    assert allocator.plan([card_for(firm, 10)], [firm], "2026-08-15") != []


def test_a_paused_firm_is_not_cut_for_a_score_it_cannot_change(store, gate, trading_config):
    """All thirteen cuts landed on a firm the kill switch had already paused.

    A paused firm is not trading, so its score is frozen, so it qualifies for a
    cut on every bar from now until the minimum. That is a ratchet with no exit.
    Reclaiming its capital is still correct — but that is ``release()``, one
    deliberate act with a reason on it, not a decay.
    """
    firm = store.upsert_firm(
        FirmRecord(firm_key="a", allocation=D("100000"), initial_allocation=D("100000"),
                   cash=D("100000"), universe=["SPY"], status=FirmStatus.PAUSED.value)
    )
    allocator = Allocator(store, trading_config.brokerage, gate)
    assert allocator.plan([card_for(firm, 10)], [firm], "2026-08-14") == []
    assert store.require_firm_by_id(firm.id).allocation == Decimal("100000.00")

    # release() is still available, and still takes the money back.
    change = allocator.release(firm, "stopped trading")
    assert change.applied is True


# =========================================================================
# leaderboard
# =========================================================================
def test_the_leaderboard_ranks_measured_firms_above_unmeasured_ones(store):
    firms = [
        store.upsert_firm(FirmRecord(firm_key=k, allocation=D("1000"), universe=["SPY"]))
        for k in ("a", "b", "c")
    ]
    cards = [
        Scorecard(firm_key="a", firm_id=firms[0].id, score=D("40"), sufficient_data=True),
        Scorecard(firm_key="b", firm_id=firms[1].id, score=D("99"), sufficient_data=False),
        Scorecard(firm_key="c", firm_id=firms[2].id, score=D("60"), sufficient_data=True),
    ]
    board = Leaderboard(cards, firms)
    assert [r.firm_key for r in board.rows] == ["c", "a", "b"]
    assert board.rows[2].state == "measuring"
    assert "(99.00)" in board.render()


def test_killed_firms_rank_last(store):
    firms = [
        store.upsert_firm(FirmRecord(firm_key="a", allocation=D("1000"), universe=["SPY"])),
        store.upsert_firm(
            FirmRecord(firm_key="b", allocation=D("1000"), universe=["SPY"], status="killed")
        ),
    ]
    cards = [
        Scorecard(firm_key="a", firm_id=firms[0].id, score=D("10"), sufficient_data=True),
        Scorecard(firm_key="b", firm_id=firms[1].id, score=D("99"), sufficient_data=True),
    ]
    assert [r.firm_key for r in Leaderboard(cards, firms).rows] == ["a", "b"]


# =========================================================================
# the brokerage cycle
# =========================================================================
def test_oversight_refuses_to_run_on_broken_books(store, firm_record, market, trading_config, gate):
    store.db.update("firms", firm_record.id, {"cash": Decimal("1.00")})
    brokerage = Brokerage(store, trading_config, gate)
    with pytest.raises(LedgerNotReconciled):
        brokerage.oversee(market)
    # The failure itself is recorded.
    assert store.events(event_type="reconcile_failed")


def test_oversight_scores_persists_and_ranks(store, firm_record, market, trading_config, gate):
    report = Brokerage(store, trading_config, gate).oversee(market)
    assert report.reconciliation.ok
    assert len(report.cards) == 1
    assert store.latest_performance(firm_record.id) is not None
    assert report.leaderboard.rows[0].firm_key == "test_firm"


def test_a_tripped_firm_takes_a_strike_and_is_suspended_not_killed(
    store, firm_record, market, trading_config, gate
):
    """First offence is a sentence, not an execution.

    This used to request a kill on the first trip. It no longer does, and the
    change is the point: six firms were destroyed in nine days on a kill
    counter that was counting fills instead of bars, and every one of those
    kills was wrong. A village whose only answer to a bad fortnight is death
    gets quieter each time it is mistaken. See firms/strikes.py.
    """
    # Force a drawdown big enough to trip the switch regardless of trade count.
    store.update_firm_fields(firm_record.id, high_water_mark=Decimal("500000.00"))
    report = Brokerage(store, trading_config, gate).oversee(market)

    assert report.paused and report.paused[0]["firm"] == "test_firm"
    assert store.require_firm_by_id(firm_record.id).status == FirmStatus.PAUSED.value
    assert not store.require_firm_by_id(firm_record.id).is_killed

    state = store.strike_state(firm_record.id)
    assert state["strikes"] == 1
    assert state["gulag_bars_left"] > 0, "a strike must carry a sentence"
    assert state["breach_open"] == 1, "the slump must be marked, or it strikes again"

    # No kill on the first strike — that is what the third one is for.
    assert not [p for p in gate.pending()
                if p.action == ApprovalAction.KILL_FIRM.value]


def test_the_third_strike_requests_the_kill(
    store, firm_record, market, trading_config, gate
):
    store.update_firm_fields(firm_record.id, high_water_mark=Decimal("500000.00"))
    brokerage = Brokerage(store, trading_config, gate)
    for _ in range(3):
        brokerage.oversee(market)
        # Out of breach and back in: three separate slumps, not one long one.
        store.set_breach_open(firm_record.id, 0)
        store.release_from_gulag(firm_record.id)
        store.set_firm_status(firm_record.id, FirmStatus.ACTIVE.value)

    assert store.strike_state(firm_record.id)["strikes"] == 3
    pending = [p for p in gate.pending()
               if p.action == ApprovalAction.KILL_FIRM.value]
    assert pending, "the third strike must ask for the kill"


def test_an_approved_kill_closes_the_firm_and_returns_the_cash(
    store, firm_record, trading_config, gate
):
    brokerage = Brokerage(store, trading_config, gate)
    result = brokerage.kill_firm("test_firm", "approved")
    firm = store.require_firm_by_id(firm_record.id)
    assert firm.is_killed
    assert firm.cash == Decimal("0.00")
    assert result["returned"] == "100000.00"


def test_a_killed_firm_cannot_be_resumed(store, firm_record, trading_config, gate):
    brokerage = Brokerage(store, trading_config, gate)
    brokerage.kill_firm("test_firm", "approved")
    with pytest.raises(ValueError):
        brokerage.resume_firm("test_firm", "robbie")


def test_a_paused_firm_can_be_resumed_by_a_human(store, firm_record, trading_config, gate):
    store.set_firm_status(firm_record.id, FirmStatus.PAUSED.value)
    Brokerage(store, trading_config, gate).resume_firm("test_firm", "robbie")
    assert store.require_firm_by_id(firm_record.id).is_active


def test_kill_all_fires_when_every_firm_is_dead(store, firm_record, market, trading_config, gate):
    store.set_firm_status(firm_record.id, FirmStatus.KILLED.value, "dead")
    brokerage = Brokerage(store, trading_config, gate)
    cards = brokerage.evaluator.evaluate_all(store.firms(), market)
    halted, reason, _ = brokerage.kill_switch.check(cards)
    assert halted and "killed" in reason


def test_kill_all_pauses_everything_and_asks(store, firm_record, market, trading_config, gate):
    brokerage = Brokerage(store, trading_config, gate)
    cards = brokerage.evaluator.evaluate_all(store.firms(), market)
    state = brokerage.kill_switch.state(cards)
    result = brokerage.kill_switch.halt("testing", state)
    assert result["paused"] == ["test_firm"]
    assert store.require_firm_by_id(firm_record.id).status == FirmStatus.PAUSED.value
    assert any(a.action == ApprovalAction.KILL_ALL.value for a in gate.pending())


def test_a_halted_ecosystem_does_not_reallocate(store, firm_record, market, trading_config, gate):
    store.set_firm_status(firm_record.id, FirmStatus.KILLED.value, "dead")
    report = Brokerage(store, trading_config, gate).oversee(market)
    assert report.halted is not None
    assert report.allocation_changes == []
