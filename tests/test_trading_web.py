"""Mission Control — the Village on one page.

The property under test is the same one the approval gate is held to: the web
tier can *see* everything and can grant nothing. Every action button does what
the equivalent CLI command does and no more, and anything that would move
capital writes an approval request and stops.
"""

from __future__ import annotations

from decimal import Decimal
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
    for panel in ("Ecosystem", "Firms", "Real money", "Brokerage", "The council",
                  "Strategy court", "Competition", "Black market", "Sandbox"):
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
def test_the_village_draws_every_building_and_road(client):
    import re

    from src.trading.flow import EDGES, NODES

    body = client.get("/village/flow").text
    assert len(re.findall(r"id='node-", body)) == len(NODES)
    assert len(re.findall(r"id='edge-", body)) == len(EDGES)
    assert "data-after=" in body


def test_the_village_says_the_villagers_are_real(client):
    body = client.get("/village/flow").text
    assert "actually happened" in body
    assert "nobody wanders for" in body.lower()


def test_every_firm_gets_a_resident(client):
    import re

    body = client.get("/village/flow").text
    residents = re.findall(r"id='resident-([a-z_]+)'", body)
    assert sorted(residents) == ["alpha", "beta"]


def test_a_resident_posture_follows_the_firm_status(client):
    """The little people are the firms, so a paused firm has to look paused."""
    import re

    db = db_for(client)
    db.execute("UPDATE firms SET status = ? WHERE firm_key = ?", ("paused", "alpha"))
    db.execute("UPDATE firms SET status = ? WHERE firm_key = ?", ("killed", "beta"))
    db.close()
    body = client.get("/village/flow").text
    moods = dict(re.findall(r"id='resident-([a-z_]+)' class='villager (\w+)'", body))
    assert moods == {"alpha": "paused", "beta": "gone"}


def test_the_side_buildings_get_their_own_traffic(client):
    """A court ruling or a market sale should show up in the village too."""
    client.post(
        "/village/actions/market-sell",
        data={"seller": "alpha", "asset": "genome", "price": "50"},
        follow_redirects=False,
    )
    edges = {ev["edge"] for ev in client.get("/village/flow/events?after=0").json()["events"]}
    assert "firms>bazaar" in edges


def test_the_village_can_be_left_to_run(client):
    """The answer to 'I do not want to click for things to happen'."""
    body = client.get("/village/flow").text
    assert "id=flow-live" in body
    assert "Let it run" in body


def test_a_quiet_village_says_who_is_holding_the_decisions(client):
    """In human mode the page has to say so, or 'let it run' looks broken."""
    body = client.get("/village/flow").text
    assert "council is not sitting" in body
    assert "gatehouse" in body


def test_the_council_panel_says_it_is_not_sitting_by_default(client):
    body = client.get("/village").text
    assert "The council is not sitting" in body
    assert "TRADE_AUTONOMY=council" in body


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


# =========================================================================
# real money
# =========================================================================
def test_the_real_money_panel_says_nothing_is_live(client):
    body = client.get("/village").text
    assert "Real money — nothing is live" in body
    # And it says why nobody qualifies, rather than just showing empty buttons.
    assert "not evidence about the world" in body


def test_a_firm_that_is_not_ready_gets_no_button_and_a_reason(client):
    body = client.get("/village").text
    assert "unmet · next:" in body
    assert "/village/actions/go-live" not in body


def test_asking_to_go_live_refuses_a_firm_with_no_evidence(client):
    """The button is re-checked here rather than trusted from the page that
    drew it, because the page was rendered in the past."""
    response = client.post("/village/actions/go-live", data={"firm": "alpha"},
                           follow_redirects=False)
    assert "refused" in said(response)
    assert "does not meet the criteria" in said(response)

    db = db_for(client)
    assert db.query_one("SELECT COUNT(*) AS n FROM human_approvals")["n"] == 0
    assert db.query_one("SELECT venue FROM firms WHERE firm_key = 'alpha'")["venue"] \
        == "paper"
    db.close()


def _ready(monkeypatch, firm_key="alpha"):
    """Make one firm pass every criterion, without inventing a year of history."""
    from src.trading import promotion

    monkeypatch.setattr(
        promotion, "assess",
        lambda *a, **k: promotion.Readiness(
            firm_key=firm_key,
            checks=[promotion.Check("Everything", "yes", "yes", True)],
            start_capital=promotion.D("500"),
        ),
    )


