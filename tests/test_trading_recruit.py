"""Recruitment — bringing your own bots into the village.

The temptation with a "drop your files in" feature is to trust the files,
because they are yours. This does not: a recruit goes through the same court as
anything else, is created holding nothing, and needs the same approval any
other capital needs. What you get for dropping a file in is a *candidate firm*,
not a funded one.
"""

from __future__ import annotations

import pytest

from src.agents.human_gate import ApprovalAction, ApprovalStatus
from src.money import D, ZERO
from src.trading.recruit import firm_key_for, pending_recruits, recruit, stake_for

# A genome the court actually accepts on the fixture's data. The verdict is a
# real backtest, so a made-up genome usually earns MODIFY — which is the court
# working, and would make every test below assert on a refusal.
GOOD = """
GENOME = {
    "fast_window": 8, "slow_window": 60, "rsi_window": 14, "trend_bias": 88,
    "value_window": 60, "fair_band_pct": 6, "calm_vol_pct": 30,
}
UNIVERSE = ["SPY", "QQQ"]
"""

HOSTILE = """
import socket
GENOME = {"fast_window": 8, "slow_window": 34}
UNIVERSE = ["SPY"]
"""

NO_UNIVERSE = """
GENOME = {
    "fast_window": 8, "slow_window": 60, "rsi_window": 14, "trend_bias": 88,
    "value_window": 60, "fair_band_pct": 6, "calm_vol_pct": 30,
}
"""


@pytest.fixture
def bot(tmp_path):
    def write(text: str, name: str = "my_bot.py"):
        path = tmp_path / name
        path.write_text(text)
        return path
    return write


# =========================================================================
# the trial is not optional
# =========================================================================
def test_a_hostile_file_is_not_recruited(ecosystem, bot):
    result = recruit(ecosystem, bot(HOSTILE, "sneaky.py"))
    assert result.accepted is False
    assert "REJECT" in result.reason
    assert ecosystem.store.get_firm("sneaky") is None


def test_a_recruited_file_is_never_executed(ecosystem, bot, tmp_path):
    marker = tmp_path / "RECRUIT_SIDE_EFFECT"
    recruit(ecosystem, bot(
        f"open({str(marker)!r},'w').write('x')\n" + GOOD, "bomb.py"
    ))
    assert not marker.exists()


def test_a_file_with_nothing_to_trade_is_not_a_firm(ecosystem, bot):
    result = recruit(ecosystem, bot(NO_UNIVERSE, "no_symbols.py"))
    assert result.accepted is False
    assert "nothing to trade" in result.reason or "court returned" in result.reason


def test_an_unreadable_file_does_not_crash_the_drop_box(ecosystem, tmp_path):
    box = tmp_path / "box"          # its own directory: tmp_path holds fixtures
    box.mkdir()
    (box / "notes.txt").write_text("not a strategy")
    (box / "ok.py").write_text(GOOD)
    results = ecosystem.recruit_all(box)
    # .txt is not a strategy extension, so it is skipped rather than failed.
    assert [r.firm_key for r in results] == ["ok"]


# =========================================================================
# born paused and broke
# =========================================================================
def test_a_recruit_holds_nothing_until_it_is_approved(ecosystem, bot):
    result = recruit(ecosystem, bot(GOOD, "alpha_bot.py"))
    assert result.accepted is True

    firm = ecosystem.store.get_firm("alpha_bot")
    assert firm is not None
    assert firm.status == "paused"
    assert D(firm.allocation) == ZERO
    assert D(firm.cash) == ZERO


def test_recruiting_writes_one_approval_for_the_capital(ecosystem, bot):
    result = recruit(ecosystem, bot(GOOD, "alpha_bot.py"))
    approval = ecosystem.gate.get(result.approval_id)
    assert approval.action == ApprovalAction.ALLOCATE_CAPITAL.value
    assert approval.status == ApprovalStatus.PENDING.value
    assert approval.details["kind"] == "recruit"
    assert approval.details["firm"] == "alpha_bot"


def test_approving_it_funds_it_and_sets_it_trading(ecosystem, bot):
    result = recruit(ecosystem, bot(GOOD, "alpha_bot.py"))
    ecosystem.gate.approve(result.approval_id, approved_by="you")
    applied = ecosystem.apply_approvals()

    assert any("funded alpha_bot" in str(a) for a in applied)
    firm = ecosystem.store.get_firm("alpha_bot")
    assert firm.status == "active"
    assert D(firm.allocation) == result.requested
    assert D(firm.cash) == result.requested


