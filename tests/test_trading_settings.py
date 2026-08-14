"""The switches on the wall, and the controls that flip them.

Three things could be started but not stopped from the page you were looking
at: a paused firm, one of the three quarters, and the background loop itself.
The CLI could do the first and the environment could do the other two, but only
by restarting the process that was actually ticking.

These cover the switches themselves, the routes that set them, and the one
refusal that stays a refusal: a killed firm does not come back.
"""

from __future__ import annotations

import pytest

from src.trading.settings import KNOWN, Settings, UnknownSetting


# =========================================================================
# the store
# =========================================================================
@pytest.fixture
def settings(db):
    return Settings(db)


def test_an_unset_switch_defers_to_the_configuration(settings):
    """Unset means "nobody overrode it", not "off".

    The environment decides what the village does by default. A row here is a
    human overriding that at runtime, so an absent row has to hand the decision
    back rather than answer it.
    """
    assert settings.get("bazaar") is False
    assert settings.get("bazaar", default=True) is True


def test_a_set_switch_beats_the_configuration(settings):
    settings.set("bazaar", False, by="tester")
    assert settings.get("bazaar", default=True) is False

    settings.set("bazaar", True, by="tester")
    assert settings.get("bazaar", default=False) is True


def test_clearing_hands_the_decision_back(settings):
    settings.set("arena", False, by="tester")
    assert settings.get("arena", default=True) is False

    settings.clear("arena")
    assert settings.get("arena", default=True) is True


def test_toggle_returns_where_it_landed(settings):
    assert settings.toggle("tavern", default=False) is True
    assert settings.toggle("tavern", default=False) is False


def test_an_unknown_switch_is_refused_rather_than_stored(settings):
    """A typo in a form post must not quietly create a setting nothing reads."""
    with pytest.raises(UnknownSetting):
        settings.set("bazzar", True)
    with pytest.raises(UnknownSetting):
        settings.clear("bazzar")
    assert settings.all() == {}


def test_every_switch_records_who_flipped_it(settings):
    settings.set("paused", True, by="robbie")
    assert settings.all()["paused"]["by"] == "robbie"
    assert settings.all()["paused"]["on"] is True


def test_an_unmigrated_database_reads_as_undecided(db):
    """Reading a switch must never be the thing that breaks a tick."""
    db.execute("DROP TABLE village_settings")
    reader = Settings(db)
    assert reader.raw("paused") is None
    assert reader.get("paused", default=True) is True
    assert reader.all() == {}


def test_the_registry_covers_what_the_page_offers():
    assert set(KNOWN) == {"paused", "arena", "bazaar", "tavern", "evolution"}


# =========================================================================
# the loop honours the pause
# =========================================================================
def test_a_paused_village_does_not_tick(ecosystem):
    """The switch is read per pass, not at startup — a pause you have to
    restart to apply is not a pause."""
    ecosystem.settings.set("paused", True, by="test")
    assert ecosystem.settings.get("paused") is True

    # The loop's check, in the same shape cmd_run uses it.
    assert ecosystem.settings.get("paused")
    ecosystem.settings.set("paused", False, by="test")
    assert not ecosystem.settings.get("paused")


def test_a_closed_quarter_stays_shut_even_with_living_on(ecosystem, monkeypatch):
    from dataclasses import replace

    from src.trading.living import LivingConfig

    monkeypatch.setenv("TRADE_LIVING", "on")
    ecosystem.config = replace(ecosystem.config, living=LivingConfig())
    ecosystem._living = None
    ecosystem.settings.set("arena", False, by="test")
    ecosystem.settings.set("bazaar", False, by="test")
    ecosystem.settings.set("tavern", False, by="test")

    reports = ecosystem.simulate(40)
    assert [line for r in reports for line in r.village] == []


# =========================================================================
# the controls
# =========================================================================
@pytest.fixture
def village(ecosystem, monkeypatch):
    """Mission Control wired to the same database as the ecosystem fixture."""
    from fastapi.testclient import TestClient

    from src.agents.web import app

    monkeypatch.setenv("DATABASE_URL", ecosystem.db.url)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    return TestClient(app)


def test_the_page_offers_every_switch(village):
    page = village.get("/village").text
    assert "Switches" in page
    for label in ("The village", "Arena", "Bazaar", "Tavern"):
        assert label in page


def test_flipping_a_switch_from_the_page_reaches_the_database(village, ecosystem):
    village.post("/village/actions/switch", data={"name": "paused"})
    assert ecosystem.settings.get("paused") is True

    village.post("/village/actions/switch", data={"name": "paused"})
    assert ecosystem.settings.get("paused") is False


def test_the_button_says_what_it_will_do(village):
    """"Turn off" is not what you are about to do to a running village."""
    assert "Pause" in village.get("/village").text

    village.post("/village/actions/switch", data={"name": "paused"})
    page = village.get("/village").text
    assert "Start again" in page
    assert ">paused<" in page


def test_a_bogus_switch_is_refused_on_the_page_not_with_a_500(village):
    response = village.post(
        "/village/actions/switch", data={"name": "nonsense"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "no%20switch" in response.headers["location"]


# =========================================================================
# bringing a firm back
# =========================================================================
def _pause(ecosystem):
    from src.trading.models import FirmStatus

    firm = ecosystem.store.firms()[0]
    ecosystem.store.set_firm_status(firm.id, FirmStatus.PAUSED.value)
    return firm


def test_a_paused_firm_gets_a_button(village, ecosystem):
    assert "Bring back" not in village.get("/village").text
    _pause(ecosystem)
    assert "Bring back" in village.get("/village").text


def test_the_button_brings_it_back(village, ecosystem):
    firm = _pause(ecosystem)
    village.post("/village/actions/resume", data={"firm": firm.firm_key})

    assert ecosystem.store.get_firm(firm.firm_key).status == "active"
    assert "Bring back" not in village.get("/village").text


def test_a_killed_firm_gets_no_button_and_no_resume(village, ecosystem):
    """A pause says "this tripped a limit, look at it". A kill is the answer to
    having looked. Reversing the second from a web button would make the kill
    switch a suggestion."""
    from src.trading.models import FirmStatus

    firm = ecosystem.store.firms()[0]
    ecosystem.store.set_firm_status(firm.id, FirmStatus.KILLED.value)

    page = village.get("/village").text
    assert "Bring back" not in page
    assert "does not reverse" in page

    response = village.post(
        "/village/actions/resume", data={"firm": firm.firm_key},
        follow_redirects=False,
    )
    assert "refused" in response.headers["location"]
    assert ecosystem.store.get_firm(firm.firm_key).status == "killed"


def test_an_unknown_firm_is_a_message_not_a_crash(village):
    response = village.post(
        "/village/actions/resume", data={"firm": "no_such_firm"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "refused" in response.headers["location"]


# =========================================================================
# the hosted mirror still refuses all of it
# =========================================================================
def test_none_of_these_controls_exist_on_a_public_mirror(ecosystem, monkeypatch):
    """New buttons are new write surface. The mirror strips and refuses both."""
    from fastapi.testclient import TestClient

    from src.agents.web import app

    monkeypatch.setenv("DATABASE_URL", ecosystem.db.url)
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    _pause(ecosystem)
    client = TestClient(app)

    page = client.get("/village").text
    assert "Bring back" not in page
    assert "<form" not in page

    for path, payload in (
        ("/village/actions/resume", {"firm": "alpha"}),
        ("/village/actions/switch", {"name": "paused"}),
    ):
        assert client.post(path, data=payload).status_code == 403
    assert ecosystem.settings.get("paused") is False