def test_the_confirmation_page_refuses_a_firm_that_has_not_earned_it(client):
    body = client.get("/village/live/alpha").text
    assert "cannot go live" in body
    assert "criteria are unmet" in body
    # No button to press, and the evidence is on screen anyway.
    assert "/village/actions/go-live" not in body
    assert "Closed trades" in body


def test_the_confirmation_page_shows_the_evidence_and_one_button(client, monkeypatch):
    """The reason one press is allowed to be enough: the criteria are on screen
    at the moment of the decision, which the approval gate never showed."""
    _ready(monkeypatch)
    body = client.get("/village/live/alpha").text
    assert "Put alpha on real money?" in body
    assert "The evidence" in body
    assert "$500.00" in body
    assert "puts it back on paper by itself" in body
    assert body.count("/village/actions/go-live") == 1


def test_the_switch_puts_a_firm_live_in_one_press(client, monkeypatch):
    _ready(monkeypatch)
    response = client.post("/village/actions/go-live", data={"firm": "alpha"},
                           follow_redirects=False)
    assert "is LIVE on alpaca" in said(response)

    db = db_for(client)
    firm = db.query_one("SELECT venue, cash FROM firms WHERE firm_key = 'alpha'")
    assert firm["venue"] == "alpaca"
    # The cap moved the money, not just the number.
    assert Decimal(str(firm["cash"])) == Decimal("500")

    # And the record reads as an approval, because it is one.
    row = db.query_one("SELECT action, status, approved_by FROM human_approvals")
    assert row["action"] == ApprovalAction.LIVE_TRADING.value
    assert row["status"] == ApprovalStatus.APPROVED.value
    assert "Mission Control" in row["approved_by"]
    db.close()


def test_the_switch_cannot_promote_a_firm_that_has_not_earned_it(client):
    """The evidence gate is what protects this decision, not the number of
    pages between the click and the ledger. Posting straight at the action —
    past the confirmation page entirely — still refuses."""
    response = client.post("/village/actions/go-live", data={"firm": "alpha"},
                           follow_redirects=False)
    assert "refused" in said(response)
    assert "does not meet the criteria" in said(response)

    db = db_for(client)
    assert db.query_one("SELECT COUNT(*) AS n FROM human_approvals")["n"] == 0
    assert db.query_one("SELECT venue FROM firms WHERE firm_key = 'alpha'")["venue"] \
        == "paper"
    db.close()


def test_going_live_and_coming_back_is_a_round_trip(client, monkeypatch):
    """What the switch is for: on, then off, with nothing left behind."""
    _ready(monkeypatch)
    client.post("/village/actions/go-live", data={"firm": "alpha"},
                follow_redirects=False)
    response = client.post("/village/actions/to-paper", data={"firm": "alpha"},
                           follow_redirects=False)
    assert "alpha is back on paper" in said(response)

    db = db_for(client)
    assert db.query_one("SELECT venue FROM firms WHERE firm_key = 'alpha'")["venue"] \
        == "paper"
    db.close()


def test_the_panic_button_needs_no_approval_at_all(client):
    db = db_for(client)
    db.execute("UPDATE firms SET venue = 'alpaca'")
    db.close()

    body = client.get("/village").text
    assert "REAL MONEY" in body
    assert "Pull EVERYTHING back to paper" in body

    response = client.post("/village/actions/all-to-paper", follow_redirects=False)
    assert "pulled 2 firm(s) off real money" in said(response)

    db = db_for(client)
    assert [r["venue"] for r in db.query("SELECT venue FROM firms")] == ["paper", "paper"]
    # Coming back is the half that asks nobody: no approval was written.
    assert db.query_one("SELECT COUNT(*) AS n FROM human_approvals")["n"] == 0
    db.close()


def test_one_firm_can_be_pulled_back_on_its_own(client):
    db = db_for(client)
    db.execute("UPDATE firms SET venue = 'alpaca' WHERE firm_key = 'alpha'")
    db.close()

    response = client.post("/village/actions/to-paper", data={"firm": "alpha"},
                           follow_redirects=False)
    assert "alpha is back on paper" in said(response)

    db = db_for(client)
    assert db.query_one("SELECT venue FROM firms WHERE firm_key = 'alpha'")["venue"] \
        == "paper"
    db.close()
