"""The JSON API, and the solar-system page that consumes it.

Two properties. The API must be **read-only** — it is the one surface that
exists for other programs to call, so it is the one where an action route would
be easiest to add and worst to have. And it must not quietly launder a number:
money stays a string so nobody's JSON parser turns it into a float, and
``sufficient_data`` rides along so a consumer can tell a score from a verdict.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, firms_yaml, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("TRADE_VENDOR_DIR", str(tmp_path / "vendor"))
    monkeypatch.setenv("TRADE_DATA_SEED", "12345")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))

    from src.cli import main

    main(["trade", "init"])
    from src.agents.web import app

    return TestClient(app)


# =========================================================================
# read-only, by construction
# =========================================================================
def test_the_api_router_has_no_write_routes():
    """Not a convention — there is no POST/PUT/PATCH/DELETE handler to call."""
    from src.trading.api import router

    methods = {m for route in router.routes for m in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}


def test_posting_to_an_api_route_is_rejected(client):
    assert client.post("/api/firms").status_code in (404, 405)


def test_the_api_cannot_reach_the_approval_gate(client):
    """Nothing under /api can grant anything; the gate stays at / and the CLI."""
    from src.trading.api import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert not any("approve" in p or "action" in p for p in paths)


# =========================================================================
# the payloads
# =========================================================================
def test_status_reports_the_health_check(client):
    body = client.get("/api/status").json()
    assert body["firms"] == 2
    assert body["reconciled"] is True
    assert body["source"] == "synthetic"
    assert body["autonomy"] == "human"
    assert body["council_sitting"] is False


def test_firms_carries_every_firm_with_its_scorecard(client):
    body = client.get("/api/firms").json()
    assert {f["key"] for f in body["firms"]} == {"alpha", "beta"}
    for firm in body["firms"]:
        assert firm["venue"] == "paper"
        assert firm["universe"]
        assert "sufficient_data" in firm


def test_money_is_a_string_so_nobody_parses_it_as_a_float(client):
    """JSON has no decimal type. Sending a number undoes src/money.py."""
    body = client.get("/api/firms").json()
    raw = client.get("/api/firms").text
    for firm in body["firms"]:
        assert isinstance(firm["allocation"], str)
        assert isinstance(firm["equity"], str)
    # And the wire format really is quoted, not just coerced on the way out.
    assert re.search(r'"allocation":\s*"\d', raw)


def test_a_score_never_travels_without_its_sample_gate(client):
    """A consumer must be able to tell "42 on four trades" from "42 on forty"."""
    body = client.get("/api/firms").json()
    for firm in body["firms"]:
        assert firm["sufficient_data"] is False
        assert firm["closed_trades"] == 0


def test_a_broken_ledger_is_visible_in_the_api(client):
    from decimal import Decimal

    from src.db import Database

    import os

    db = Database.from_url(os.environ["DATABASE_URL"])
    db.execute("UPDATE firms SET cash = ? WHERE firm_key = ?", (Decimal("1.00"), "alpha"))
    db.close()
    body = client.get("/api/status").json()
    assert body["reconciled"] is False
    assert body["ok"] is False
    assert "reconcil" in body["reconciliation"].lower()


def test_the_council_endpoint_says_whether_it_is_sitting(client):
    body = client.get("/api/council").json()
    assert body["sitting"] is False
    assert body["rulings"] == []


def test_tokens_says_they_are_not_capital(client):
    body = client.get("/api/tokens").json()
    assert "capital" in body["note"]


def test_an_empty_village_does_not_500(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    from src.cli import main

    main(["init-db"])
    from src.agents.web import app

    body = TestClient(app).get("/api/firms").json()
    assert body["firms"] == []


# =========================================================================
# the solar system page
# =========================================================================
SOLAR = Path("src/trading/static/solar.html")


def test_the_solar_page_is_served(client):
    response = client.get("/village/solar")
    assert response.status_code == 200
    assert "SOLAR SYSTEM" in response.text


def test_the_solar_page_reads_the_api_rather_than_a_fixture(client):
    """Upstream ships eight invented agents; a planet here is a real firm."""
    body = client.get("/village/solar").text
    assert "fetch('/api/firms')" in body
    assert "let AGENTS = []" in body
    for invented in ("COORDINATOR", "RESEARCHER", "gpt-4o", "tasks24h: 142"):
        assert invented not in body


def test_the_solar_page_asks_the_network_for_nothing():
    """It is served from the app, so a CDN or a font host would be the only
    thing on the page able to see when you are looking at your own ledger."""
    body = SOLAR.read_text()
    assert "fonts.googleapis" not in body
    assert "cdn." not in body
    assert "<script src=" not in body
    # The only absolute URL is the attribution in the header comment.
    urls = set(re.findall(r"https?://[^\"'\s)>]+", body))
    assert urls == {"https://github.com/Audazia/solar-system-agents"}


def test_the_solar_page_is_served_same_origin_so_the_fetch_works(client):
    """A relative fetch only resolves because the app serves the page itself."""
    assert "fetch('/api/firms')" in client.get("/village/solar").text
    assert client.get("/api/firms").status_code == 200


def test_a_win_rate_below_the_sample_gate_is_not_shown_as_a_number():
    body = SOLAR.read_text()
    assert "firm.sufficient_data ? Math.round" in body
    assert "below sample gate" in body


def test_the_upstream_reference_error_is_gone():
    """`i` is let-scoped to the loop above; upstream reads it after.

    It throws inside requestAnimationFrame on every frame, which is why nothing
    orbited until it was removed. The line was dead — the next assignment
    overwrote it.
    """
    body = SOLAR.read_text()
    assert "0.3 - i * 0.015" not in body


def test_the_vendored_page_credits_upstream():
    body = SOLAR.read_text()
    assert "Audazia/solar-system-agents" in body
    assert "MIT" in body
