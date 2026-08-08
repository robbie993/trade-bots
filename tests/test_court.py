"""File Court tests.

The load-bearing case is `test_a_court_with_no_reviewers_returns_UNREVIEWED`.
The deployment prompt this was built from returned a hardcoded risk score of 50
whenever CodeTribunal produced no output, which turned "nothing reviewed this
file" into a MIXED verdict indistinguishable from a real one. Several tests
here exist purely to keep that from coming back.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import CourtConfig
from src.court.backends import (
    CodeTribunalBackend,
    CommandBackend,
    InteractiveSkillBackend,
    ReviewOutcome,
    build_backends,
    risk,
)
from src.court.file_court import (
    GUILTY,
    MIXED,
    NOT_GUILTY,
    UNREVIEWED,
    FileCourt,
    aggregate,
    sha256_of,
)
from src.court.watcher import Watcher


class FakeBackend:
    """A reviewer that answers however the test wants."""

    def __init__(self, name, score=None, error="", boom=False):
        self.name = name
        self.score = score
        self.error = error
        self.boom = boom
        self.calls = 0

    def is_available(self):
        return (self.score is not None, self.error)

    def review(self, file_path):
        self.calls += 1
        if self.boom:
            raise RuntimeError("reviewer exploded")
        if self.score is None:
            return ReviewOutcome.unavailable(self.name, self.error or "unavailable")
        return ReviewOutcome(
            backend=self.name,
            available=True,
            score=Decimal(str(self.score)),
            verdict="",
            summary=f"{self.name} says {self.score}",
            raw={"risk_score": self.score, "evidence": {"n": 1}},
        )


@pytest.fixture
def court_config(tmp_path):
    return CourtConfig(
        codetribunal_path=tmp_path / "CodeTribunal",
        uploads_dir=tmp_path / "uploads",
        processed_dir=tmp_path / "processed",
        log_path=tmp_path / "logs" / "village.log",
    )


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / "suspect.py"
    path.write_text("def f():\n    return 1\n")
    return path


def tiers(t1=None, t2=None, t3=None):
    return {
        "tier1": list(t1 or []),
        "tier2": list(t2 or []),
        "tier3": list(t3 or []),
    }


# -- the bug this module exists to prevent --------------------------------
def test_a_court_with_no_reviewers_returns_UNREVIEWED(db, court_config, sample_file):
    """No reviewer ran, so there is no verdict — not MIXED, not NOT_GUILTY."""
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("gone", error="not installed")]))
    case = court.process_file(sample_file)

    assert case.verdict == UNREVIEWED
    assert case.risk_score is None
    assert case.unreviewed
    assert "gone" in case.summary


def test_an_unavailable_reviewer_never_contributes_a_score(db, court_config, sample_file):
    """One real reviewer plus one missing one averages over the real one only."""
    good = FakeBackend("real", score=10)
    missing = FakeBackend("missing", error="not on PATH")
    court = FileCourt(db, court_config, backends=tiers([good, missing]))
    case = court.process_file(sample_file)

    assert case.risk_score == Decimal("10.00")  # not (10+50)/2, and not (10+0)/2
    assert case.verdict == NOT_GUILTY
    assert "missing" in case.pending_reviewers


def test_a_reviewer_that_raises_is_not_a_verdict(db, court_config, sample_file):
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("crashy", score=80, boom=True)]))
    case = court.process_file(sample_file)

    assert case.verdict == UNREVIEWED
    assert "RuntimeError" in case.outcomes[0].error


# -- verdict bands ---------------------------------------------------------
@pytest.mark.parametrize(
    "score,expected",
    [
        (0, NOT_GUILTY),
        (40, NOT_GUILTY),   # boundary: >40 is MIXED, 40 exactly is not
        (41, MIXED),
        (70, MIXED),        # boundary: >70 is GUILTY, 70 exactly is not
        (71, GUILTY),
        (100, GUILTY),
    ],
)
def test_verdict_bands(score, expected, court_config):
    outcome = ReviewOutcome("x", True, score=Decimal(str(score)))
    verdict, avg, _ = aggregate([outcome], court_config)
    assert verdict == expected
    assert avg == Decimal(str(score)).quantize(Decimal("0.01"))


def test_aggregate_with_nothing_at_all(court_config):
    verdict, avg, summary = aggregate([], court_config)
    assert verdict == UNREVIEWED
    assert avg is None
    assert "none configured" in summary


def test_risk_scores_are_decimal_not_float(db, court_config, sample_file):
    """Averaging 10 and 20 must not produce a binary-float artefact."""
    court = FileCourt(
        db, court_config, backends=tiers([FakeBackend("a", score=10), FakeBackend("b", score=25)])
    )
    case = court.process_file(sample_file)
    assert isinstance(case.risk_score, Decimal)
    assert case.risk_score == Decimal("17.50")


def test_risk_is_clamped_and_coerced():
    assert risk(150) == Decimal("100.00")
    assert risk(-5) == Decimal("0.00")
    assert risk("42.5") == Decimal("42.50")
    assert risk(None) is None
    assert risk("banana") is None


# -- escalation ------------------------------------------------------------
def test_low_risk_files_never_reach_the_expensive_reviewers(db, court_config, sample_file):
    t2, t3 = FakeBackend("tribunal", score=90), FakeBackend("panel", score=90)
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("t1", score=5)], [t2], [t3]))
    case = court.process_file(sample_file)

    assert t2.calls == 0 and t3.calls == 0
    assert case.verdict == NOT_GUILTY


def test_a_mid_risk_file_escalates_to_tribunal_only(db, court_config, sample_file):
    t2, t3 = FakeBackend("tribunal", score=35), FakeBackend("panel", score=90)
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("t1", score=35)], [t2], [t3]))
    court.process_file(sample_file)

    assert t2.calls == 1  # 35 > tribunal_above (30)
    assert t3.calls == 0  # running mean 35 is not > panel_above (50)


def test_a_high_risk_file_escalates_all_the_way(db, court_config, sample_file):
    t2, t3 = FakeBackend("tribunal", score=80), FakeBackend("panel", score=90)
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("t1", score=90)], [t2], [t3]))
    case = court.process_file(sample_file)

    assert t2.calls == 1 and t3.calls == 1
    assert case.verdict == GUILTY


# -- persistence -----------------------------------------------------------
def test_a_case_is_written_to_the_docket(db, court_config, sample_file):
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("t1", score=90)]))
    case = court.process_file(sample_file)

    rows = court.cases()
    assert len(rows) == 1
    row = rows[0]
    assert row["file_name"] == "suspect.py"
    assert row["verdict"] == GUILTY
    assert row["file_hash"] == sha256_of(sample_file)
    assert case.id == row["id"]
    # the JSON columns round-trip
    assert json.loads(row["backends"])[0]["backend"] == "t1"
    assert json.loads(row["evidence"])["t1"] == {"n": 1}


def test_unreviewed_cases_are_listed_as_pending(db, court_config, sample_file):
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("gone", error="nope")]))
    court.process_file(sample_file)
    assert len(court.pending()) == 1


def test_missing_file_is_an_error_not_a_verdict(db, court_config, tmp_path):
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("t1", score=5)]))
    with pytest.raises(FileNotFoundError):
        court.process_file(tmp_path / "nope.py")


# -- CodeTribunal adapter --------------------------------------------------
def test_codetribunal_reports_unavailable_when_not_cloned(court_config, sample_file):
    backend = CodeTribunalBackend(court_config.codetribunal_path)
    ok, why = backend.is_available()
    assert not ok and "not cloned" in why

    outcome = backend.review(sample_file)
    assert not outcome.available
    assert outcome.score is None  # emphatically not 50


def test_codetribunal_parses_a_real_verdict(tmp_path, sample_file):
    ct = tmp_path / "CodeTribunal"
    ct.mkdir()
    (ct / "main.py").write_text(
        "import json\n"
        "print(json.dumps({'risk_score': 82, 'verdict': 'guilty', 'summary': 'bad'}))\n"
    )
    outcome = CodeTribunalBackend(ct).review(sample_file)

    assert outcome.available
    assert outcome.score == Decimal("82.00")
    assert outcome.verdict == "GUILTY"


def test_codetribunal_silence_is_not_a_score(tmp_path, sample_file):
    ct = tmp_path / "CodeTribunal"
    ct.mkdir()
    (ct / "main.py").write_text("pass\n")  # exits 0, prints nothing
    outcome = CodeTribunalBackend(ct).review(sample_file)

    assert not outcome.available
    assert outcome.score is None
    assert "no output" in outcome.error


def test_codetribunal_garbage_output_is_not_a_score(tmp_path, sample_file):
    ct = tmp_path / "CodeTribunal"
    ct.mkdir()
    (ct / "main.py").write_text("print('not json at all')\n")
    outcome = CodeTribunalBackend(ct).review(sample_file)

    assert not outcome.available
    assert "unparseable" in outcome.error


# -- the real Space layout: src/code_tribunal/app.py -----------------------
def make_space(tmp_path, body):
    """A clone shaped like the actual Hugging Face Space."""
    pkg = tmp_path / "CodeTribunal" / "src" / "code_tribunal"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "app.py").write_text(body)
    return tmp_path / "CodeTribunal"


def test_the_space_layout_is_detected_and_run_as_a_module(tmp_path, sample_file):
    ct = make_space(
        tmp_path,
        "import json\nprint(json.dumps({'risk_score': 64, 'verdict': 'mixed'}))\n",
    )
    backend = CodeTribunalBackend(ct)

    argv, env = backend.invocation()
    assert argv == ["python", "-m", "code_tribunal.app"]
    assert env["PYTHONPATH"].endswith("src")

    outcome = backend.review(sample_file)
    assert outcome.available
    assert outcome.score == Decimal("64.00")


def test_root_script_layout_still_works(tmp_path, sample_file):
    ct = tmp_path / "CodeTribunal"
    ct.mkdir()
    (ct / "main.py").write_text("import json\nprint(json.dumps({'risk_score': 5}))\n")

    argv, env = CodeTribunalBackend(ct).invocation()
    assert argv[1].endswith("main.py")
    assert env == {}


def test_the_space_layout_wins_when_both_exist(tmp_path):
    ct = make_space(tmp_path, "print('{}')\n")
    (ct / "main.py").write_text("print('{}')\n")
    argv, _ = CodeTribunalBackend(ct).invocation()
    assert argv == ["python", "-m", "code_tribunal.app"]


def test_an_explicit_entrypoint_overrides_detection(tmp_path):
    ct = make_space(tmp_path, "print('{}')\n")
    (ct / "cli.py").write_text("print('{}')\n")
    argv, _ = CodeTribunalBackend(ct, entrypoint="cli.py").invocation()
    assert argv[1].endswith("cli.py")


def test_a_clone_with_no_entrypoint_is_unavailable(tmp_path, sample_file):
    ct = tmp_path / "CodeTribunal"
    (ct / "docs").mkdir(parents=True)
    ok, why = CodeTribunalBackend(ct).is_available()
    assert not ok
    assert "no entrypoint" in why
    assert CodeTribunalBackend(ct).review(sample_file).score is None


def test_extra_args_are_configurable(tmp_path, sample_file):
    """The Space's CLI may not accept --output json; it must be removable."""
    ct = make_space(
        tmp_path,
        "import sys, json\n"
        "if '--output' in sys.argv:\n"
        "    sys.exit('this CLI rejects --output')\n"
        "print(json.dumps({'risk_score': 20}))\n",
    )
    assert not CodeTribunalBackend(ct).review(sample_file).available
    assert CodeTribunalBackend(ct, extra_args=[]).review(sample_file).score == Decimal("20.00")


