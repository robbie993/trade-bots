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


def test_a_refused_proposal_goes_back_to_the_adviser_with_its_numbers(ecosystem, monkeypatch):
    eco = ecosystem
    pid = _pending(eco, {"fast_window": 5})
    monkeypatch.setattr(eco.evolver, "trial", lambda *a, **k: {
        "enough_holdout": True, "holdout_bars": 60, "fitted_before": Decimal("1"),
        "fitted_after": Decimal("1"), "holdout_before": Decimal("2"),
        "holdout_after": Decimal("1")})
    proposals.test_one(eco, eco.market())
    (q,) = [q for q in ask.open_questions(eco.db, limit=50) if q["dedupe_key"] == f"retry:{pid}"]
    assert q["context"]["result"]["holdout_after"] == "1"
    assert "refused" in q["question"]


def test_retries_are_capped_per_firm_per_day(ecosystem, monkeypatch):
    eco = ecosystem
    monkeypatch.setattr(proposals, "RETRIES_PER_DAY", 1)
    monkeypatch.setattr(eco.evolver, "trial", lambda *a, **k: {
        "enough_holdout": True, "holdout_bars": 60, "fitted_before": Decimal("1"),
        "fitted_after": Decimal("1"), "holdout_before": Decimal("2"),
        "holdout_after": Decimal("1")})
    _pending(eco, {"fast_window": 5})
    _pending(eco, {"fast_window": 6})
    proposals.test_one(eco, eco.market())
    proposals.test_one(eco, eco.market())
    retries = [q for q in ask.recent(eco.db, 50) if q["dedupe_key"].startswith("retry:")]
    assert len(retries) == 1


def test_a_proposal_tested_on_an_older_genome_is_retested_not_adopted(ecosystem, monkeypatch):
    """Live 2026-09-27: two winners for one firm, the second overwrote the first."""
    eco = ecosystem
    firm = _firm(eco)
    monkeypatch.setattr(eco.evolver, "trial", lambda *a, **k: {
        "enough_holdout": True, "holdout_bars": 60, "fitted_before": Decimal("1"),
        "fitted_after": Decimal("2"), "holdout_before": Decimal("1"),
        "holdout_after": Decimal("3")})
    first, second = _pending(eco, {"fast_window": 9}), _pending(eco, {"fast_window": 6})
    proposals.test_one(eco, eco.market())
    proposals.test_one(eco, eco.market())
    a = eco.gate.get(eco.db.query_one("SELECT approval_id FROM ai_proposals WHERE id = ?", (first,))["approval_id"])
    b = eco.gate.get(eco.db.query_one("SELECT approval_id FROM ai_proposals WHERE id = ?", (second,))["approval_id"])
    proposals.adopt(eco, a.details, "the council")
    note = proposals.adopt(eco, b.details, "the council")
    assert "not adopted" in note
    assert eco.store.get_firm(firm.firm_key).genome["fast_window"] == 9
    row = eco.db.query_one("SELECT status FROM ai_proposals WHERE id = ?", (second,))
    assert row["status"] == "awaiting_test"


def test_a_change_that_moves_nothing_says_so_and_names_the_seats(ecosystem, monkeypatch):
    """Live 2026-10-04: six changes scored -1.28 -> -1.28 and were each called "lost"."""
    eco = ecosystem
    monkeypatch.setattr(eco.evolver, "trial", lambda *a, **k: {
        "enough_holdout": True, "holdout_bars": 60, "fitted_before": Decimal("-1.28"),
        "fitted_after": Decimal("-1.28"), "holdout_before": Decimal("-1.28"),
        "holdout_after": Decimal("-1.28")})
    pid = _pending(eco, {"ibs_entry": "0.40"})
    proposals.test_one(eco, eco.market())
    row = eco.db.query_one("SELECT * FROM ai_proposals WHERE id = ?", (pid,))
    assert row["status"] == "refused"
    assert row["verdict"].startswith("changed nothing") and "seats are" in row["verdict"]


def test_a_configured_firm_is_tested_with_its_own_seats(ecosystem, monkeypatch):
    eco = ecosystem
    firm = _firm(eco)
    seen = {}

    def trial(firm, market, genome, analysts=()):
        seen["analysts"] = tuple(analysts)
        return {"enough_holdout": False, "holdout_bars": 0, "fitted_before": Decimal("1"),
                "fitted_after": Decimal("1"), "holdout_before": None, "holdout_after": None}

    monkeypatch.setattr(eco.evolver, "trial", trial)
    _pending(eco, {"fast_window": 5})
    proposals.test_one(eco, eco.market())
    assert seen["analysts"] == tuple(eco.specs()[firm.firm_key].analysts)


def test_advisers_see_fills_not_a_second_trades_count():
    from types import SimpleNamespace

    card = SimpleNamespace(trades=80, closed_trades=2, equity=1)
    summary = ask._card_summary(card)
    assert summary["fills"] == 80 and summary["closed_trades"] == 2
    assert "trades" not in summary
