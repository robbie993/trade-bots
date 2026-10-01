"""The weekly shortlist: finds two outside minds agree are worth testing."""

import json
from datetime import datetime, timedelta, timezone

from src.trading import ask, intel, shortlist

MONDAY = datetime(2026, 10, 5, 0, 15, tzinfo=timezone.utc)


def _review(db, finds, answers, asked=MONDAY - timedelta(days=1)):
    """One research question and each mind's answer to it."""
    qid = db.insert("ai_questions", {
        "firm_key": "village", "topic": "research", "question": "q",
        "context": json.dumps({"finds": finds}), "dedupe_key": f"research:{asked:%Y-%m-%d}",
        "status": "answered", "asked_at": asked.strftime("%Y-%m-%dT%H:%M:%SZ")})
    for mind, verdicts in answers.items():
        db.insert("ai_answers", {"question_id": qid, "answered_by": mind, "model": "",
                                 "answer": "Most are skips.\nVERDICTS: " + json.dumps(verdicts),
                                 "answered_at": asked.strftime("%Y-%m-%dT%H:%M:%SZ")})


def _test(name, what="a 20-day breakout on SPY"):
    return {"name": name, "verdict": "test", "test": what, "proof": "beats SPY after costs"}


FINDS = [{"source": "github", "name": "owner/rl-trader", "url": "https://github.com/owner/rl-trader",
          "about": "an RL agent"},
         {"source": "paper", "name": "Momentum in crypto", "url": "https://arxiv.org/abs/1",
          "abstract": "momentum persists"},
         {"source": "github", "name": "owner/lure", "url": "https://github.com/owner/lure",
          "about": "1000% a month"}]


# -- reading the verdicts ----------------------------------------------------

def test_verdicts_are_read_from_the_last_marker_and_cleaned():
    text = ('First pass VERDICTS: [{"name": "old", "verdict": "test"}]\n'
            "```json\nVERDICTS: [" + json.dumps(_test("owner/rl-trader")) +
            ', {"name": "x", "verdict": "maybe"}, {"verdict": "test"}, "junk"]\n```\nThanks!')
    (v,) = shortlist.verdicts(text)
    assert v["name"] == "owner/rl-trader" and v["verdict"] == "test" and v["test"]


def test_no_marker_or_bad_json_is_no_verdicts():
    assert shortlist.verdicts("Everything here is a skip.") == []
    assert shortlist.verdicts("VERDICTS: [{not json}]") == []
    assert shortlist.verdicts("VERDICTS: []") == []


# -- agreeing ----------------------------------------------------------------

def test_two_minds_agreeing_makes_the_shortlist_one_does_not(db):
    _review(db, FINDS, {
        "claude-code": [_test("owner/rl-trader"), _test("Momentum in crypto")],
        "duck.ai": [_test("OWNER/RL-TRADER ", "the same agent on QQQ")],
    })
    (entry,) = shortlist.shortlist(db, MONDAY)
    assert entry["name"] == "owner/rl-trader"
    assert set(entry["test"]) == {"claude-code", "duck.ai"}
    assert entry["url"] == "https://github.com/owner/rl-trader" and entry["source"] == "github"


def test_one_mind_calling_it_dangerous_keeps_it_off(db):
    _review(db, FINDS, {
        "claude-code": [_test("owner/lure")],
        "duck.ai": [_test("owner/lure"),
                    {"name": "owner/lure", "verdict": "danger", "why": "a malware lure"}],
    })
    assert shortlist.shortlist(db, MONDAY) == []


def test_agreement_across_two_days_counts_each_mind_once(db):
    _review(db, FINDS, {"claude-code": [_test("owner/rl-trader")]},
            asked=MONDAY - timedelta(days=3))
    _review(db, FINDS, {"claude-code": [_test("owner/rl-trader")],
                        "duck.ai": [_test("owner/rl-trader")]}, asked=MONDAY - timedelta(days=1))
    (entry,) = shortlist.shortlist(db, MONDAY)
    assert len(entry["test"]) == 2


def test_a_review_older_than_the_week_is_not_read(db):
    _review(db, FINDS, {"claude-code": [_test("owner/rl-trader")],
                        "duck.ai": [_test("owner/rl-trader")]}, asked=MONDAY - timedelta(days=9))
    assert shortlist.shortlist(db, MONDAY) == []


# -- the research question asks for them --------------------------------------

def test_the_daily_research_question_asks_for_a_verdicts_line(ecosystem):
    intel.upsert(ecosystem.db, "github", "owner/rl-trader", title="an RL agent",
                 url="https://github.com/owner/rl-trader", score=10)
    ask.consider(ecosystem, {}, now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    (q,) = [q for q in ask.open_questions(ecosystem.db, limit=50) if q["topic"] == "research"]
    assert shortlist.MARKER in q["question"]


# -- once a week, where it is seen --------------------------------------------

class _Recorder:
    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))
        return True


def test_monday_keeps_and_sends_the_shortlist_once(ecosystem):
    eco = ecosystem
    eco.notifier = _Recorder()
    _review(eco.db, FINDS, {"claude-code": [_test("owner/rl-trader")],
                            "duck.ai": [_test("owner/rl-trader")]})
    assert shortlist.weekly(eco, MONDAY - timedelta(days=4)) == [], "only on Mondays"
    assert shortlist.weekly(eco, MONDAY)
    assert shortlist.weekly(eco, MONDAY + timedelta(hours=3)) == [], "once a week"

    (subject, body), = eco.notifier.sent
    assert "owner/rl-trader" in body and "Say yes" in body
    kept = [r for r in intel.recent(eco.db, shortlist.INTEL_SOURCE, limit=10)
            if not r["item_key"].endswith(":sent")]
    assert [r["url"] for r in kept] == ["https://github.com/owner/rl-trader"]


def test_an_empty_week_says_so(db):
    assert shortlist.lines([]) == [
        "No find had two outside minds agreeing it is worth testing this week."]
