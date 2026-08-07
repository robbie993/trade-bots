"""Spec component 4 / section 4.4 — the web approval UI.

The property under test: the web tier can *decide*, and can do nothing else.
No route spends money, kills an experiment, or touches an ad platform.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from src.agents.human_gate import ApprovalStatus  # noqa: E402
from src.agents.web import app  # noqa: E402
from src.db import Database  # noqa: E402

from .conftest import make_experiment  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch, fixture_file):
    db_path = tmp_path / "web.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "notify.log"))
    monkeypatch.setenv("MVV_SCOUT_FIXTURE", str(fixture_file))
    monkeypatch.delenv("MVV_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("MVV_SMTP_HOST", raising=False)
    db = Database.from_url(f"sqlite:///{db_path}")
    db.init_schema()
    yield TestClient(app), db
    db.close()


def seed(db, **overrides):
    from src.agents.experiment_ledger import ExperimentLedger

    return ExperimentLedger(db).create_experiment(make_experiment(**overrides))


def gate_for(db):
    from src.agents.human_gate import HumanGate
    from src.notifications import NullNotifier

    return HumanGate(db, NullNotifier())


def test_index_renders_with_an_empty_database(client):
    http, db = client
    response = http.get("/")
    assert response.status_code == 200
    assert "Human Approval Gate" in response.text
    assert "Nothing waiting on you" in response.text


def test_index_warns_that_there_is_no_authentication(client):
    http, db = client
    assert "no authentication" in http.get("/").text


def test_a_pending_kill_shows_the_spec_4_4_buttons(client):
    http, db = client
    exp = seed(db, impressions=200_000, clicks=100)
    gate_for(db).request_kill(exp, "CTR too low")

    body = http.get("/").text
    assert "KILL" in body
    assert "CONTINUE" in body
    assert "REVIEW_DATA" in body
    # The numbers that justified the alert are on the page, per spec 4.4.
    for label in ("Impressions", "Sessions", "Orders", "CTR", "Conversion", "CAC",
                  "Contribution", "Refund rate"):
        assert label in body
    assert "not killed yet" in body


def test_the_kill_button_records_a_decision_but_does_not_kill(client):
    """The web tier has no privileged path into the ledger: it writes an
    approval, and the orchestrator acts on it later."""
    http, db = client
    exp = seed(db, impressions=200_000, clicks=100)
    approval = gate_for(db).request_kill(exp, "CTR too low")

    response = http.post(f"/approvals/{approval.id}/approve", data={"approved_by": "robbie"})
    assert response.status_code == 200  # followed the redirect

    row = db.query_one("SELECT status, approved_by FROM human_approvals WHERE id = ?", (approval.id,))
    assert row["status"] == ApprovalStatus.APPROVED.value
    assert row["approved_by"] == "robbie"
    # The experiment itself is untouched.
    assert db.query_one("SELECT status FROM experiments WHERE id = ?", (exp.id,))["status"] != "killed"


def test_the_continue_button_rejects_the_kill_request(client):
    http, db = client
    exp = seed(db, impressions=200_000, clicks=100)
    approval = gate_for(db).request_kill(exp, "CTR too low")

    http.post(f"/approvals/{approval.id}/reject", data={"approved_by": "robbie"})
    row = db.query_one("SELECT status FROM human_approvals WHERE id = ?", (approval.id,))
    assert row["status"] == ApprovalStatus.REJECTED.value


def test_deciding_twice_does_not_500(client):
    http, db = client
    exp = seed(db)
    approval = gate_for(db).request_ad_spend(exp, Decimal("10"))
    assert http.post(f"/approvals/{approval.id}/approve").status_code == 200
    assert http.post(f"/approvals/{approval.id}/reject").status_code == 200
    row = db.query_one("SELECT status FROM human_approvals WHERE id = ?", (approval.id,))
    assert row["status"] == ApprovalStatus.APPROVED.value  # first decision stands


def test_review_data_shows_every_kill_condition(client):
    http, db = client
    exp = seed(db, impressions=200_000, clicks=100)
    body = http.get(f"/experiments/{exp.id}").text
    assert "Kill conditions" in body
    assert "YES" in body  # CTR fired
    assert "MET — decisions are valid" in body
    assert "Refund rate" in body  # the conditions that did *not* fire are shown too


def test_review_data_on_a_missing_experiment(client):
    http, db = client
    assert "No such experiment" in http.get("/experiments/999").text


def test_ad_spend_request_offers_approve_and_reject_not_kill(client):
    http, db = client
    exp = seed(db)
    gate_for(db).request_ad_spend(exp, Decimal("10"))
    body = http.get("/").text
    assert "Approve" in body and "Reject" in body


def test_health_api_reports_the_stop_conditions(client):
    http, db = client
    payload = http.get("/api/health").json()
    assert payload["kill_all"] is False
    assert len(payload["stop_conditions"]) == 4
    assert "alerts" in payload and "cash" in payload


def test_health_api_flags_kill_all(client):
    http, db = client
    for i in range(20):
        seed(db, product_id=f"loss-{i}", revenue=Decimal("0"), ad_spend=Decimal("900"))
    payload = http.get("/api/health").json()
    assert payload["kill_all"] is True


def test_a_fired_stop_condition_is_shown_on_the_front_page(client):
    http, db = client
    for i in range(20):
        seed(db, product_id=f"loss-{i}", revenue=Decimal("0"), ad_spend=Decimal("900"))
    assert "KILL_ALL" in http.get("/").text


def test_product_names_are_escaped(client):
    """Supplier-supplied text reaches this page; it must not be markup."""
    http, db = client
    seed(db, product_name="<script>alert(1)</script>")
    body = http.get("/").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