def test_an_unapproved_recruit_never_trades(ecosystem, bot):
    recruit(ecosystem, bot(GOOD, "alpha_bot.py"))
    ecosystem.tick()
    firm = ecosystem.store.get_firm("alpha_bot")
    assert firm.status == "paused"
    assert D(firm.cash) == ZERO
    assert ecosystem.store.positions(firm.id) == []


def test_a_funded_recruit_reconciles_like_any_other_firm(ecosystem, bot):
    result = recruit(ecosystem, bot(GOOD, "alpha_bot.py"))
    ecosystem.gate.approve(result.approval_id, approved_by="you")
    ecosystem.apply_approvals()
    ecosystem.tick()
    assert ecosystem.brokerage.reconcile(ecosystem.market()).ok


# =========================================================================
# capital it does not have
# =========================================================================
def test_a_recruit_cannot_be_asked_for_more_than_is_left(ecosystem, bot):
    """The brokerage cap is not raised by adding firms to it."""
    asked = stake_for(ecosystem, D("999999999"))
    deployed = sum((D(f.allocation) for f in ecosystem.store.firms()), ZERO)
    assert asked <= D(ecosystem.config.brokerage.total_capital) - deployed


def test_recruiting_with_no_headroom_is_refused_not_squeezed_in(ecosystem, bot):
    import dataclasses

    from src.trading.config import BrokerageConfig

    deployed = sum((D(f.allocation) for f in ecosystem.store.firms()), ZERO)
    # TradingConfig is frozen, so the config is replaced rather than mutated.
    ecosystem.config = dataclasses.replace(
        ecosystem.config, brokerage=BrokerageConfig(total_capital=deployed)
    )
    result = recruit(ecosystem, bot(GOOD, "late.py"))
    assert result.accepted is False
    assert "no capital left" in result.reason
    assert ecosystem.store.get_firm("late") is None


# =========================================================================
# naming
# =========================================================================
def test_a_file_name_becomes_a_safe_firm_key():
    assert firm_key_for("My Bot v2.py") == "my_bot_v2"
    assert firm_key_for("../../etc/passwd") == "passwd"
    assert firm_key_for("bot.py", taken={"bot"}) == "bot_2"


def test_two_bots_with_the_same_name_do_not_collide(ecosystem, bot, tmp_path):
    first = recruit(ecosystem, bot(GOOD, "twin.py"))
    second_dir = tmp_path / "other"
    second_dir.mkdir()
    (second_dir / "twin.py").write_text(GOOD)
    second = recruit(ecosystem, second_dir / "twin.py")
    assert first.firm_key == "twin"
    assert second.firm_key == "twin_2"


# =========================================================================
# the drop box
# =========================================================================
def test_the_drop_box_takes_python_and_yaml(ecosystem, tmp_path):
    box = tmp_path / "box"
    box.mkdir()
    (box / "one.py").write_text(GOOD)
    (box / "two.yaml").write_text(
        "name: Two\n"
        "strategy: mean_reversion\n"
        "genome:\n"
        "  fast_window: 12\n  slow_window: 60\n  rsi_window: 14\n"
        "  trend_bias: 88\n  value_window: 60\n  fair_band_pct: 6\n"
        "  calm_vol_pct: 30\n"
        "universe: [SPY]\n"
    )
    results = ecosystem.recruit_all(box)
    assert {r.firm_key for r in results} == {"one", "two"}
    assert all(r.accepted for r in results), [r.reason for r in results]


def test_a_named_strategy_key_does_not_break_the_parser(tmp_path):
    """`strategy:` may be a nested block or a plain name.

    Descending into a string raised an AttributeError out of the parser rather
    than a readable refusal — and a plain name is exactly how
    config/firm_config.yaml writes it.
    """
    from src.trading.court.evidence import gather

    path = tmp_path / "named.yaml"
    path.write_text(
        "strategy: momentum\n"
        "genome:\n  fast_window: 8\n  slow_window: 34\n"
        "universe: [SPY]\n"
    )
    evidence = gather(path)
    assert evidence.genome["fast_window"] == 8
    assert evidence.universe == ("SPY",)


def test_pending_recruits_lists_what_is_waiting(ecosystem, bot):
    recruit(ecosystem, bot(GOOD, "waiting.py"))
    rows = pending_recruits(ecosystem)
    assert [r["firm"] for r in rows] == ["waiting"]
    assert rows[0]["file"] == "waiting.py"


