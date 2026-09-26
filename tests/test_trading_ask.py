"""Firms ask outside minds once, the queue cannot pile up, and an answer
reaches the firm's memory as advice without changing anything else."""

from __future__ import annotations

from datetime import datetime, timezone

from src.trading import ask


def _firm(ecosystem, **genome):
    firm = ecosystem.store.active_firms()[0]
    firm.genome = {**(firm.genome or {}), **genome}
    ecosystem.store.upsert_firm(firm)
    return ecosystem.store.get_firm(firm.firm_key)


def test_a_question_is_asked_once(db):
    assert ask.ask(db, "f", "heir", "why?", {}, "heir:f") is not None
    assert ask.ask(db, "f", "heir", "why?", {}, "heir:f") is None


def test_the_queue_stops_growing_when_nobody_answers(db, monkeypatch):
    monkeypatch.setattr(ask, "MAX_OPEN", 2)
    assert ask.ask(db, "f", "t", "q1", {}, "k1")
    assert ask.ask(db, "f", "t", "q2", {}, "k2")
    assert ask.ask(db, "f", "t", "q3", {}, "k3") is None


def test_an_answer_goes_to_the_firms_memory_and_nowhere_else(ecosystem):
    firm = _firm(ecosystem)
    before = (firm.allocation, dict(firm.genome or {}))
    qid = ask.ask(ecosystem.db, firm.firm_key, "review", "read?", {"x": 1}, "r:1")
    assert ask.answer(ecosystem.db, qid, "claude", "Look at your exits first.", model="m")
    mem = ecosystem.db.query("SELECT * FROM trade_memory WHERE memory_type = 'outside_advice'")
    assert len(mem) == 1 and mem[0]["firm_id"] == firm.id
    assert "Look at your exits first." in mem[0]["summary"]
    after = ecosystem.store.get_firm(firm.firm_key)
    assert (after.allocation, dict(after.genome or {})) == before


def test_each_mind_answers_each_question_once(db):
    qid = ask.ask(db, "f", "t", "q", {}, "k")
    assert ask.answer(db, qid, "claude", "a")
    assert not ask.answer(db, qid, "claude", "again")
    assert [q["id"] for q in ask.open_questions(db, answered_by="chatgpt")] == [qid]
    assert ask.open_questions(db, answered_by="claude") == []


def test_an_empty_answer_is_not_an_answer(db):
    qid = ask.ask(db, "f", "t", "q", {}, "k")
    assert not ask.answer(db, qid, "claude", "   ")


def test_a_funded_heir_asks_about_its_predecessor_and_reviews_its_week(ecosystem):
    firm = _firm(ecosystem, inherited_from="firm_old", inherited_lesson="costs killed it")
    notes = ask.consider(ecosystem, {}, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    topics = {q["topic"] for q in ask.recent(ecosystem.db, 50) if q["firm_key"] == firm.firm_key}
    assert {"heir", "quiet", "review"} <= topics
    assert any("predecessor" in n for n in notes)
    heir_q = next(q for q in ask.open_questions(ecosystem.db, limit=50)
                  if q["topic"] == "heir" and q["firm_key"] == firm.firm_key)
    assert heir_q["context"]["predecessor_lesson"] == "costs killed it"
    assert "costs killed it" not in str(heir_q["context"]["genome"])


def test_asking_twice_in_a_week_asks_nothing_new(ecosystem):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    ask.consider(ecosystem, {}, now=now)
    assert ask.consider(ecosystem, {}, now=now) == []


def test_the_prompt_carries_the_guardrails():
    p = ask.prompt_for({"firm_key": "f", "topic": "t", "question": "q", "context": {}})
    assert "cannot place orders" in p


def test_asking_is_off_unless_switched_on(ecosystem):
    ecosystem.tick()
    assert ecosystem.db.query("SELECT COUNT(*) AS n FROM ai_questions")[0]["n"] == 0


def test_an_estate_with_allocation_but_no_cash_does_not_ask(ecosystem):
    """Live 2026-09-25: two zombie estates filled half the queue."""
    firm = ecosystem.store.active_firms()[0]
    ecosystem.store.update_firm_fields(firm.id, cash=0)
    ask.consider(ecosystem, {}, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert not [q for q in ask.recent(ecosystem.db, 50) if q["firm_key"] == firm.firm_key]
