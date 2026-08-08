"""`experiment add` — entering real products by hand.

The Scout's platform connectors need API credentials the operator may not
have; the business question does not. Twenty products typed in by hand answer
"can this find something profitable?" exactly as well as twenty pulled from an
API, and cost nothing to start.

The command must take the *same* path `tick` takes for a scouted candidate:
platform gate, duplicate check, economic filter, in that order. These tests
pin that, and pin the two things that make manual entry safe — it writes a
`discovered` row and never moves money.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.experiment_ledger import ExperimentLedger
from src.agents.scout import build_scout
from src.cli import main
from src.config import Config
from src.db import Database
from src.models import Status


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "manual.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "notify.log"))
    # No automated discovery — this is the manual-entry workflow.
    monkeypatch.setenv("MVV_SCOUT_SOURCE", "manual")
    monkeypatch.delenv("MVV_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("MVV_SMTP_HOST", raising=False)
    assert main(["init-db"]) == 0
    return {"db_path": db_path}


def ledger_of(env) -> ExperimentLedger:
    return ExperimentLedger(Database.from_url(f"sqlite:///{env['db_path']}"))


def add(*args) -> int:
    return main(["experiment", "add", *args])


GOOD = ("--name", "Wireless Earbuds Pro", "--cost", "12.50", "--price", "39.99",
        "--platform", "temu")


# -- the happy path --------------------------------------------------------
def test_a_product_becomes_a_discovered_experiment(env):
    assert add(*GOOD) == 0

    experiments = ledger_of(env).all()
    assert len(experiments) == 1
    e = experiments[0]
    assert e.product_name == "Wireless Earbuds Pro"
    assert e.status == Status.DISCOVERED.value
    assert e.unit_cost == Decimal("12.50")
    assert e.selling_price == Decimal("39.99")
    assert e.source_platform == "temu"


def test_money_survives_as_decimal(env):
    """A price entered as text must not become a float on the way in."""
    assert add("--name", "Odd Price", "--cost", "10.10", "--price", "30.30",
               "--platform", "temu") == 0
    e = ledger_of(env).all()[0]
    assert isinstance(e.unit_cost, Decimal)
    assert e.unit_cost == Decimal("10.10")
    assert e.selling_price == Decimal("30.30")


def test_shipping_is_folded_into_landed_cost(env):
    """Shipping is not free just because the supplier lists it separately —
    the kill logic's COGS must include it."""
    assert add("--name", "LED Strip", "--cost", "4.20", "--shipping", "1.30",
               "--price", "16.99", "--platform", "aliexpress") == 0
    assert ledger_of(env).all()[0].unit_cost == Decimal("5.50")


def test_price_is_suggested_when_omitted(env, capsys):
    assert add("--name", "No Price", "--cost", "4.20", "--platform", "temu") == 0
    assert "suggested" in capsys.readouterr().out
    assert ledger_of(env).all()[0].selling_price > Decimal("4.20")


def test_the_manual_entry_is_recorded_in_the_audit_trail(env):
    assert add(*GOOD) == 0
    led = ledger_of(env)
    events = led.events(led.all()[0].id)
    created = [e for e in events if e["event_type"] == "created"]
    assert created and "manual entry" in created[0]["detail"]


def test_adding_a_product_spends_nothing(env):
    """No cash row, and the experiment is only `discovered` — the sample order
    and the ad budget remain separate human approvals."""
    assert add(*GOOD) == 0
    db = Database.from_url(f"sqlite:///{env['db_path']}")
    assert db.query("SELECT * FROM cash_flow") == []
    assert ledger_of(env).all()[0].status == Status.DISCOVERED.value


def test_supplier_defaults_to_the_platform(env):
    assert add(*GOOD) == 0
    assert ledger_of(env).all()[0].supplier == "temu"


def test_the_url_is_kept_for_audit(env):
    assert add(*GOOD, "--url", "https://temu.com/item/1") == 0
    assert ledger_of(env).all()[0].product_url == "https://temu.com/item/1"


# -- the three refusals, in the order tick applies them --------------------
def test_an_unapproved_platform_is_refused_and_requests_approval(env, capsys):
    assert add("--name", "Widget", "--cost", "5", "--price", "20",
               "--platform", "alibaba") == 1

    assert ledger_of(env).all() == []
    out = capsys.readouterr().out
    assert "not approved" in out
    db = Database.from_url(f"sqlite:///{env['db_path']}")
    pending = db.query("SELECT * FROM human_approvals WHERE action = ?", ("add_platform",))
    assert len(pending) == 1


def test_a_duplicate_product_is_refused(env, capsys):
    assert add(*GOOD) == 0
    assert add(*GOOD) == 1

    assert len(ledger_of(env).all()) == 1
    assert "already exists" in capsys.readouterr().out


def test_an_explicit_product_id_allows_a_genuine_second_listing(env):
    assert add(*GOOD) == 0
    assert add(*GOOD, "--product-id", "temu-9931") == 0
    assert len(ledger_of(env).all()) == 2


def test_bad_economics_are_refused(env, capsys):
    assert add("--name", "Bad Deal", "--cost", "9.90", "--price", "10.00",
               "--platform", "temu") == 1

    assert ledger_of(env).all() == []
    out = capsys.readouterr().out
    assert "not worth testing" in out
    assert "--force" in out


def test_force_records_it_anyway_and_says_so(env, capsys):
    assert add("--name", "Bad Deal", "--cost", "9.90", "--price", "10.00",
               "--platform", "temu", "--force") == 0

    experiments = ledger_of(env).all()
    assert len(experiments) == 1
    assert "forced past the economic filter" in capsys.readouterr().out
    led = ledger_of(env)
    created = [e for e in led.events(experiments[0].id) if e["event_type"] == "created"]
    assert "FORCED" in created[0]["detail"]


def test_force_does_not_bypass_the_platform_gate(env):
    """--force overrides the economic filter only. Spending rules are not
    the operator's to wave through from a convenience flag."""
    assert add("--name", "Widget", "--cost", "5", "--price", "20",
               "--platform", "alibaba", "--force") == 1
    assert ledger_of(env).all() == []


# -- the scout must stay quiet in a manual workflow ------------------------
@pytest.mark.parametrize("source", ["manual", "none"])
def test_a_manual_scout_source_discovers_nothing(source, monkeypatch, tmp_path):
    """Otherwise `tick` keeps inventing fixture products on every pass and
    spends the 20-experiment stop condition on sample data."""
    monkeypatch.setenv("MVV_SCOUT_SOURCE", source)
    scout = build_scout(Config())
    assert scout.discover(limit=10) == []


def test_tick_adds_nothing_of_its_own_under_manual_entry(env):
    assert add(*GOOD) == 0
    assert main(["tick"]) == 0
    # Still exactly the one product the operator entered.
    assert len(ledger_of(env).all()) == 1


def test_an_unknown_scout_source_is_still_an_error(monkeypatch):
    monkeypatch.setenv("MVV_SCOUT_SOURCE", "nonsense")
    with pytest.raises(ValueError, match="unknown scout source"):
        build_scout(Config())