def test_the_shipped_example_bots_are_readable_and_safe():
    """The templates in bots/ have to be valid, or they teach the wrong thing.

    Asserted on the file rather than on the verdict: whether a backtest likes a
    genome depends on the data and the seed, and pinning a template to one
    seed's ACCEPT would be a test of the synthetic feed, not of the example.
    What must hold is that each one parses, reaches outside nothing, declares a
    complete in-range genome, and names symbols to trade.
    """
    from pathlib import Path

    from src.trading.brain.evolver import BASE_GENOME
    from src.trading.court.evidence import gather

    from src.trading import adapter

    files = [p for p in Path("bots").iterdir()
             if p.suffix.lower() in (".py", ".yaml", ".yml", ".json")]
    assert files, "bots/ should ship example strategies"

    # Two kinds of example live here now, and they are judged differently. A
    # genome file declares seven numbers and the court reads them without ever
    # running the file. An adapter file declares a function, and the village
    # runs it — see src/trading/adapter.py. Both must parse and both must
    # reach outside nothing; only the first can be asked for a genome.
    genome_files, adapter_files = [], []
    for path in files:
        evidence = gather(path)
        assert evidence.syntax_error is None, path.name
        assert evidence.is_safe, (path.name, evidence.dangerous_imports)
        assert not evidence.out_of_range, (path.name, evidence.out_of_range)
        assert not evidence.unknown_genes, (path.name, evidence.unknown_genes)

        if path.suffix.lower() == ".py" and _has_entry_point(path):
            adapter_files.append(path)
            continue
        genome_files.append(path)
        assert set(BASE_GENOME) <= set(evidence.genome), path.name
        assert evidence.universe, path.name

    assert genome_files, "at least one genome example"
    assert adapter_files, "at least one adapter example"
    for path in adapter_files:
        assert callable(adapter.load(path)), path.name


def _has_entry_point(path) -> bool:
    """Does this file define a function the village would call?

    Read with `ast` rather than imported: deciding *whether* a file is an
    adapter must not require running it.
    """
    import ast

    from src.trading.adapter import ENTRY_POINTS

    tree = ast.parse(path.read_text())
    names = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return bool(names & set(ENTRY_POINTS))


# =========================================================================
# the council, when it is sitting
# =========================================================================
def test_the_council_judges_a_recruit_on_its_trial_not_its_record(autonomous_eco, bot):
    """A new firm has no track record; the sample gate is the wrong question."""
    from src.trading.council.council import GRANT

    result = recruit(autonomous_eco, bot(GOOD, "newcomer.py"))
    assert result.accepted
    rulings = autonomous_eco.hold_council()
    assert rulings and rulings[0].verdict == GRANT
    jurors = {f.juror for f in rulings[0].findings}
    assert "court_accepted" in jurors
    assert "sample_gate" not in jurors


def test_the_council_funds_a_recruit_it_grants(autonomous_eco, bot):
    recruit(autonomous_eco, bot(GOOD, "newcomer.py"))
    autonomous_eco.hold_council()
    autonomous_eco.apply_approvals()
    firm = autonomous_eco.store.get_firm("newcomer")
    assert firm.status == "active"
    assert D(firm.allocation) > ZERO


def test_the_council_will_not_hand_a_newcomer_everything(autonomous_eco, bot, monkeypatch):
    """An untried firm does not get the run of the brokerage."""
    from src.trading.council import Council, gather

    result = recruit(autonomous_eco, bot(GOOD, "greedy.py"))
    approval = autonomous_eco.gate.get(result.approval_id)
    evidence = gather(autonomous_eco, approval)
    evidence.new_allocation = evidence.headroom  # ask for all of it
    ruling = Council().rule(evidence)
    assert ruling.verdict != "grant"


@pytest.fixture
def autonomous_eco(db, tmp_path, firms_yaml, notifier):
    from src.config import Config
    from src.trading.config import AutonomyConfig, DataConfig, TradingConfig
    from src.trading.ecosystem import Ecosystem

    eco = Ecosystem(
        db,
        TradingConfig(
            firms_config=firms_yaml,
            audit_vault=tmp_path / "vault",
            vendor_dir=tmp_path / "vendor",
            data=DataConfig(source="synthetic", seed=12345, history_days=180),
            autonomy=AutonomyConfig(mode="council"),
        ),
        Config(database_url=db.url, notification_log=tmp_path / "n.log"),
        notifier,
    )
    eco.init_firms()
    return eco
