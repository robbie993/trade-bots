"""Mission Control — the Village on one page.

The property under test is the same one the approval gate is held to: the web
tier can *see* everything and can grant nothing. Every action button does what
the equivalent CLI command does and no more, and anything that would move
capital writes an approval request and stops.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from src.agents.human_gate import ApprovalAction, ApprovalStatus  # noqa: E402
from src.db import Database  # noqa: E402


@pytest.fixture
def client(tmp_path, firms_yaml, monkeypatch):
    db_path = tmp_path / "web.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("TRADE_VENDOR_DIR", str(tmp_path / "vendor"))
    monkeypatch.setenv("TRADE_DATA_SEED", "12345")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "notifications.log"))

    from src.cli import main

    main(["trade", "init"])

    from src.agents.web import app

    return TestClient(app)


def said(response) -> str:
    """The flash message carried back on the redirect."""
    return parse_qs(urlparse(response.headers.get("location", "")).query).get("said", [""])[0]


def db_for(client) -> Database:
    import os

    return Database.from_url(os.environ["DATABASE_URL"])


# =========================================================================
# the page
# =========================================================================
def test_mission_control_renders_every_panel(client):
    body = client.get("/village").text
    for panel in ("Ecosystem", "Firms", "Brokerage", "Strategy court",
                  "Competition", "Black market", "Sandbox"):
        assert panel in body


def test_the_gate_links_to_mission_control(client):
    assert "/village" in client.get("/").text


def test_mission_control_links_back_to_the_gate(client):
    body = client.get("/village").text
    assert "approval gate" in body
    assert "nothing on this page grants an approval" in body


def test_the_page_survives_an_empty_ecosystem(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    from src.cli import main

    main(["init-db"])
    from src.agents.web import app

    body = TestClient(app).get("/village").text
    assert "No firms yet" in body
    assert "trade init" in body


def test_a_firm_page_shows_the_kill_table(client):
    body = client.get("/village/firms/alpha").text
    assert "Kill conditions" in body
    assert "Drawdown" in body
    assert "Positions" in body
    assert "Capital" in body


def test_an_unknown_firm_does_not_500(client):
    response = client.get("/village/firms/nope")
    assert response.status_code == 200
    assert "No firm" in response.text


def test_a_broken_ledger_is_shown_in_red(client):
    from decimal import Decimal

    db = db_for(client)
    db.execute("UPDATE firms SET cash = ? WHERE firm_key = ?", (Decimal("42.00"), "alpha"))
    db.close()
    body = client.get("/village").text
    assert "The books do not reconcile" in body
    assert "refuse to score, kill or allocate" in body


# =========================================================================
# actions
# =========================================================================
def test_the_tick_button_runs_a_tick(client):
    response = client.post("/village/actions/tick", follow_redirects=False)
    assert response.status_code == 303
    assert "proposal(s)" in said(response)


def test_the_season_button_runs_a_season(client):
    response = client.post("/village/actions/season", follow_redirects=False)
    assert "season:" in said(response)


def test_a_strategy_file_can_be_dropped_in(client):
    response = client.post(
        "/village/actions/court-submit",
        files={"file": ("evil.py", b"import socket\nGENOME={'fast_window':10}\nUNIVERSE=['SPY']\n")},
        follow_redirects=False,
    )
    message = said(response)
    assert "REJECT" in message
    assert "socket" in message


def test_an_uploaded_file_is_never_executed(client, tmp_path):
    """Same guarantee as the CLI path: the court reads, it does not run."""
    marker = tmp_path / "WEB_SIDE_EFFECT"
    client.post(
        "/village/actions/court-submit",
        files={
            "file": (
                "bomb.py",
                f"open({str(marker)!r},'w').write('x')\nGENOME={{'fast_window':10}}\n"
                "UNIVERSE=['SPY']\n".encode(),
            )
        },
        follow_redirects=False,
    )
    assert not marker.exists()


def test_an_unreadable_upload_is_a_message_not_a_500(client):
    response = client.post(
        "/village/actions/court-submit",
        files={"file": ("notes.txt", b"not a strategy")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "could not try that file" in said(response)


def test_selling_a_genome_lists_it(client):
    response = client.post(
        "/village/actions/market-sell",
        data={"seller": "alpha", "asset": "genome", "price": "50"},
        follow_redirects=False,
    )
    message = said(response)
    assert "listed #" in message
    # The '#' survives the redirect — it is a URL fragment if not encoded.
    assert "tokens" in message


def test_a_refused_action_says_so_rather_than_erroring(client):
    response = client.post(
        "/village/actions/market-sell",
        data={"seller": "nobody", "asset": "genome", "price": "50"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "refused" in said(response)


def test_buying_capital_from_the_page_still_needs_an_approval(client):
    client.post(
        "/village/actions/market-sell",
        data={"seller": "alpha", "asset": "capital", "price": "5000"},
        follow_redirects=False,
    )
    response = client.post(
        "/village/actions/market-buy",
        data={"buyer": "beta", "listing": "1"},
        follow_redirects=False,
    )
    message = said(response)
    assert "capital does not move until approval" in message

    db = db_for(client)
    rows = db.query("SELECT allocation FROM firms ORDER BY firm_key")
    approvals = db.query("SELECT action, status FROM human_approvals")
    db.close()
    # Nothing moved, and the request is pending. Compared as Decimals: SQLite's
    # NUMERIC affinity stores "50000.00" back as 50000, which is why every read
    # in this codebase goes through D() rather than trusting the raw column.
    from decimal import Decimal

    assert {Decimal(str(r["allocation"])) for r in rows} == {
        Decimal("50000"),
        Decimal("25000"),
    }
    assert any(
        a["action"] == ApprovalAction.ALLOCATE_CAPITAL.value
        and a["status"] == ApprovalStatus.PENDING.value
        for a in approvals
    )


def test_the_page_cannot_settle_an_unapproved_transfer(client):
    client.post(
        "/village/actions/market-sell",
        data={"seller": "alpha", "asset": "capital", "price": "5000"},
        follow_redirects=False,
    )
    client.post(
        "/village/actions/market-buy",
        data={"buyer": "beta", "listing": "1"},
        follow_redirects=False,
    )
    response = client.post(
        "/village/actions/market-settle", data={"transaction": "1"}, follow_redirects=False
    )
    assert "until a human approves it" in said(response)


def test_sandbox_actions_run_and_report(client):
    from src.trading.competition import TokenLedger

    db = db_for(client)
    for key in ("alpha", "beta"):
        TokenLedger(db).award(key, 500, "seed")
    db.close()

    response = client.post(
        "/village/actions/sandbox",
        data={"action": "form", "actor": "alpha", "name": "The Pact", "target": "beta"},
        follow_redirects=False,
    )
    assert "The Pact" in said(response)

    response = client.post(
        "/village/actions/sandbox",
        data={"action": "betray", "actor": "alpha", "name": "The Pact"},
        follow_redirects=False,
    )
    assert "betrayal" in said(response)


def test_sandbox_actions_leave_the_ledger_alone(client):
    from src.trading.competition import TokenLedger

    db = db_for(client)
    for key in ("alpha", "beta"):
        TokenLedger(db).award(key, 500, "seed")
    before = db.query("SELECT firm_key, allocation, cash, status FROM firms ORDER BY firm_key")
    db.close()

    client.post(
        "/village/actions/sandbox",
        data={"action": "sabotage", "actor": "alpha", "target": "beta"},
        follow_redirects=False,
    )

    db = db_for(client)
    after = db.query("SELECT firm_key, allocation, cash, status FROM firms ORDER BY firm_key")
    db.close()
    assert before == after


def test_apply_approvals_from_the_page_carries_out_only_decided_things(client):
    response = client.post("/village/actions/apply-approvals", follow_redirects=False)
    assert "nothing approved is waiting" in said(response)


# =========================================================================
# the flow diagram
# =========================================================================
def test_the_flow_page_draws_every_node_and_edge(client):
    import re

    from src.trading.flow import EDGES, NODES

    body = client.get("/village/flow").text
    assert len(re.findall(r"id='node-", body)) == len(NODES)
    assert len(re.findall(r"id='edge-", body)) == len(EDGES)
    assert "data-after=" in body


def test_the_flow_page_says_the_dots_are_real(client):
    body = client.get("/village/flow").text
    assert "actually happened" in body
    assert "if the village is idle" in body.lower()


def test_the_event_feed_is_empty_before_anything_runs(client):
    assert client.get("/village/flow/events?after=0").json()["events"] == []


def test_a_tick_fills_the_event_feed(client):
    client.post("/village/actions/tick", follow_redirects=False)
    events = client.get("/village/flow/events?after=0").json()["events"]
    assert events
    assert events[0]["node"] == "market"
    assert any(ev["edge"] == "market>firms" for ev in events)
    assert any(ev["edge"] == "brokerage>audit" for ev in events)


def test_the_feed_only_returns_what_is_new(client):
    client.post("/village/actions/tick", follow_redirects=False)
    first = client.get("/village/flow/events?after=0").json()["events"]
    assert client.get(f"/village/flow/events?after={first[-1]['id']}").json()["events"] == []


def test_mission_control_links_to_the_flow(client):
    assert "/village/flow" in client.get("/village").text
