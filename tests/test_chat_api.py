"""The chat answered on Railway with its own key (src/trading/chat_api.py).

No network: the model call is replaced. What is pinned: nothing runs without a
key, a waiting question gets the village's answer, an order in that answer
still only becomes a card waiting for the tap, and a failed call leaves the
question to be tried again.
"""

from __future__ import annotations

from src.trading import chat, chat_api, desk


def test_without_a_key_nothing_answers(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not chat_api.enabled()
    assert chat_api.answer_soon("sqlite:///:memory:") is None


def test_a_waiting_question_is_answered_and_orders_wait_for_the_tap(db, monkeypatch):
    seen = []

    def fake(prompt):
        seen.append(prompt)
        return 'On it.\nORDER: {"side": "buy", "symbol": "NVDA", "dollars": 500}'

    monkeypatch.setattr(chat_api, "_ask", fake)
    q = chat.post(db, "buy $500 of NVDA")
    assert chat_api.answer_waiting(db) == 1
    assert "You are the Village" in seen[0] and "buy $500 of NVDA" in seen[0]
    reply = chat.history(db)[-1]
    assert reply["role"] == "village" and reply["text"] == "On it."
    assert "(api)" in reply["answered_by"]
    assert [o["status"] for o in desk.orders(db)] == ["proposed"]
    assert chat.waiting(db) == [] and q


def test_a_failed_call_leaves_the_question_for_another_try(db, monkeypatch):
    def boom(prompt):
        raise RuntimeError("overloaded")

    monkeypatch.setattr(chat_api, "_ask", boom)
    chat.post(db, "hey")
    assert chat_api.answer_waiting(db) == 0
    assert [q["status"] for q in chat.waiting(db)] == ["waiting"]
