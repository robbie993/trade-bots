"""The exported dashboard — one file, no server.

Two properties are worth holding this to. It has to be *self-contained*: a file
that quietly fetches a stylesheet from a CDN is not a file you can email, and
one that fetches data is not a snapshot at all. And it has to be *honest*: no
control that looks live and is not, and every number stamped with when it was
true.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from src.trading import snapshot  # noqa: E402


@pytest.fixture
def snap(ecosystem):
    ecosystem.tick()
    return snapshot.build(ecosystem)


# =========================================================================
# self-contained
# =========================================================================
def test_the_file_asks_the_network_for_nothing(snap):
    """No CDN, no stylesheet, no image, no font. It has to survive an inbox."""
    assert "<script src=" not in snap
    assert "<link" not in snap
    assert "<img" not in snap
    assert "@import" not in snap
    # The only absolute URL allowed is the SVG namespace, which is an
    # identifier rather than an address — nothing is fetched from it.
    urls = set(re.findall(r"https?://[^\"'\s)]+", snap))
    assert urls <= {"http://www.w3.org/2000/svg"}


def test_it_never_calls_back_to_the_server(snap):
    """A snapshot that polls is a broken dashboard, not a snapshot."""
    assert "fetch(" not in snap
    assert "XMLHttpRequest" not in snap
    assert "/village/flow/events" not in snap


def test_it_is_one_whole_document(snap):
    assert snap.startswith("<!doctype html>")
    assert snap.rstrip().endswith("</html>")


# =========================================================================
# honest
# =========================================================================
def test_nothing_in_the_file_can_be_submitted(snap):
    """Every control that needed a server is removed, not disabled."""
    assert "<form" not in snap
    assert "action=" not in snap
    assert "method=post" not in snap


def test_the_only_button_is_the_replay(snap):
    buttons = re.findall(r"<button[^>]*>(.*?)</button>", snap, re.S)
    assert buttons == ["Replay"]


def test_it_says_when_it_was_taken_and_where_it_came_from(snap, ecosystem):
    assert "Taken 20" in snap
    assert "still photograph" in snap
    assert ecosystem.feed.name in snap


def test_a_note_reaches_the_banner(ecosystem):
    built = snapshot.build(ecosystem, note="Demo data, not a real book.")
    assert "Demo data, not a real book." in built


def test_a_database_password_never_reaches_the_file(ecosystem, monkeypatch):
    monkeypatch.setattr(
        ecosystem.db, "url", "postgresql://mvv:hunter2@db.internal:5432/mvv", raising=False
    )
    built = snapshot.build(ecosystem)
    assert "hunter2" not in built
    assert "db.internal:5432/mvv" in built


# =========================================================================
# it is the same village
# =========================================================================
def test_it_draws_the_same_village_as_the_live_page(snap):
    from src.trading.flow import EDGES, NODES

    assert len(re.findall(r"id='node-", snap)) == len(NODES)
    assert len(re.findall(r"id='edge-", snap)) == len(EDGES)


def test_every_firm_still_gets_a_resident(snap, ecosystem):
    residents = set(re.findall(r"id='resident-([a-z_]+)'", snap))
    assert residents == {f.firm_key for f in ecosystem.store.firms()}


def test_the_recorded_events_travel_inside_the_file(snap, ecosystem):
    """The walkers are the same rows the tick wrote, carried along."""
    recorded = ecosystem.flow.recent(120)
    assert recorded, "the tick should have recorded something"
    assert "const EVENTS = [" in snap
    for event in recorded[:3]:
        assert event.label in snap


def test_the_panels_carry_the_real_numbers(snap, ecosystem):
    from src.money import fmt_money

    for firm in ecosystem.store.firms():
        assert firm.firm_key in snap
        assert fmt_money(firm.allocation) in snap


def test_a_quiet_village_exports_without_complaint(ecosystem):
    """No tick has run: an empty recording is a fact, not an error."""
    built = snapshot.build(ecosystem)
    assert "const EVENTS = [];" in built
    assert "<form" not in built


# =========================================================================
# the command
# =========================================================================
def test_the_command_writes_the_file(tmp_path, firms_yaml, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'd.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("TRADE_VENDOR_DIR", str(tmp_path / "vendor"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    from src.cli import main

    main(["trade", "init"])
    main(["trade", "tick"])
    capsys.readouterr()

    out = tmp_path / "nested" / "village.html"
    assert main(["trade", "dashboard", "--out", str(out)]) == 0
    assert out.exists()
    assert "<!doctype html>" in out.read_text()
    assert "snapshot" in capsys.readouterr().out


def test_the_freeze_leaves_ordinary_text_alone():
    body = "<p>alpha <a href='/village/firms/alpha'>look</a></p><form><button>x</button></form>"
    assert snapshot.freeze(body) == "<p>alpha look</p>"