def test_a_verdict_is_found_among_gradio_log_noise(tmp_path, sample_file):
    """It is a Gradio Space, so banners and warnings share stdout."""
    ct = make_space(
        tmp_path,
        "import json\n"
        "print('Loading weights... {corrupt not json')\n"
        "print('WARNING: cache miss')\n"
        "print(json.dumps({'risk_score': 77, 'verdict': 'guilty'}))\n"
        "print('Done in 4.2s')\n",
    )
    outcome = CodeTribunalBackend(ct).review(sample_file)

    assert outcome.available
    assert outcome.score == Decimal("77.00")
    assert outcome.verdict == "GUILTY"


def test_extract_json_picks_the_last_object(tmp_path):
    from src.court.backends import extract_json

    assert extract_json('{"a": 1}\n{"risk_score": 9}')["risk_score"] == 9
    assert extract_json('noise {"nested": {"deep": 2}} tail')["nested"] == {"deep": 2}
    assert extract_json('a string with { an unbalanced brace') is None
    assert extract_json("[1, 2, 3]") is None      # arrays are not verdicts
    assert extract_json("") is None
    # braces inside strings must not confuse the scanner
    assert extract_json('{"msg": "a } brace", "risk_score": 3}')["risk_score"] == 3


def test_codetribunal_nonzero_exit_is_not_a_score(tmp_path, sample_file):
    ct = tmp_path / "CodeTribunal"
    ct.mkdir()
    (ct / "main.py").write_text("import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
    outcome = CodeTribunalBackend(ct).review(sample_file)

    assert not outcome.available
    assert "exit 3" in outcome.error


# -- other backends --------------------------------------------------------
def test_a_command_backend_missing_from_PATH_is_unavailable(sample_file):
    backend = CommandBackend("yama", ["definitely-not-installed-xyz", "{file}"])
    outcome = backend.review(sample_file)
    assert not outcome.available
    assert "not on PATH" in outcome.error


def test_interactive_skills_never_fabricate_a_score(sample_file):
    backend = InteractiveSkillBackend("tribunal", "/tribunal")
    outcome = backend.review(sample_file)
    assert not outcome.available
    assert outcome.score is None
    assert "/tribunal" in outcome.error


def test_build_backends_wires_three_tiers(court_config):
    backends = build_backends(court_config)
    assert [b.name for b in backends["tier1"]] == ["codetribunal"]
    assert [b.name for b in backends["tier2"]] == ["tribunal"]
    assert [b.name for b in backends["tier3"]] == ["agent_review_panel"]


def test_yama_joins_tier1_when_enabled(tmp_path):
    config = CourtConfig(codetribunal_path=tmp_path / "ct", yama_enabled=True)
    assert [b.name for b in build_backends(config)["tier1"]] == ["codetribunal", "yama"]


# -- the watcher -----------------------------------------------------------
@pytest.fixture
def watcher(db, court_config):
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("t1", score=12)]))
    return Watcher(court, court_config, log=lambda _m: None)


