"""The council — the village deciding what the approval gate used to hold.

The question these tests exist to answer is not "does it approve things". It is
whether a body that can grant itself capital is still constrained by the same
facts a human was reading. So: the vetoes fire, the evidence is what decides,
an unsettled case still comes back to a person, and the one irreversible act in
the system is not on the menu at all.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.agents.human_gate import ApprovalAction, ApprovalStatus
from src.config import Config
from src.money import D
from src.trading.config import AutonomyConfig, DataConfig, TradingConfig
from src.trading.council import Council, CouncilEvidence, gather
from src.trading.council.council import DEFER, GRANT, PANELS, REFUSE, digest_of
from src.trading.ecosystem import Ecosystem


# =========================================================================
# fixtures
# =========================================================================
@pytest.fixture
def council():
    return Council()


@pytest.fixture
def autonomous(db, tmp_path, firms_yaml, notifier):
    """An ecosystem that decides for itself."""
    config = TradingConfig(
        firms_config=firms_yaml,
        audit_vault=tmp_path / "vault",
        vendor_dir=tmp_path / "vendor",
        data=DataConfig(source="synthetic", seed=12345, history_days=180),
        autonomy=AutonomyConfig(mode="council"),
    )
    eco = Ecosystem(
        db,
        config,
        Config(database_url=db.url, notification_log=tmp_path / "notifications.log"),
        notifier,
    )
    eco.init_firms()
    return eco


def raise_evidence(**overrides) -> CouncilEvidence:
    """A firm that plainly deserves a raise, before the test spoils it."""
    base = dict(
        action="allocate_capital",
        firm_key="alpha",
        firm_exists=True,
        status="active",
        books_reconcile=True,
        sufficient_data=True,
        closed_trades=30,
        score=D("75"),
        return_pct=D("12"),
        drawdown_pct=D("3"),
        win_rate_pct=D("60"),
        allocation=D("50000"),
        initial_allocation=D("50000"),
        new_allocation=D("55000"),
        headroom=D("100000"),
        max_allocation=D("150000"),
        good_score=D("60"),
        poor_score=D("40"),
    )
    base.update(overrides)
    return CouncilEvidence(**base)


def kill_evidence(**overrides) -> CouncilEvidence:
    base = dict(
        action="kill_firm",
        firm_key="alpha",
        firm_exists=True,
        status="paused",
        books_reconcile=True,
        sufficient_data=True,
        closed_trades=30,
        drawdown_pct=D("45"),
        tripped=("Drawdown",),
        open_positions=0,
        notes={"kill_condition_met": True, "kill_reason": "Drawdown 45% exceeds 40%"},
    )
    base.update(overrides)
    return CouncilEvidence(**base)


def resume_evidence(**overrides) -> CouncilEvidence:
    base = dict(
        action="resume_firm",
        firm_key="alpha",
        firm_exists=True,
        status="paused",
        books_reconcile=True,
        tripped=(),
        drawdown_pct=D("4"),
        withdrawable=D("9000"),
    )
    base.update(overrides)
    return CouncilEvidence(**base)


# =========================================================================
# the boundary
# =========================================================================
def test_the_council_has_no_panel_for_live_trading():
    """The one act that leaves the system is not the council's to authorise.

    Not a setting — there is no panel, so there is no code path that reaches a
    verdict on it however the configuration is written.
    """
    assert ApprovalAction.LIVE_TRADING.value not in PANELS


def test_a_live_trading_request_is_left_for_a_human(council):
    ruling = council.rule(CouncilEvidence(action=ApprovalAction.LIVE_TRADING.value))
    assert ruling.verdict == DEFER
    assert "no panel" in ruling.reason


def test_an_unknown_action_is_never_granted_by_default(council):
    """Adding an approval action must not silently make it autonomous."""
    ruling = council.rule(CouncilEvidence(action="something_invented_later"))
    assert ruling.verdict == DEFER


def test_the_tick_skips_live_trading_requests(autonomous, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "x")
    monkeypatch.setenv("ALPACA_API_SECRET", "y")
    approval = autonomous.request_live_trading("alpaca", "let me trade")
    autonomous.hold_council()
    assert autonomous.gate.get(approval.id).status == ApprovalStatus.PENDING.value
    assert autonomous.council.recent() == []


# =========================================================================
# the vetoes
# =========================================================================
def test_broken_books_stop_every_decision(council):
    ruling = council.rule(raise_evidence(books_reconcile=False, reconciliation="cash off by 12.00"))
    assert ruling.verdict == DEFER
    assert "books" in ruling.vetoes


def test_a_raise_past_the_brokerage_cap_is_refused(council):
    ruling = council.rule(raise_evidence(headroom=D("100")))
    assert ruling.verdict == DEFER
    assert "headroom" in ruling.vetoes


def test_a_raise_past_the_firms_ceiling_is_refused(council):
    ruling = council.rule(raise_evidence(new_allocation=D("200000")))
    assert ruling.verdict == DEFER
    assert "ceiling" in ruling.vetoes


def test_the_council_will_not_compound(council):
    """Granting the same firm a raise every tick is the failure being prevented."""
    ruling = council.rule(raise_evidence(recent_council_grants=3))
    assert ruling.verdict == DEFER
    assert "compounding" in ruling.vetoes
    assert "needs a human" in ruling.reason


def test_below_the_sample_gate_nothing_is_granted(council):
    ruling = council.rule(raise_evidence(sufficient_data=False, closed_trades=4))
    assert ruling.verdict == DEFER
    assert "sample_gate" in ruling.vetoes


def test_a_missing_firm_stops_the_decision(council):
    ruling = council.rule(raise_evidence(firm_exists=False))
    assert ruling.verdict == DEFER
    assert "firm_exists" in ruling.vetoes


# =========================================================================
# raising an allocation
# =========================================================================
def test_a_clear_winner_gets_its_raise(council):
    ruling = council.rule(raise_evidence())
    assert ruling.verdict == GRANT
    assert ruling.for_weight > ruling.against_weight


def test_a_loser_is_refused_its_raise(council):
    ruling = council.rule(
        raise_evidence(score=D("20"), return_pct=D("-8"), drawdown_pct=D("25"),
                       win_rate_pct=D("30"))
    )
    assert ruling.verdict == REFUSE


def test_a_close_call_comes_back_to_a_human(council):
    """The verdict that keeps the council honest."""
    ruling = council.rule(
        raise_evidence(score=D("50"), return_pct=D("1"), drawdown_pct=D("12"),
                       win_rate_pct=D("35"))
    )
    assert ruling.verdict == DEFER
    assert "waits for a human" in ruling.reason


# =========================================================================
# killing and resuming
# =========================================================================
def test_a_kill_needs_the_ledgers_conclusion_not_the_councils(council):
    ruling = council.rule(
        kill_evidence(tripped=(), notes={"kill_condition_met": False, "kill_reason": ""})
    )
    assert ruling.verdict == DEFER
    assert "kill_condition" in ruling.vetoes


def test_a_tripped_kill_condition_carries_the_kill(council):
    assert council.rule(kill_evidence()).verdict == GRANT


def test_a_drawdown_kill_is_not_held_up_by_the_sample_gate(council):
    """Drawdown is checked before the sample gate, and so is the council.

    A 45% drawdown on six trades is the account being emptied, not a weak
    statistical claim about an edge — demanding twenty trades first would
    reproduce exactly the mistake the kill switch avoids.
    """
    ruling = council.rule(kill_evidence(sufficient_data=False, closed_trades=6))
    assert "sample_gate" not in ruling.vetoes
    assert ruling.verdict == GRANT


def test_a_win_rate_kill_still_needs_the_sample_gate(council):
    """The statistical conditions keep their gate."""
    ruling = council.rule(
        kill_evidence(sufficient_data=False, closed_trades=6, tripped=("Win rate",),
                      notes={"kill_condition_met": True, "kill_reason": "Win rate 20%"})
    )
    assert ruling.verdict == DEFER
    assert "sample_gate" in ruling.vetoes


def test_a_firm_whose_condition_cleared_may_resume(council):
    assert council.rule(resume_evidence()).verdict == GRANT


def test_a_firm_still_tripping_may_not_resume(council):
    ruling = council.rule(resume_evidence(tripped=("Drawdown",)))
    assert ruling.verdict == DEFER
    assert "condition_cleared" in ruling.vetoes


def test_a_firm_with_no_cash_is_not_resumed(council):
    ruling = council.rule(resume_evidence(withdrawable=D("0"), drawdown_pct=D("30")))
    assert ruling.verdict == REFUSE


# =========================================================================
# capital between firms
# =========================================================================
def test_a_seller_who_cannot_pay_stops_a_transfer(council):
    ruling = council.rule(
        raise_evidence(kind="capital_transfer", from_firm="beta",
                       from_firm_withdrawable=D("0"))
    )
    assert ruling.verdict == DEFER
    assert "seller_can_pay" in ruling.vetoes


# =========================================================================
# it has to be reviewable
# =========================================================================
def test_the_same_evidence_always_gives_the_same_ruling(council):
    evidence = raise_evidence()
    first, second = council.rule(evidence), council.rule(evidence)
    assert (first.verdict, first.for_weight, first.against_weight) == (
        second.verdict, second.for_weight, second.against_weight
    )


def test_every_juror_is_recorded_with_its_reason(council):
    ruling = council.rule(raise_evidence())
    jurors = {f.juror for f in ruling.findings}
    assert {"books", "score", "drawdown", "headroom", "compounding"} <= jurors
    assert all(f.reason for f in ruling.findings)


def test_a_stored_ruling_can_be_replayed_from_its_own_evidence(autonomous):
    """The audit property: the row carries everything the verdict came from."""
    council = Council(autonomous.db)
    council.record(council.rule(raise_evidence()), approval_id=None)
    row = autonomous.db.query_one("SELECT * FROM council_rulings ORDER BY id DESC LIMIT 1")

    stored = json.loads(row["evidence"])
    rebuilt = CouncilEvidence(**{
        key: _restore(key, value) for key, value in stored.items()
    })
    assert Council().rule(rebuilt).verdict == row["verdict"]


def _restore(key, value):
    numeric = {
        "score", "return_pct", "drawdown_pct", "win_rate_pct", "equity", "allocation",
        "initial_allocation", "new_allocation", "headroom", "withdrawable",
        "from_firm_withdrawable", "good_score", "poor_score", "kill_score",
        "max_allocation",
    }
    if key in numeric:
        return D(value)
    if key in {"kill_conditions", "tripped"}:
        return tuple(value)
    return value


def test_an_unchanged_request_is_not_reheard(autonomous):
    """119 identical rulings for one decision is noise, not an audit trail."""
    council = Council(autonomous.db)
    approval = autonomous.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value, "Raise alpha", amount=D("5000"),
        details={"kind": "trading_allocation", "firm": "alpha"},
    )
    evidence = raise_evidence()
    council.record(council.rule(evidence), approval_id=approval.id)
    assert council.already_heard(approval.id, evidence) is True
    assert council.already_heard(approval.id, raise_evidence(score=D("80"))) is False


def test_the_digest_changes_when_the_facts_change():
    assert digest_of(raise_evidence()) != digest_of(raise_evidence(score=D("80")))


# =========================================================================
# end to end, through the gate
# =========================================================================
def test_in_human_mode_nothing_is_decided_automatically(ecosystem):
    """The default is unchanged: no rulings, no grants, everything waits."""
    assert ecosystem.config.autonomy.council_decides is False
    ecosystem.tick()
    assert ecosystem.hold_council() == []
    assert ecosystem.db.query("SELECT * FROM council_rulings") == []


def test_a_granted_raise_actually_moves_the_allocation(autonomous):
    """The council signs the same row a human would have signed."""
    firm = autonomous.store.get_firm("alpha")
    approval = autonomous.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value,
        "Raise alpha",
        amount=D("5000"),
        details={
            "kind": "trading_allocation",
            "firm": "alpha",
            "old_allocation": str(firm.allocation),
            "new_allocation": str(D(firm.allocation) + D("5000")),
            "reason": "test",
        },
    )
    # Stand the firm up as a clear winner so the panel has something to carry.
    _make_a_winner(autonomous, "alpha")

    rulings = autonomous.hold_council()
    assert [r.verdict for r in rulings] == [GRANT]
    assert autonomous.gate.get(approval.id).status == ApprovalStatus.APPROVED.value
    assert autonomous.gate.get(approval.id).approved_by == "the council"

    autonomous.apply_approvals()
    assert D(autonomous.store.get_firm("alpha").allocation) == D(firm.allocation) + D("5000")


def test_a_granted_resume_actually_un_pauses_the_firm(autonomous):
    firm = autonomous.store.get_firm("alpha")
    autonomous.store.set_firm_status(firm.id, "paused", "tripped something")

    asked = autonomous.request_resumes()
    assert [a.action for a in asked] == [ApprovalAction.RESUME_FIRM.value]

    autonomous.hold_council()
    autonomous.apply_approvals()
    assert autonomous.store.get_firm("alpha").status == "active"


def test_a_council_grant_is_written_down_with_its_reasons(autonomous):
    _make_a_winner(autonomous, "alpha")
    firm = autonomous.store.get_firm("alpha")
    autonomous.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value,
        "Raise alpha",
        amount=D("5000"),
        details={
            "kind": "trading_allocation", "firm": "alpha",
            "old_allocation": str(firm.allocation),
            "new_allocation": str(D(firm.allocation) + D("5000")),
        },
    )
    autonomous.hold_council()
    row = autonomous.db.query_one("SELECT * FROM council_rulings ORDER BY id DESC LIMIT 1")
    assert row["verdict"] == GRANT
    assert row["firm_key"] == "alpha"
    assert json.loads(row["findings"])
    assert json.loads(row["evidence"])["score"]


def test_a_deferred_decision_is_still_waiting_for_you(autonomous):
    """Autonomy narrows what you are asked, it does not remove you."""
    firm = autonomous.store.get_firm("alpha")
    approval = autonomous.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value,
        "Raise alpha",
        amount=D("5000"),
        details={
            "kind": "trading_allocation", "firm": "alpha",
            "old_allocation": str(firm.allocation),
            "new_allocation": str(D(firm.allocation) + D("5000")),
        },
    )
    # No trades: below the sample gate, which is a veto.
    rulings = autonomous.hold_council()
    assert rulings and rulings[0].verdict == DEFER
    assert autonomous.gate.get(approval.id).status == ApprovalStatus.PENDING.value


def test_the_books_being_broken_stops_the_council_too(autonomous):
    _make_a_winner(autonomous, "alpha")
    firm = autonomous.store.get_firm("alpha")
    autonomous.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value, "Raise alpha", amount=D("5000"),
        details={"kind": "trading_allocation", "firm": "alpha",
                 "old_allocation": str(firm.allocation),
                 "new_allocation": str(D(firm.allocation) + D("5000"))},
    )
    autonomous.db.execute(
        "UPDATE firms SET cash = ? WHERE firm_key = ?", (Decimal("1.00"), "alpha")
    )
    rulings = autonomous.hold_council()
    assert rulings and rulings[0].verdict == DEFER
    assert "books" in rulings[0].vetoes


def test_gathering_reads_the_ledger_not_the_request(autonomous):
    """A request that claims a firm is wonderful does not make it so."""
    firm = autonomous.store.get_firm("alpha")
    approval = autonomous.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value,
        "Raise alpha because it is the best firm ever, score 99",
        amount=D("5000"),
        details={"kind": "trading_allocation", "firm": "alpha", "score": "99",
                 "old_allocation": str(firm.allocation),
                 "new_allocation": str(D(firm.allocation) + D("5000"))},
    )
    evidence = gather(autonomous, autonomous.gate.get(approval.id))
    assert evidence.score != D("99")
    assert evidence.sufficient_data is False


# =========================================================================
# it has to be quiet enough to leave running
# =========================================================================
def test_a_request_the_council_will_grant_does_not_page_you_first(autonomous, notifier):
    """Being notified about a decision that is made two lines later is noise."""
    _make_a_winner(autonomous, "alpha")
    firm = autonomous.store.get_firm("alpha")
    autonomous.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value, "Raise alpha", amount=D("5000"),
        details={"kind": "trading_allocation", "firm": "alpha",
                 "old_allocation": str(firm.allocation),
                 "new_allocation": str(D(firm.allocation) + D("5000"))},
    )
    assert notifier.sent == []
    autonomous.hold_council()
    assert notifier.sent == []


def test_a_deferred_request_does_page_you(autonomous, notifier):
    """The one outcome that needs a person is the one allowed to interrupt them."""
    firm = autonomous.store.get_firm("alpha")
    autonomous.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value, "Raise alpha", amount=D("5000"),
        details={"kind": "trading_allocation", "firm": "alpha",
                 "old_allocation": str(firm.allocation),
                 "new_allocation": str(D(firm.allocation) + D("5000"))},
    )
    autonomous.hold_council()  # below the sample gate, so DEFER
    assert notifier.sent


def test_in_human_mode_every_request_still_notifies(ecosystem, notifier):
    """Turning autonomy off must put the notifications back exactly as they were."""
    assert ecosystem.gate.quiet_actions == set()
    ecosystem.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value, "Raise alpha", amount=D("5000"),
        details={"kind": "trading_allocation", "firm": "alpha"},
    )
    assert notifier.sent


def test_live_trading_is_never_quiet(autonomous):
    """The council silences only what it will actually rule on."""
    assert ApprovalAction.LIVE_TRADING.value not in autonomous.gate.quiet_actions


# =========================================================================
# a whole tick, running itself
# =========================================================================
def test_a_tick_in_council_mode_decides_and_carries_out(autonomous):
    """The wiring, not just the pieces: one tick, start to finish, no human."""
    _make_a_winner(autonomous, "alpha")
    firm = autonomous.store.get_firm("alpha")
    autonomous.store.set_firm_status(firm.id, "paused", "tripped something")

    report = autonomous.tick()

    assert any(r.verdict == GRANT for r in report.rulings)
    assert any("resumed alpha" in str(done) for done in report.carried_out)
    assert autonomous.store.get_firm("alpha").status == "active"
    assert autonomous.gate.pending() == []


def test_a_tick_in_human_mode_decides_nothing(ecosystem):
    report = ecosystem.tick()
    assert report.rulings == []
    assert report.carried_out == []


def _make_a_winner(eco, firm_key: str) -> None:
    """Give a firm a track record good enough to argue about.

    Written through the same store the tick uses rather than by poking rows, so
    what the evaluator reads back is a real set of closed trades.
    """
    from src.trading.models import Fill

    firm = eco.store.get_firm(firm_key)
    for index in range(24):
        eco.store.settle(
            firm,
            Fill(firm_id=firm.id, symbol="SPY", side="buy", quantity=D("1"),
                 price=D("100"), fee=D("0")),
        )
        firm = eco.store.require_firm_by_id(firm.id)
        eco.store.settle(
            firm,
            Fill(firm_id=firm.id, symbol="SPY", side="sell", quantity=D("1"),
                 price=D("110") if index % 5 else D("98"), fee=D("0")),
        )
        firm = eco.store.require_firm_by_id(firm.id)
