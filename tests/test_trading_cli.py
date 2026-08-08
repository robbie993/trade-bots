"""The `trade` CLI — every command runs, and none of them spend anything."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.cli import main
from src.trading.adapters import FRAMEWORKS, render_survey, survey


@pytest.fixture
def cli(tmp_path, firms_yaml, monkeypatch):
    """Run the CLI against a temp database and vault, with a fixed data seed."""
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("TRADE_VENDOR_DIR", str(tmp_path / "vendor"))
    monkeypatch.setenv("TRADE_DATA_SEED", "12345")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "notifications.log"))

    def run(*args):
        return main(list(args))

    run("trade", "init")
    return run


def test_init_creates_the_firms(cli, capsys):
    capsys.readouterr()
    assert cli("trade", "firms") == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out


def test_init_is_idempotent(cli):
    assert cli("trade", "init") == 0


@pytest.mark.parametrize(
    "command",
    [
        ("trade", "firms"),
        ("trade", "status"),
        ("trade", "reconcile"),
        ("trade", "leaderboard"),
        ("trade", "allocations"),
        ("trade", "kill-status"),
        ("trade", "memory"),
        ("trade", "audit"),
        ("trade", "frameworks"),
        ("trade", "backtest"),
        ("trade", "evolve"),
        ("trade", "apply-approvals"),
    ],
)
def test_read_only_commands_run(cli, command, capsys):
    assert cli(*command) == 0
    assert capsys.readouterr().out.strip()


def test_tick_then_show(cli, capsys):
    assert cli("trade", "tick") == 0
    capsys.readouterr()
    assert cli("trade", "show", "alpha") == 0
    out = capsys.readouterr().out
    assert "Kill conditions" in out
    assert "Positions" in out


def test_show_rejects_an_unknown_firm(cli, capsys):
    assert cli("trade", "show", "nope") == 1
    assert "unknown firm" in capsys.readouterr().out


def test_simulate_reports_and_ranks(cli, capsys):
    assert cli("trade", "simulate", "--days", "15") == 0
    out = capsys.readouterr().out
    assert "15 tick(s)" in out
    assert "alpha" in out


def test_reconcile_fails_loudly_on_broken_books(cli, capsys, tmp_path):
    from src.db.connection import Database

    cli("trade", "tick")
    db = Database.from_url(f"sqlite:///{tmp_path / 'cli.db'}")
    db.execute("UPDATE firms SET cash = ? WHERE firm_key = ?", (Decimal("42.00"), "alpha"))
    db.close()
    assert cli("trade", "reconcile") == 1
    assert "RECONCILIATION FAILED" in capsys.readouterr().out


def test_a_live_request_spends_nothing(cli, capsys):
    assert cli("trade", "live-request", "--venue", "alpaca") == 0
    out = capsys.readouterr().out
    assert "requested approval" in out
    assert "until this is approved" in out


def test_audit_can_write_into_the_vault(cli, capsys, tmp_path):
    cli("trade", "tick")
    capsys.readouterr()
    assert cli("trade", "audit", "--write") == 0
    assert "written to" in capsys.readouterr().out
    assert list((tmp_path / "vault" / "brokerage").glob("*.md"))


def test_resume_needs_a_named_human(cli):
    with pytest.raises(SystemExit):
        cli("trade", "resume", "alpha")


def test_resume_reactivates_a_paused_firm(cli, capsys, tmp_path):
    from src.db.connection import Database

    db = Database.from_url(f"sqlite:///{tmp_path / 'cli.db'}")
    db.execute("UPDATE firms SET status = ? WHERE firm_key = ?", ("paused", "alpha"))
    db.close()
    assert cli("trade", "resume", "alpha", "--by", "robbie") == 0
    assert "resumed by robbie" in capsys.readouterr().out


def test_the_trade_commands_are_attached_to_the_main_cli():
    from src.cli import build_parser

    trade = build_parser()._subparsers._group_actions[0].choices["trade"]
    commands = set(trade._subparsers._group_actions[0].choices)
    assert {"init", "tick", "leaderboard", "kill-status", "reconcile"} <= commands


# =========================================================================
# adapters
# =========================================================================
def test_the_survey_lists_every_framework(trading_config):
    rows = survey(trading_config)
    assert {r.name for r in rows} == set(FRAMEWORKS)
    # Nothing is vendored in a fresh checkout, and nothing needs to be.
    assert all(not r.installed or r.kind == "pip" for r in rows)


def test_the_survey_says_the_frameworks_are_optional(trading_config):
    text = render_survey(trading_config)
    assert "all optional" in text
    assert "clone_all.sh" in text


def test_lex_conscience_is_documented_as_unreachable(trading_config):
    row = next(r for r in survey(trading_config) if r.name == "lex-conscience")
    assert "native heart" in row.role


def test_a_vendored_repo_is_detected(trading_config, tmp_path):
    from src.trading.adapters import VendorRepo

    repo = VendorRepo("Magents", trading_config)
    assert not repo.present
    assert "not cloned" in repo.doctor()

    repo.path.mkdir(parents=True)
    (repo.path / "requirements.txt").write_text("")
    (repo.path / "main.py").write_text("")
    assert repo.present
    assert "main.py" in repo.doctor()
    assert "pip install -r requirements.txt" in repo.doctor()
