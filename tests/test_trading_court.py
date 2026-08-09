"""The strategy court — evidence, the twelve jurors, and the ruling.

The property that matters most is the first one tested: the court reads a
submitted file and never runs it. Everything else is calibration.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.money import D
from src.trading.court import ACCEPT, MODIFY, REJECT, EvidenceError, StrategyCourt, gather
from src.trading.court.advocates import Defence, Prosecution
from src.trading.court.judge import Judge
from src.trading.court.jury import Finding, Jury


@pytest.fixture
def court(store, trading_config):
    return StrategyCourt(store, trading_config)


def write(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(body)
    return path


GOOD_YAML = """
strategy:
  universe: [SPY, QQQ]
  genome:
    fast_window: 15
    slow_window: 60
    trend_bias: 0
"""


# =========================================================================
# evidence
# =========================================================================
def test_a_submitted_file_is_never_executed(tmp_path, court, feed):
    """The whole safety story of the court in one test.

    If the file were imported, this would create the marker. Nothing in
    ``evidence.py`` imports, execs or evals a submission — it reads the AST.
    """
    marker = tmp_path / "SIDE_EFFECT"
    path = write(
        tmp_path,
        "bomb.py",
        f"open({str(marker)!r}, 'w').write('executed')\n"
        "GENOME = {'fast_window': 10, 'slow_window': 30}\n"
        "UNIVERSE = ['SPY']\n",
    )
    case = court.submit(path, feed=feed)
    assert not marker.exists()
    assert case.ruling.verdict == REJECT  # `open` is a veto anyway


def test_evidence_reads_a_python_genome(tmp_path):
    path = write(
        tmp_path, "s.py", "GENOME = {'fast_window': 9, 'trend_bias': 70}\nUNIVERSE = ['spy']\n"
    )
    evidence = gather(path)
    assert evidence.kind == "python"
    assert evidence.genome == {"fast_window": 9, "trend_bias": 70}
    assert evidence.universe == ("SPY",)
    assert evidence.is_safe
    assert len(evidence.sha256) == 64


def test_evidence_reads_a_yaml_genome(tmp_path):
    evidence = gather(write(tmp_path, "s.yaml", GOOD_YAML))
    assert evidence.kind == "yaml"
    assert evidence.genome["fast_window"] == 15
    assert evidence.universe == ("SPY", "QQQ")


def test_evidence_flags_dangerous_imports_and_calls(tmp_path):
    evidence = gather(
        write(tmp_path, "s.py", "import socket\nGENOME = {}\ndef f():\n    eval('1')\n")
    )
    assert "socket" in evidence.dangerous_imports
    assert "eval" in evidence.dangerous_calls
    assert not evidence.is_safe


def test_evidence_notes_a_computed_genome_rather_than_running_it(tmp_path):
    evidence = gather(write(tmp_path, "s.py", "GENOME = dict(fast_window=1+2)\n"))
    assert evidence.genome == {}
    assert any("computed" in note for note in evidence.notes)


def test_evidence_records_a_syntax_error_instead_of_raising(tmp_path):
    evidence = gather(write(tmp_path, "s.py", "def broken(:\n"))
    assert evidence.syntax_error is not None


def test_evidence_range_checks_the_genes(tmp_path):
    evidence = gather(
        write(tmp_path, "s.py", "GENOME = {'fast_window': 999, 'wibble': 3}\n")
    )
    assert any("fast_window" in entry for entry in evidence.out_of_range)
    assert evidence.unknown_genes == ["wibble"]


def test_unreadable_files_are_refused(tmp_path):
    with pytest.raises(EvidenceError):
        gather(tmp_path / "nope.py")
    with pytest.raises(EvidenceError):
        gather(write(tmp_path, "empty.py", ""))
    with pytest.raises(EvidenceError):
        gather(write(tmp_path, "s.txt", "hello"))


# =========================================================================
# the jury
# =========================================================================
def test_hygiene_jurors_never_argue_for_the_defence(tmp_path, trading_config):
    """Passing a safety check is a prerequisite, not a merit.

    This is the calibration bug the jury was rewritten to fix: a strategy that
    lost money was once admitted on the strength of having tidy imports.
    """
    evidence = gather(write(tmp_path, "s.yaml", GOOD_YAML))
    findings = Jury(trading_config).deliberate(evidence, None, None)
    hygiene = {"parses", "imports", "calls", "genome_present", "gene_ranges",
               "universe", "determinism"}
    for finding in findings:
        if finding.juror in hygiene:
            assert not finding.is_for, f"{finding.juror} argued for the defence"


def test_there_are_twelve_jurors(tmp_path, trading_config):
    evidence = gather(write(tmp_path, "s.yaml", GOOD_YAML))
    findings = Jury(trading_config).deliberate(evidence, None, None)
    assert len(findings) == 12
    assert [f.juror for f in findings] == list(Jury.JURORS)


def test_backtest_jurors_abstain_without_a_backtest(tmp_path, trading_config):
    evidence = gather(write(tmp_path, "s.yaml", GOOD_YAML))
    findings = {f.juror: f for f in Jury(trading_config).deliberate(evidence, None, None)}
    for juror in ("return", "drawdown", "sample_size", "kill_criteria", "costs"):
        assert findings[juror].abstained


def test_the_determinism_juror_catches_disagreement(trading_config, feed):
    from src.trading.backtest import BacktestResult

    a = BacktestResult(firm_key="x", final_equity=D(100), trades=3)
    b = BacktestResult(firm_key="x", final_equity=D(200), trades=3)
    finding = Jury(trading_config)._determinism(a, b)
    assert finding.is_against
    assert "cannot be audited" in finding.reason


# =========================================================================
# advocates and judge
# =========================================================================
def test_each_advocate_cites_only_its_own_side():
    findings = [
        Finding("return", "for", D(30), "made money"),
        Finding("drawdown", "against", D(20), "very volatile"),
        Finding("parses", "abstain", D(0), "fine"),
    ]
    defence = Defence().build(findings)
    prosecution = Prosecution().build(findings)
    assert "made money" in defence.text and "volatile" not in defence.text
    assert "volatile" in prosecution.text and "made money" not in prosecution.text
    # An abstention belongs to neither.
    assert "parses" not in defence.text and "parses" not in prosecution.text


def test_a_veto_rejects_regardless_of_weight():
    findings = [
        Finding("imports", "against", D(100), "imports socket", veto=True),
        Finding("return", "for", D(999), "made a fortune"),
    ]
    ruling = Judge().rule(
        findings, Prosecution().build(findings), Defence().build(findings)
    )
    assert ruling.verdict == REJECT
    assert ruling.confidence == Decimal("100")
    assert "imports" in ruling.vetoes


def test_a_close_argument_is_modify_not_a_coin_flip():
    findings = [
        Finding("return", "for", D(50), "up a bit"),
        Finding("drawdown", "against", D(45), "bumpy"),
    ]
    ruling = Judge().rule(findings, Prosecution().build(findings), Defence().build(findings))
    assert ruling.verdict == MODIFY


def test_nothing_to_rule_on_is_modify():
    ruling = Judge().rule([], Prosecution().build([]), Defence().build([]))
    assert ruling.verdict == MODIFY


# =========================================================================
# the trial, end to end
# =========================================================================
def test_a_dangerous_file_is_rejected(tmp_path, court, feed):
    path = write(
        tmp_path,
        "evil.py",
        "import socket\nGENOME = {'fast_window': 10, 'slow_window': 30}\nUNIVERSE = ['SPY']\n",
    )
    case = court.submit(path, feed=feed)
    assert case.ruling.verdict == REJECT
    assert case.genome_id is None


def test_a_profitable_strategy_is_admitted_as_an_unselected_candidate(
    tmp_path, court, store, feed
):
    """ACCEPT is admission, not deployment."""
    case = court.submit(write(tmp_path, "good.yaml", GOOD_YAML), feed=feed)
    if case.ruling.verdict != ACCEPT:
        pytest.skip("this seed did not produce a profitable strategy")
    assert case.genome_id is not None
    row = store.db.query_one(
        "SELECT * FROM strategy_genomes WHERE id = ?", (case.genome_id,)
    )
    assert not bool(row["selected"])
    assert "court candidate" in row["notes"]


def test_no_firm_genome_changes_because_of_a_ruling(tmp_path, court, store, firm_record, feed):
    before = store.require_firm_by_id(firm_record.id).genome
    court.submit(write(tmp_path, "good.yaml", GOOD_YAML), feed=feed, firm_key="test_firm")
    assert store.require_firm_by_id(firm_record.id).genome == before


def test_the_whole_trial_is_stored_on_one_row(tmp_path, court, store, feed):
    case = court.submit(write(tmp_path, "good.yaml", GOOD_YAML), feed=feed, submitted_by="robbie")
    row = court.case(case.id)
    assert row["file_sha256"] == case.evidence.sha256
    assert row["submitted_by"] == "robbie"
    assert row["ruling"] == case.ruling.verdict
    assert row["findings"] and row["prosecution"] is not None and row["defence"] is not None


def test_the_ruling_is_remembered_as_a_lesson(tmp_path, court, store, feed):
    court.submit(write(tmp_path, "good.yaml", GOOD_YAML), feed=feed)
    lessons = court.memory.lessons(limit=10)
    assert any("court" in lesson.summary.lower() for lesson in lessons)


def test_watch_reviews_a_directory(tmp_path, court, feed):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.yaml").write_text(GOOD_YAML)
    (inbox / "b.py").write_text("import socket\nGENOME = {'fast_window': 5}\nUNIVERSE=['SPY']\n")
    (inbox / "notes.txt").write_text("ignored")
    cases = court.watch(inbox, feed=feed)
    assert len(cases) == 2  # the .txt is skipped


def test_the_transcript_shows_every_juror(tmp_path, court, feed):
    case = court.submit(write(tmp_path, "good.yaml", GOOD_YAML), feed=feed)
    transcript = case.transcript()
    for juror in Jury.JURORS:
        assert juror in transcript
    assert "RULING:" in transcript
