"""The whole loop, end to end — and the order it refuses to break."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.human_gate import ApprovalAction
from src.trading.brokerage.reconciliation import LedgerNotReconciled
from src.trading.models import FirmStatus, ProposalStatus


def test_init_creates_the_configured_firms(ecosystem):
    firms = {f.firm_key: f for f in ecosystem.store.firms()}
    assert set(firms) == {"alpha", "beta"}
    assert firms["alpha"].allocation == Decimal("50000.00")
    assert firms["alpha"].cash == Decimal("50000.00")
    assert firms["alpha"].universe == ["SPY", "QQQ"]


def test_init_never_refunds_an_existing_firm(ecosystem):
    ecosystem.store.set_allocation(
        ecosystem.store.get_firm("alpha").id, Decimal("10000"), Decimal("-40000")
    )
    ecosystem.init_firms()
    assert ecosystem.store.get_firm("alpha").allocation == Decimal("10000.00")


def test_init_does_update_the_mandate(ecosystem, firms_yaml):
    firms_yaml.write_text(
        firms_yaml.read_text().replace('name: "Alpha"', 'name: "Alpha Renamed"')
    )
    ecosystem._specs.clear()
    ecosystem.init_firms()
    assert ecosystem.store.get_firm("alpha").name == "Alpha Renamed"


def test_a_tick_runs_the_whole_loop(ecosystem):
    report = ecosystem.tick(ecosystem.market().seek(150))
    assert report.errors == []
    assert report.oversight is not None
    assert report.oversight.reconciliation.ok
    assert len(report.oversight.cards) == 2
    assert report.summary()


def test_a_tick_leaves_the_books_reconciled(ecosystem):
    for step in (120, 140, 160):
        ecosystem.tick(ecosystem.market().seek(step))
    assert ecosystem.brokerage.reconcile(ecosystem.market()).ok


def test_fills_move_cash_positions_and_the_proposal_row(ecosystem):
    ecosystem.tick(ecosystem.market().seek(150))
    firm = ecosystem.store.get_firm("alpha")
    fills = ecosystem.store.fills(firm.id)
    if not fills:
        pytest.skip("no trade was proposed on this bar")
    assert firm.cash < Decimal("50000.00")
    assert ecosystem.store.positions(firm.id)
    filled = [
        p for p in ecosystem.store.proposals(firm.id) if p.status == ProposalStatus.FILLED.value
    ]
    assert filled


def test_every_proposal_is_stored_even_when_refused(ecosystem):
    ecosystem.tick(ecosystem.market().seek(150))
    proposals = ecosystem.store.proposals()
    assert proposals
    for proposal in proposals:
        assert proposal.risk_verdict  # a verdict is always recorded
        assert proposal.ethics_verdict


def test_the_audit_vault_is_written(ecosystem):
    ecosystem.tick(ecosystem.market().seek(150))
    vault = ecosystem.config.audit_vault
    assert (vault / "index.md").exists()
    assert list((vault / "brokerage").glob("*.md"))
    index = (vault / "index.md").read_text()
    assert "Trading Edition" in index


def test_a_failed_reconciliation_stops_the_tick(ecosystem):
    ecosystem.tick(ecosystem.market().seek(150))
    firm = ecosystem.store.get_firm("alpha")
    ecosystem.store.db.update("firms", firm.id, {"cash": Decimal("123456.00")})
    report = ecosystem.tick(ecosystem.market().seek(151))
    assert report.errors
    assert "RECONCILIATION FAILED" in report.errors[0]
    # Nothing downstream ran.
    assert report.oversight is None
    assert report.lessons == 0


def test_a_broken_ledger_also_stops_evolution(ecosystem):
    firm = ecosystem.store.get_firm("alpha")
    ecosystem.store.db.update("firms", firm.id, {"cash": Decimal("1.00")})
    with pytest.raises(LedgerNotReconciled):
        ecosystem.evolve()


def test_a_simulation_drives_the_loop_over_history(ecosystem):
    reports = ecosystem.simulate(days=20)
    assert len(reports) == 20
    assert all(r.oversight is not None for r in reports)
    assert ecosystem.brokerage.reconcile(ecosystem.market()).ok


def test_a_simulation_never_drives_cash_negative(ecosystem):
    ecosystem.simulate(days=40)
    for firm in ecosystem.store.firms():
        assert firm.cash >= 0


def test_backtest_writes_nothing(ecosystem):
    before = ecosystem.store.fills()
    results = ecosystem.backtest()
    assert len(results) == 2
    assert ecosystem.store.fills() == before


def test_an_approved_kill_is_carried_out_once(ecosystem):
    firm = ecosystem.store.get_firm("alpha")
    approval = ecosystem.brokerage.request_kill(firm, "testing")
    ecosystem.gate.approve(approval.id, approved_by="robbie")

    assert ecosystem.apply_approvals() == [
        "killed alpha (returned 50000.00)"
    ]
    assert ecosystem.store.get_firm("alpha").status == FirmStatus.KILLED.value
    # Idempotent: a second pass must not kill it (or return its cash) again.
    assert ecosystem.apply_approvals() == []


def test_an_approved_allocation_increase_is_carried_out(ecosystem):
    approval = ecosystem.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value,
        "raise alpha",
        details={"firm": "alpha", "new_allocation": "55000"},
        dedupe_key="allocation:alpha",
    )
    ecosystem.gate.approve(approval.id, approved_by="robbie")
    ecosystem.apply_approvals()
    assert ecosystem.store.get_firm("alpha").allocation == Decimal("55000.00")


def test_pending_approvals_are_not_carried_out(ecosystem):
    ecosystem.brokerage.request_kill(ecosystem.store.get_firm("alpha"), "testing")
    assert ecosystem.apply_approvals() == []
    assert ecosystem.store.get_firm("alpha").is_active


def test_requesting_a_live_venue_sends_no_order(ecosystem):
    approval = ecosystem.request_live_trading("alpaca")
    assert approval.action == ApprovalAction.LIVE_TRADING.value
    assert ecosystem.store.fills() == []


def test_paper_needs_no_live_approval(ecosystem):
    with pytest.raises(ValueError):
        ecosystem.request_live_trading("paper")


def test_a_firm_on_an_unapproved_live_venue_refuses_to_trade(ecosystem):
    firm = ecosystem.store.get_firm("alpha")
    ecosystem.store.update_firm_fields(firm.id, venue="alpaca")
    report = ecosystem.tick(ecosystem.market().seek(150))
    assert ecosystem.store.fills(firm.id) == []
    if report.refused_by_venue:
        assert "no approved live_trading" in report.refused_by_venue[0]


def test_status_reports_the_whole_ecosystem(ecosystem):
    ecosystem.tick(ecosystem.market().seek(150))
    status = ecosystem.status()
    assert status["firms"] == 2
    assert status["reconciled"] is True
    assert status["data_source"] == "synthetic"
    assert status["gateway"]["enabled"] is False


def test_the_audit_report_covers_firms_ethics_and_events(ecosystem):
    ecosystem.tick(ecosystem.market().seek(150))
    report = ecosystem.audit_report()
    assert "# Trading ecosystem audit" in report
    assert "## Reconciliation" in report
    assert "alpha" in report
    assert "## Ethics" in report


def test_a_killed_flat_firm_is_skipped(ecosystem):
    firm = ecosystem.store.get_firm("alpha")
    ecosystem.store.set_firm_status(firm.id, FirmStatus.KILLED.value, "dead")
    ecosystem.tick(ecosystem.market().seek(150))
    assert all(p.firm_id != firm.id for p in ecosystem.store.proposals())


def test_the_gateway_narrates_offline_without_a_network(ecosystem):
    from src.trading.models import TradeProposal

    result = ecosystem.gateway.narrate(
        TradeProposal(firm_id=1, symbol="SPY", quantity="1", reference_price="100")
    )
    assert result.source == "offline"
    assert "SPY" in result.text
