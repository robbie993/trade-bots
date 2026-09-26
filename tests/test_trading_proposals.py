"""Advice becomes a proposal, the mind tests it, the council rules, and only a
held-out winner is ever adopted."""

from __future__ import annotations

import json
from decimal import Decimal

from src.trading import ask, proposals
from src.trading.council.council import Council
from src.trading.council.evidence import CouncilEvidence


def test_parse_keeps_only_real_genes_clamped_and_few():
    text = ('Here you go: {"changes": {"stop_loss_pct": 99, "fast_window": "4.6", '
            '"not_a_gene": 5, "trend_bias": "abc"}, "why": "cut losers"}')
    changes, why = proposals.parse(text)
    assert changes == {"stop_loss_pct": "25.00", "fast_window": 5}
    assert why == "cut losers"
    assert proposals.parse("no json here") == ({}, "")


def _firm(eco):
    return eco.store.active_firms()[0]


def test_an_answer_makes_the_firm_talk_back_and_a_proposal_becomes_a_row(ecosystem):
    eco = ecosystem
    firm = _firm(eco)
    qid = ask.ask(eco.db, firm.firm_key, "review", "read?", {}, "review:t")
    ask.answer(eco.db, qid, "claude", "Your stops are too loose.")
    (fq,) = [q for q in ask.open_questions(eco.db, limit=50) if q["topic"] == "proposal"]
    assert fq["firm_key"] == firm.firm_key
    assert "stop_loss_pct" in fq["context"]["genes"]
    ask.answer(eco.db, fq["id"], "claude",
               '{"changes": {"stop_loss_pct": 8}, "why": "cap the losers"}')
    (row,) = proposals.recent(eco.db)
    assert row["status"] == "awaiting_test"
    assert json.loads(row["changes"]) == {"stop_loss_pct": "8.00"}


def test_a_proposal_answer_does_not_ask_for_yet_another_proposal(ecosystem):
    eco = ecosystem
    firm = _firm(eco)
    qid = ask.ask(eco.db, firm.firm_key, "proposal", "genes?", {}, "p:1")
    ask.answer(eco.db, qid, "claude", '{"changes": {}, "why": "nothing to change"}')
    assert [q for q in ask.open_questions(eco.db, limit=50) if q["topic"] == "proposal"] == []
    assert proposals.recent(eco.db) == []


def _pending(eco, changes):
    firm = _firm(eco)
    return eco.db.insert("ai_proposals", {
        "firm_key": firm.firm_key, "question_id": 1, "proposed_by": "claude",
        "why": "w", "changes": json.dumps(changes), "status": "awaiting_test"})


def test_the_mind_really_backtests_a_proposal(ecosystem):
    """No stand-in here: Evolver.trial runs both genomes on the village's bars."""
    eco = ecosystem
    pid = _pending(eco, {"fast_window": 5})
    note = proposals.test_one(eco, eco.market())
    row = eco.db.query_one("SELECT * FROM ai_proposals WHERE id = ?", (pid,))
    assert row["status"] in ("refused", "filed") and row["tested_at"]
    assert row["fitted_before"] is not None and row["fitted_after"] is not None
    assert f"#{pid}" in note


def test_a_held_out_loser_is_refused_and_never_filed(ecosystem, monkeypatch):
    eco = ecosystem
    pid = _pending(eco, {"fast_window": 5})
    monkeypatch.setattr(eco.evolver, "trial", lambda *a, **k: {
        "enough_holdout": True, "holdout_bars": 60, "fitted_before": Decimal("1"),
        "fitted_after": Decimal("9"), "holdout_before": Decimal("2"),
        "holdout_after": Decimal("1")})
    proposals.test_one(eco, eco.market())
    row = eco.db.query_one("SELECT * FROM ai_proposals WHERE id = ?", (pid,))
    assert row["status"] == "refused" and row["approval_id"] is None


def test_a_held_out_winner_is_filed_to_the_council_and_adopted_keeping_its_inheritance(
        ecosystem, monkeypatch):
    eco = ecosystem
    firm = _firm(eco)
    firm.genome = {**(firm.genome or {}), "inherited_from": "firm_old", "analysts": ["technical"]}
    eco.store.upsert_firm(firm)
    pid = _pending(eco, {"fast_window": 5})
    monkeypatch.setattr(eco.evolver, "trial", lambda *a, **k: {
        "enough_holdout": True, "holdout_bars": 60, "fitted_before": Decimal("1"),
        "fitted_after": Decimal("2"), "holdout_before": Decimal("1"),
        "holdout_after": Decimal("3")})
    proposals.test_one(eco, eco.market())
    row = eco.db.query_one("SELECT * FROM ai_proposals WHERE id = ?", (pid,))
    assert row["status"] == "filed" and row["approval_id"]
    approval = eco.gate.get(row["approval_id"])
    assert approval.action == "adopt_genome"
    proposals.adopt(eco, approval.details, "the council")
    after = eco.store.get_firm(firm.firm_key)
    assert after.genome["fast_window"] == 5
    assert after.genome["inherited_from"] == "firm_old" and after.genome["analysts"] == ["technical"]


def _evidence(**p):
    ev = CouncilEvidence(action="adopt_genome", firm_key="f", firm_exists=True, status="active")
    ev.notes["proposal"] = {"changes": {"fast_window": 5}, **p}
    return ev


def test_the_council_grants_a_held_out_winner():
    r = Council().rule(_evidence(holdout_before="1", holdout_after="3", holdout_bars=60,
                                 fitted_before="1", fitted_after="2"))
    assert r.verdict == "grant"


def test_the_council_vetoes_anything_that_did_not_win_out_of_sample():
    r = Council().rule(_evidence(holdout_before="3", holdout_after="1", holdout_bars=60,
                                 fitted_before="1", fitted_after="9"))
    assert r.verdict == "defer" and "held_out_win" in r.vetoes
    r = Council().rule(_evidence(holdout_before=None, holdout_after=None, holdout_bars=0))
    assert r.verdict == "defer"


def test_the_same_change_twice_is_one_proposal(ecosystem):
    eco = ecosystem
    firm = _firm(eco)
    for i in range(2):
        qid = ask.ask(eco.db, firm.firm_key, "proposal", "genes?", {}, f"p:{i}")
        ask.answer(eco.db, qid, "claude", '{"changes": {"stop_loss_pct": 8}, "why": "w"}')
    assert len(proposals.recent(eco.db)) == 1
