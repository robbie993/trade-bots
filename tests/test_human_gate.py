"""Spec component 4 / section 6 — the human approval gate.

The property under test throughout: nothing that costs money or changes the
storefront can happen without a row a human changed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.human_gate import ApprovalAction, ApprovalRequired, ApprovalStatus

from .conftest import make_experiment


@pytest.fixture
def experiment(ledger):
    return ledger.create_experiment(make_experiment())


def test_a_request_is_pending_and_grants_nothing(gate, experiment):
    approval = gate.request_ad_spend(experiment, Decimal("10"))
    assert approval.status == ApprovalStatus.PENDING.value
    assert gate.has_approval(ApprovalAction.AD_SPEND.value, experiment.id) is False
    with pytest.raises(ApprovalRequired):
        gate.require_approval(ApprovalAction.AD_SPEND.value, experiment.id)


def test_approval_unlocks_the_action(gate, experiment):
    approval = gate.request_ad_spend(experiment, Decimal("10"))
    gate.approve(approval.id, "robbie")
    assert gate.has_approval(ApprovalAction.AD_SPEND.value, experiment.id) is True
    assert gate.require_approval(ApprovalAction.AD_SPEND.value, experiment.id).id == approval.id


def test_an_approved_amount_is_a_ceiling_not_a_suggestion(gate, experiment):
    approval = gate.request_ad_spend(experiment, Decimal("10"))
    gate.approve(approval.id)
    assert gate.has_approval(ApprovalAction.AD_SPEND.value, experiment.id, Decimal("10")) is True
    assert gate.has_approval(ApprovalAction.AD_SPEND.value, experiment.id, Decimal("80")) is False
    with pytest.raises(ApprovalRequired):
        gate.require_approval(ApprovalAction.AD_SPEND.value, experiment.id, Decimal("80"))


def test_spend_above_the_scale_threshold_is_a_different_kind_of_approval(gate, experiment):
    """$50/day is the line in spec section 6; above it the operator is asked
    to approve a *scale-up*, not a routine ad buy."""
    small = gate.request_ad_spend(experiment, Decimal("25"))
    assert small.action == ApprovalAction.AD_SPEND.value
    big = gate.request_ad_spend(experiment, Decimal("80"))
    assert big.action == ApprovalAction.SCALE_AD_SPEND.value


def test_approving_ad_spend_does_not_approve_a_kill(gate, experiment):
    approval = gate.request_ad_spend(experiment, Decimal("10"))
    gate.approve(approval.id)
    assert gate.has_approval(ApprovalAction.KILL_EXPERIMENT.value, experiment.id) is False


def test_approval_for_one_experiment_does_not_cover_another(gate, ledger, experiment):
    other = ledger.create_experiment(make_experiment(product_id="test-2"))
    approval = gate.request_ad_spend(experiment, Decimal("10"))
    gate.approve(approval.id)
    assert gate.has_approval(ApprovalAction.AD_SPEND.value, other.id) is False


def test_rejection_does_not_unlock(gate, experiment):
    approval = gate.request_ad_spend(experiment, Decimal("10"))
    gate.reject(approval.id, "robbie", "too expensive")
    assert gate.has_approval(ApprovalAction.AD_SPEND.value, experiment.id) is False


def test_a_decision_cannot_be_made_twice(gate, experiment):
    approval = gate.request_ad_spend(experiment, Decimal("10"))
    gate.approve(approval.id)
    with pytest.raises(ValueError):
        gate.reject(approval.id)


def test_duplicate_requests_do_not_spam_the_operator(gate, notifier, experiment):
    first = gate.request_ad_spend(experiment, Decimal("10"))
    second = gate.request_ad_spend(experiment, Decimal("10"))
    assert first.id == second.id
    assert len(notifier.sent) == 1


def test_kill_notification_matches_the_spec_format(gate, notifier, experiment):
    gate.request_kill(experiment, "CTR too low")
    subject, body = notifier.sent[0]
    assert "KILL TRIGGERED" in subject
    assert "🚨 EXPERIMENT KILL TRIGGERED" in body
    for label in ("Impressions:", "Sessions:", "Orders:", "CTR:", "Conversion:", "CAC:",
                  "Contribution Margin:", "Refund Rate:"):
        assert label in body
    assert "KILL or CONTINUE" in body
    assert "not killed yet" in body.lower()


def test_kill_button_maps_to_approve_and_continue_to_reject(gate, ledger, experiment):
    approval = gate.request_kill(experiment, "CTR too low")
    assert gate.decide(approval.id, "KILL").status == ApprovalStatus.APPROVED.value

    other = ledger.create_experiment(make_experiment(product_id="test-4"))
    second = gate.request_kill(other, "CTR too low")
    assert gate.decide(second.id, "CONTINUE").status == ApprovalStatus.REJECTED.value


def test_pending_lists_only_undecided_requests(gate, ledger, experiment):
    a = gate.request_ad_spend(experiment, Decimal("10"))
    other = ledger.create_experiment(make_experiment(product_id="test-3"))
    gate.request_sample(other, 1, Decimal("10"))
    gate.approve(a.id)
    assert [p.action for p in gate.pending()] == [ApprovalAction.ORDER_SAMPLE.value]


def test_sample_request_prices_the_whole_order(gate, experiment):
    approval = gate.request_sample(experiment, 3, Decimal("12.50"))
    assert approval.amount == Decimal("37.50")
    assert approval.details["units"] == 3


def test_platform_requests_are_not_tied_to_an_experiment(gate):
    approval = gate.request_platform("wish")
    assert approval.experiment_id is None
    assert gate.find_pending(ApprovalAction.ADD_PLATFORM.value, None).id == approval.id


def test_unknown_decision_is_rejected(gate, experiment):
    approval = gate.request_ad_spend(experiment, Decimal("10"))
    with pytest.raises(ValueError):
        gate.decide(approval.id, "maybe")
