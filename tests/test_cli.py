"""The operator's path through the system, exercised as a real CLI session.

This is the acceptance test for Phase 1: discover -> approve sample ->
approve listing -> approve ads -> measure -> kill trigger -> human decides,
with the cash ledger tracking every move.
"""

from __future__ import annotations

import json

import pytest

from src.cli import main
from src.db import Database


@pytest.fixture
def env(tmp_path, monkeypatch, fixture_file):
    """A CLI environment pointed entirely at tmp_path."""
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "notify.log"))
    monkeypatch.setenv("MVV_SCOUT_FIXTURE", str(fixture_file))
    monkeypatch.setenv("MVV_SCOUT_SOURCE", "fixture")
    monkeypatch.delenv("MVV_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("MVV_SMTP_HOST", raising=False)
    assert main(["init-db"]) == 0
    return {"db_path": db_path, "tmp": tmp_path}


def db_of(env) -> Database:
    return Database.from_url(f"sqlite:///{env['db_path']}")


def test_init_db_creates_every_table(env, capsys):
    main(["init-db"])
    out = capsys.readouterr().out
    for table in ("experiments", "orders", "cash_flow", "approvals", "experiment_events"):
        assert table in out


def test_scout_writes_nothing(env, capsys):
    assert main(["scout"]) == 0
    out = capsys.readouterr().out
    assert "ae-1" in out
    assert "Nothing has been written" in out
    assert db_of(env).query_one("SELECT COUNT(*) AS n FROM experiments")["n"] == 0


def test_config_shows_the_risk_surface(env, capsys):
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    assert "emergency_buffer" in out
    assert "KILL_ALL after      20 experiments" in out


def test_full_operator_workflow(env, capsys):
    # 1. Fund the account.
    assert main(["cash", "open"]) == 0
    assert "$1,000.00" in capsys.readouterr().out

    # 2. First tick discovers products and asks to buy a sample.
    assert main(["tick"]) == 0
    capsys.readouterr()
    main(["approvals"])
    out = capsys.readouterr().out
    assert "sample_order" in out

    db = db_of(env)
    exp_id = db.query_one("SELECT id FROM experiments WHERE product_id = 'ae-1'")["id"]

    def approve_pending(action):
        row = db.query_one(
            "SELECT id FROM human_approvals WHERE action = ? AND status = 'pending' "
            "AND experiment_id = ?",
            (action, exp_id),
        )
        assert row is not None, f"expected a pending {action} request"
        assert main(["approve", str(row["id"]), "--by", "robbie"]) == 0
        capsys.readouterr()

    # 3. Approve each gate in turn; the loop advances one step per approval.
    approve_pending("sample_order")
    main(["tick"]); capsys.readouterr()
    approve_pending("launch")
    main(["tick"]); capsys.readouterr()
    approve_pending("ad_spend")
    main(["tick"]); capsys.readouterr()

    row = db.query_one("SELECT status, ads_paused, daily_ad_budget FROM experiments WHERE id = ?", (exp_id,))
    assert row["status"] == "launched"
    assert not row["ads_paused"]

    # 4. Two weeks later the operator types in what the dashboards say.
    assert main([
        "metrics", str(exp_id),
        "--impressions", "120000", "--clicks", "300", "--sessions", "800",
        "--orders", "60", "--revenue", "1300", "--ad-spend", "450",
    ]) == 0
    out = capsys.readouterr().out
    assert "sample gate: MET" in out
    assert "KILL TRIGGER: CTR too low" in out

    # 5. Evaluate pauses the ads and files a kill request. It does not kill.
    assert main(["evaluate"]) == 0
    assert "KILL TRIGGERED" in capsys.readouterr().out
    row = db.query_one("SELECT status, ads_paused, pending_kill_reason FROM experiments WHERE id = ?", (exp_id,))
    assert row["status"] == "launched"
    assert bool(row["ads_paused"]) is True
    assert row["pending_kill_reason"] == "CTR too low"

    # 6. The human decides.
    kill_request = db.query_one(
        "SELECT id FROM human_approvals WHERE action = 'kill' AND experiment_id = ?", (exp_id,)
    )
    assert main(["approve", str(kill_request["id"]), "--by", "robbie"]) == 0
    capsys.readouterr()
    assert main(["evaluate"]) == 0
    capsys.readouterr()

    row = db.query_one("SELECT status, kill_reason FROM experiments WHERE id = ?", (exp_id,))
    assert row["status"] == "killed"
    assert row["kill_reason"] == "CTR too low"

    # 7. The audit view explains the decision.
    assert main(["show", str(exp_id)]) == 0
    out = capsys.readouterr().out
    assert "KILLED: CTR too low" in out
    assert "MET — decisions are valid" in out
    assert "YES" in out  # the CTR row of the kill table


def test_metrics_below_the_gate_report_no_trigger(env, capsys):
    main(["cash", "open"])
    main(["tick"])
    capsys.readouterr()
    db = db_of(env)
    exp_id = db.query_one("SELECT id FROM experiments WHERE product_id = 'ae-1'")["id"]
    main(["metrics", str(exp_id), "--impressions", "500", "--clicks", "1", "--orders", "3"])
    out = capsys.readouterr().out
    assert "sample gate: not met" in out
    assert "KILL TRIGGER" not in out


def test_recording_an_order_moves_real_cash(env, capsys):
    main(["cash", "open"])
    main(["tick"])
    capsys.readouterr()
    db = db_of(env)
    exp_id = db.query_one("SELECT id FROM experiments WHERE product_id = 'ae-1'")["id"]

    assert main(["order", str(exp_id), "--customer-paid", "24.99", "--supplier-cost", "7.00"]) == 0
    out = capsys.readouterr().out
    assert "net contribution" in out
    # Supplier paid today, customer money still held by the processor.
    assert db.query_one("SELECT COUNT(*) AS n FROM orders")["n"] == 1
    categories = {r["category"] for r in db.query("SELECT category FROM cash_flow")}
    assert {"supplier_payment", "customer_revenue", "processor_hold", "processor_fee"} <= categories


def test_health_exits_nonzero_when_the_stop_condition_fires(env, capsys):
    main(["cash", "open"])
    db = db_of(env)
    for i in range(20):
        db.insert(
            "experiments",
            {"product_id": f"loss-{i}", "product_name": f"L{i}", "status": "active",
             "unit_cost": "10", "selling_price": "40", "revenue": "0", "ad_spend": "100",
             "orders": 0},
        )
    assert main(["health"]) == 2
    assert "KILL_ALL" in capsys.readouterr().out


def test_resume_refuses_without_an_approved_request(env, capsys):
    main(["cash", "open"])
    main(["tick"])
    capsys.readouterr()
    db = db_of(env)
    exp_id = db.query_one("SELECT id FROM experiments WHERE product_id = 'ae-1'")["id"]
    approval_id = db.query_one("SELECT id FROM human_approvals WHERE experiment_id = ?", (exp_id,))["id"]
    assert main(["resume", str(exp_id), "--approval-id", str(approval_id), "--budget", "10"]) == 1
    assert "not approved" in capsys.readouterr().err


def test_resume_refuses_while_a_kill_decision_is_outstanding(env, capsys):
    main(["cash", "open"])
    main(["tick"])
    capsys.readouterr()
    db = db_of(env)
    exp_id = db.query_one("SELECT id FROM experiments WHERE product_id = 'ae-1'")["id"]
    db.update("experiments", exp_id, {"pending_kill_reason": "CTR too low"})
    approval_id = db.query_one("SELECT id FROM human_approvals WHERE experiment_id = ?", (exp_id,))["id"]
    main(["approve", str(approval_id)])
    capsys.readouterr()
    assert main(["resume", str(exp_id), "--approval-id", str(approval_id), "--budget", "10"]) == 1
    assert "undecided kill trigger" in capsys.readouterr().err


def test_weekly_report_renders(env, capsys):
    main(["cash", "open"])
    main(["tick"])
    capsys.readouterr()
    assert main(["weekly"]) == 0
    out = capsys.readouterr().out
    assert "WEEKLY SUMMARY" in out
    assert "Phase 1 success criteria" in out


def test_list_shows_the_flag_column(env, capsys):
    main(["cash", "open"])
    main(["tick"])
    capsys.readouterr()
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "status" in out and "contrib" in out


def test_show_on_a_missing_experiment_exits_nonzero(env, capsys):
    assert main(["show", "999"]) == 1


def test_metrics_from_a_file_feed_the_loop(env, capsys, tmp_path):
    main(["cash", "open"])
    main(["tick"])
    capsys.readouterr()
    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text(json.dumps({"ae-1": {"impressions": 5000, "sessions": 200, "orders": 30}}))
    db = db_of(env)
    exp_id = db.query_one("SELECT id FROM experiments WHERE product_id = 'ae-1'")["id"]
    db.update("experiments", exp_id, {"status": "active"})

    assert main(["tick", "--metrics-source", "file", "--metrics-file", str(metrics_file)]) == 0
    capsys.readouterr()
    assert db.query_one("SELECT impressions FROM experiments WHERE id = ?", (exp_id,))["impressions"] == 5000


def test_order_does_not_clobber_metrics_entered_by_hand(env, capsys):
    main(["cash", "open"])
    main(["tick"])
    capsys.readouterr()
    db = db_of(env)
    exp_id = db.query_one("SELECT id FROM experiments WHERE product_id = 'ae-1'")["id"]
    main(["metrics", str(exp_id), "--orders", "55", "--revenue", "1429.45"])
    capsys.readouterr()

    assert main(["order", str(exp_id), "--customer-paid", "25.99"]) == 0
    assert "metrics unchanged" in capsys.readouterr().out
    row = db.query_one("SELECT orders, revenue FROM experiments WHERE id = ?", (exp_id,))
    assert row["orders"] == 55

    # Recompute refuses without --force, and obeys with it.
    assert main(["recompute", str(exp_id)]) == 1
    assert "would discard metrics" in capsys.readouterr().err
    assert main(["recompute", str(exp_id), "--force"]) == 0
    assert db.query_one("SELECT orders FROM experiments WHERE id = ?", (exp_id,))["orders"] == 1


# =========================================================================
# v1.0 additions
# =========================================================================
def test_cash_open_once_is_idempotent(env, capsys):
    """A startup script must not double the budget on every restart."""
    assert main(["cash", "open", "--once"]) == 0
    capsys.readouterr()
    assert main(["cash", "open", "--once"]) == 0
    assert "already open" in capsys.readouterr().out
    db = db_of(env)
    assert db.query_one("SELECT COUNT(*) AS n FROM cash_flow")["n"] == 1


def test_cash_open_without_once_still_records_a_second_entry(env, capsys):
    main(["cash", "open"])
    main(["cash", "open"])
    capsys.readouterr()
    db = db_of(env)
    assert db.query_one("SELECT COUNT(*) AS n FROM cash_flow")["n"] == 2


def test_health_lists_all_four_stop_conditions(env, capsys):
    main(["cash", "open", "--once"])
    capsys.readouterr()
    assert main(["health"]) == 0
    out = capsys.readouterr().out
    for condition in ("no_profitable_products", "cash_negative_streak",
                      "stalled_kill_approval", "ad_spend_outruns_revenue"):
        assert condition in out


def test_health_exits_two_on_a_stalled_kill_decision(env, capsys, tmp_path):
    from datetime import timedelta
    from src.db import utcnow

    main(["cash", "open", "--once"])
    main(["tick"])
    capsys.readouterr()
    db = db_of(env)
    exp_id = db.query_one("SELECT id FROM experiments WHERE product_id = 'ae-1'")["id"]
    db.insert(
        "human_approvals",
        {
            "experiment_id": exp_id,
            "action": "kill",
            "status": "pending",
            "requested_at": (utcnow() - timedelta(hours=100)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "CTR too low",
        },
    )
    assert main(["health"]) == 2
    out = capsys.readouterr().out
    assert "TRIGGERED" in out
    assert "unanswered for more than 72h" in out


def test_export_writes_a_csv(env, capsys, tmp_path):
    main(["cash", "open", "--once"])
    main(["tick"])
    capsys.readouterr()
    out_path = tmp_path / "experiments.csv"
    assert main(["export", "experiments", "--out", str(out_path)]) == 0
    assert "wrote" in capsys.readouterr().out
    text = out_path.read_text()
    assert "product_id" in text
    assert "ae-1" in text


def test_export_of_an_empty_table_is_not_an_error(env, capsys, tmp_path):
    assert main(["export", "orders", "--out", str(tmp_path / "o.csv")]) == 0
    assert "no rows" in capsys.readouterr().out


def test_weekly_reports_profitable_products_found(env, capsys):
    main(["cash", "open", "--once"])
    main(["tick"])
    capsys.readouterr()
    main(["weekly"])
    assert "profitable_products" in capsys.readouterr().out