def test_watcher_processes_and_archives(watcher, court_config):
    watcher.ensure_dirs()
    dropped = Path(court_config.uploads_dir) / "thing.py"
    dropped.write_text("x = 1\n")

    cases = watcher.process_once()

    assert len(cases) == 1
    assert not dropped.exists()
    assert (Path(court_config.processed_dir) / "thing.py").exists()


def test_watcher_ignores_unwatched_extensions(watcher, court_config):
    watcher.ensure_dirs()
    (Path(court_config.uploads_dir) / "notes.txt").write_text("hi")
    assert watcher.process_once() == []


def test_watcher_does_not_clobber_earlier_evidence(watcher, court_config):
    """The prompt's rename() overwrote same-named files. This must not."""
    watcher.ensure_dirs()
    processed = Path(court_config.processed_dir)
    (processed / "dup.py").write_text("FIRST\n")

    (Path(court_config.uploads_dir) / "dup.py").write_text("SECOND\n")
    watcher.process_once()

    assert (processed / "dup.py").read_text() == "FIRST\n"
    assert (processed / "dup.1.py").read_text() == "SECOND\n"


def test_a_failed_review_leaves_the_file_for_a_retry(db, court_config):
    court = FileCourt(db, court_config, backends=tiers([FakeBackend("t1", score=12)]))
    watcher = Watcher(court, court_config, log=lambda _m: None)
    watcher.ensure_dirs()
    stuck = Path(court_config.uploads_dir) / "stuck.py"
    stuck.write_text("x\n")

    def explode(_path):
        raise OSError("disk gone")

    court.process_file = explode
    assert watcher.process_once() == []
    assert stuck.exists()  # still there for the next sweep


def test_watcher_creates_its_directories(watcher, court_config):
    assert not Path(court_config.uploads_dir).exists()
    watcher.ensure_dirs()
    assert Path(court_config.uploads_dir).is_dir()
    assert Path(court_config.processed_dir).is_dir()
