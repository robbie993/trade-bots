"""The operator's ideas, handed to the part of the village that can use them.

What is pinned: an idea is only taken from an explicit IDEA line; an idea for a
firm lands in its memory as outside advice and makes it ask for a concrete,
testable change (the existing advice loop, which decides nothing by itself);
an idea for the village goes to the research review; ideas in something sent
are passed on the same way; and the operator can address a part of the village.
"""

from __future__ import annotations

import json

import pytest

from src.trading import chat, desk, ideas, inbox


@pytest.fixture
def firms(db):
    for key, status in (("alpha", "active"), ("beta", "active"), ("gone", "killed"),
                        (desk.DESK, "active")):
        db.insert("firms", {"firm_key": key, "name": key, "status": status,
                            "strategy": "momentum", "allocation": 1000, "cash": 1000,
                            "genome": json.dumps({"stop_loss_pct": 5})})
    return db


def test_ideas_come_only_from_idea_lines():
    text, found = ideas.parse(
        "Love it.\n"
        'IDEA: {"to": "firms", "idea": "sit out the first 30 minutes"}\n'
        'IDEA: {"to": "village"}\n'
        "IDEA: not json\n")
    assert text == "Love it."
    assert found == [{"idea": "sit out the first 30 minutes", "to": "firms"}]


def test_an_idea_for_every_firm_becomes_advice_and_a_testable_question(firms):
    db = firms
    got = ideas.give(db, "sit out the first 30 minutes of the session", "firms")
    assert got["firms"] == ["alpha", "beta"]          # not the dead firm, not the desk
    advice = db.query("SELECT summary FROM trade_memory WHERE memory_type = 'outside_advice'")
    assert len(advice) == 2 and all("first 30 minutes" in a["summary"] for a in advice)
    asks = db.query("SELECT firm_key, topic, status FROM ai_questions ORDER BY id")
    assert [(a["firm_key"], a["topic"], a["status"]) for a in asks] == [
        ("alpha", "operator", "answered"), ("alpha", "proposal", "open"),
        ("beta", "operator", "answered"), ("beta", "proposal", "open")]
    assert db.query("SELECT * FROM ai_proposals") == []   # nothing changes by itself


def test_an_idea_for_one_firm_goes_only_to_that_firm(firms):
    got = ideas.give(firms, "use a tighter stop", "beta")
    assert got["firms"] == ["beta"]


def test_an_idea_for_the_village_goes_to_the_research_review(firms):
    db = firms
    got = ideas.give(db, "add options flow as a data source", "village")
    assert got["village"] and got["firms"] == []
    kept = ideas.recent(db)
    assert kept[0]["title"] == "add options flow as a data source"
    assert kept[0]["detail"]["went_to"] == "village"
    assert "research review" in ideas.describe(got)


def test_the_chat_passes_an_idea_on_and_says_where_it_went(firms):
    db = firms
    q = chat.post(db, "tell the firms to sit out the open", "mind")
    chat.claim(db)
    chat.answer(db, q, 'Good call.\nIDEA: {"to": "firms", "idea": "sit out the open"}')
    reply = chat.history(db)[-1]["text"]
    assert reply.startswith("Good call.") and "IDEA:" not in reply
    assert "alpha, beta" in reply
    assert len(db.query("SELECT id FROM ai_questions WHERE topic = 'proposal'")) == 2


def test_the_operator_can_talk_to_a_part(firms):
    db = firms
    q = chat.post(db, "why did you refuse beta?", "council")
    question = db.query_one("SELECT * FROM village_chat WHERE id = ?", (q,))
    assert chat.talking_to(question) == "council"
    p = chat.prompt(db, question)
    assert "The operator is talking to the Council." in p and "IDEA:" in p


def test_ideas_in_something_sent_are_passed_on(firms):
    db = firms
    row = inbox.submit_link(db, "https://www.instagram.com/reel/abc/")
    inbox.finish(db, row, text="cut your agent costs by caching", title="AI costs",
                 summary='A post about caching prompts.\n'
                         'IDEA: {"to": "village", "idea": "cache the outside minds\' prompts"}')
    kept = db.query_one("SELECT summary FROM inbox WHERE id = ?", (row,))["summary"]
    assert "IDEA:" not in kept and "Ideas passed to the village" in kept
    assert ideas.recent(db)[0]["detail"]["origin"].endswith(f"(#{row})")


def _proposal_q(db, firm_key):
    return db.query_one("SELECT id FROM ai_questions WHERE topic = 'proposal' "
                        "AND firm_key = ?", (firm_key,))["id"]


def test_status_follows_each_firm_idea_to_its_end(firms):
    from src.trading import ask

    db = firms
    ideas.give(db, "use a tighter stop", "firms")
    first = {f["firm"]: f["stage"] for f in ideas.status(db)[0]["firms"]}
    assert first == {"alpha": "waiting", "beta": "waiting"}
    # alpha's adviser finds nothing to change; beta's proposes a tighter stop.
    ask.answer(db, _proposal_q(db, "alpha"), "claude",
               '{"changes": {}, "why": "this is a data idea, not a setting"}')
    ask.answer(db, _proposal_q(db, "beta"), "claude",
               '{"changes": {"stop_loss_pct": 3}, "why": "tighter"}')
    fates = {f["firm"]: f for f in ideas.status(db)[0]["firms"]}
    assert fates["alpha"]["stage"] == "no change"
    assert "data idea" in fates["alpha"]["detail"]
    assert fates["beta"]["stage"] == "testing"
    db.execute("UPDATE ai_proposals SET status = 'refused', verdict = 'lost the held-out bars'")
    beta = {f["firm"]: f for f in ideas.status(db)[0]["firms"]}["beta"]
    assert beta["stage"] == "refused" and "lost" in beta["detail"]


def test_a_village_idea_waits_for_the_research_review(firms):
    ideas.give(firms, "add options flow as a data source", "village")
    assert ideas.status(firms)[0]["village"]["stage"] == "not reviewed yet"


def test_the_village_can_see_what_became_of_its_ideas(firms):
    db = firms
    ideas.give(db, "add options flow as a data source", "village")
    q = chat.post(db, "what happened to my ideas?")
    p = chat.prompt(db, db.query_one("SELECT * FROM village_chat WHERE id = ?", (q,)))
    assert "WHAT BECAME OF EACH" in p and "research review: not reviewed yet" in p


def test_advisers_are_only_offered_genes_the_firm_can_feel():
    from src.trading import proposals

    class F:
        firm_key, genome = "x", {"analysts": ["reversion"]}

    offered = set(proposals.genes_for(F()))
    assert offered == {"rsi_entry", "ibs_entry", "pullback_atr", "stop_loss_pct"}
    trend = set(proposals.genes_for(F(), seats=("technical", "sentiment", "macro")))
    assert {"fast_window", "slow_window", "stop_loss_pct"} <= trend
    # Nothing reads these anywhere, and the backtest never runs the shadow desk.
    assert not trend & {"top_fraction", "max_per_name", "lookback", "max_positions",
                        "rsi_entry", "shadow_dte_min"}
