"""The village as planets, for anything that draws one.

`solar-system-agents` ships with twelve invented agents and polls `/health` and
`/api/status` for real ones. These tests are about the difference between those
two things: a dashboard fed by this endpoint must be showing the actual village,
and the numbers it draws with must *mean* something rather than look nice.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from src.trading import planets  # noqa: E402


def test_every_firm_and_institution_becomes_an_agent(ecosystem):
    found = planets.agents(ecosystem)
    ids = {a["id"] for a in found}
    for key, *_ in planets.INSTITUTIONS:
        assert key in ids
    for firm in ecosystem.store.firms():
        assert firm.firm_key in ids
    assert len(found) == len(planets.INSTITUTIONS) + len(ecosystem.store.firms())


def test_the_schema_is_the_one_the_dashboard_reads(ecosystem):
    """Field names come from the dashboard's own config.js. Renaming one here
    silently stops it drawing, which looks like a broken dashboard rather than
    a broken payload."""
    required = {
        "id", "name", "model", "role", "tier", "premium", "skills",
        "channels", "status", "heartbeat", "planet", "color", "size",
        "orbit", "speed",
    }
    for agent in planets.agents(ecosystem):
        assert required <= set(agent), sorted(required - set(agent))


def test_size_comes_from_allocation(ecosystem):
    """A bigger desk is a bigger planet. Otherwise it is decoration."""
    found = {a["id"]: a for a in planets.agents(ecosystem)}
    firms = {f.firm_key: f for f in ecosystem.store.firms()}
    biggest = max(firms.values(), key=lambda f: f.allocation)
    smallest = min(firms.values(), key=lambda f: f.allocation)
    assert found[biggest.firm_key]["size"] > found[smallest.firm_key]["size"]


def test_an_idle_firm_visibly_stops(ecosystem):
    """Speed is trades, not time. A picture where everything spins at the same
    rate whatever is happening is an animation, not a display."""
    # By firm key, not by tier: the court is tier "operations" too, and it is
    # an institution with a fixed orbit rather than something that trades.
    keys = {f.firm_key for f in ecosystem.store.firms()}
    idle = [a for a in planets.agents(ecosystem) if a["id"] in keys]
    assert idle
    assert all(a["speed"] == pytest.approx(0.2) for a in idle), \
        "nothing has traded yet, so nothing should be moving"


def test_speed_rises_with_trades():
    """Asserted on the function rather than on a tick, because whether the
    fixture's two firms happen to trade on their first bar is a fact about the
    seed, not about this."""

    class Card:
        def __init__(self, trades):
            self.trades = trades

    assert planets._speed(Card(0)) == pytest.approx(0.2)
    assert planets._speed(Card(4)) > planets._speed(Card(1))
    assert planets._speed(Card(10_000)) <= 2.0, "an orbit that fast is a blur"


def test_a_paused_firm_changes_colour(ecosystem):
    from src.trading.models import FirmStatus

    firm = ecosystem.store.firms()[0]
    ecosystem.store.set_firm_status(firm.id, FirmStatus.PAUSED.value)
    found = {a["id"]: a for a in planets.agents(ecosystem)}
    assert found[firm.firm_key]["color"] == planets.STATUS_COLOR["paused"]


def test_a_firm_that_cannot_be_valued_is_marked_as_such(ecosystem):
    """The distinction the whole system now turns on, carried to the picture:
    a firm nobody can price is not a firm doing badly."""
    ecosystem.store.db.insert("positions", {
        "firm_id": ecosystem.store.firms()[0].id, "symbol": "SPY",
        "quantity": "10", "avg_price": "400",
    })
    real = ecosystem.feed

    class RefusesSPY:
        name = "picky"

        def series(self, symbol):
            if symbol == "SPY":
                raise RuntimeError("no bars")
            return real.series(symbol)

    ecosystem._feed = RefusesSPY()
    found = planets.agents(ecosystem)
    # Colours must not collide: the council used to share this exact red.
    assert len({c for c in (planets.STATUS_COLOR.values())}) == len(planets.STATUS_COLOR)
    keys = {f.firm_key for f in ecosystem.store.firms()}
    blind = [a for a in found
             if a["id"] in keys and a["color"] == planets.STATUS_COLOR["blind"]]
    assert blind, "an unpriceable book should be visible on the dashboard"
    assert blind[0]["rings"] is True
    assert blind[0]["measured"] is False


def test_nothing_here_claims_to_be_a_language_model(ecosystem):
    """The court's jury is arithmetic and the analysts are pure functions.
    A dashboard column saying otherwise would be the one dishonest pixel."""
    assert all(a["premium"] is False for a in planets.agents(ecosystem))
    assert all(a["model"] != "claude" for a in planets.agents(ecosystem))


def test_the_status_endpoint_carries_the_agents(ecosystem, tmp_path, monkeypatch, firms_yaml):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", ecosystem.db.url)
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("MVV_PUBLIC", "0")
    import src.agents.web as web

    payload = TestClient(web.app).get("/api/status").json()
    assert "agents" in payload
    assert len(payload["agents"]) == len(planets.INSTITUTIONS) + payload["firms"]


def test_the_dashboard_probes_both_paths_it_was_configured_for(ecosystem, monkeypatch, firms_yaml):
    """config.js names healthEndpoint '/health' and statusEndpoint
    '/api/status'. Both have to answer or it draws nothing."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", ecosystem.db.url)
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("MVV_PUBLIC", "0")
    import src.agents.web as web

    client = TestClient(web.app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/status").status_code == 200
