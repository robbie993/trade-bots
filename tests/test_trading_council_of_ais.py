"""The queue that lets the village ask an outside mind, and keep the answer."""

from __future__ import annotations

import pytest

from src.trading import council_of_ais as ai


@pytest.fixture(autouse=True)
def _tmp_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "ROOT", tmp_path / "ai_queue")
    monkeypatch.setattr(ai, "OPEN", tmp_path / "ai_queue" / "open")
    monkeypatch.setattr(ai, "ANSWERED", tmp_path / "ai_queue" / "answered")


def test_a_question_waits_until_something_answers_it():
    q = ai.ask("does this reconcile?", asked_by="ledger")
    assert [x.id for x in ai.open_questions()] == [q.id]
    assert ai.answered() == []


def test_answering_moves_it_and_keeps_who_said_it():
    q = ai.ask("why is this number wrong?")
    ai.answer(q.id, "premium booked at sale", answered_by="gpt-5")

    assert ai.open_questions() == []
    got = ai.answered()
    assert len(got) == 1
    assert got[0].answer == "premium booked at sale"
    assert got[0].answered_by == "gpt-5"


def test_an_unattributed_answer_is_refused():
    """A claim whose origin is unrecorded cannot be weighed later."""
    q = ai.ask("anything")
    with pytest.raises(ValueError):
        ai.answer(q.id, "trust me", answered_by="  ")
    assert len(ai.open_questions()) == 1, "it must stay open"


def test_answering_an_unknown_question_is_quiet():
    assert ai.answer("nosuchid", "hello", answered_by="gpt-5") is None


def test_a_malformed_file_does_not_stop_the_queue():
    """One bad file must not hide every other question."""
    good = ai.ask("readable")
    (ai.OPEN / "broken.json").write_text("{not json")
    assert [q.id for q in ai.open_questions()] == [good.id]


def test_long_input_is_truncated_not_refused():
    q = ai.ask("x" * (ai.MAX_QUESTION + 500))
    assert len(q.question) == ai.MAX_QUESTION
