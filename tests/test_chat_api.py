"""The chat answered on Railway with its own keys (src/trading/chat_api.py).

No network: the model calls are replaced. What is pinned: nothing runs without
a key, the brain the operator picked answers and says so, the next one stands
in when it fails, an order in an answer still only becomes a card waiting for
the tap, and a question asked of the PC is left to the PC for a while.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.trading import chat, chat_api, desk

KEYS = [b.env for b in chat_api.BRAINS]


@pytest.fixture
def keys(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)

    def set_(*names):
        for n in names:
            monkeypatch.setenv(chat_api.BY_KEY[n].env, "test-key")
    return set_


def test_without_a_key_nothing_answers(keys):
    assert not chat_api.enabled()
    assert chat_api.answer_soon("sqlite:///:memory:") is None


def test_the_picked_brain_answers_and_orders_wait_for_the_tap(db, keys, monkeypatch):
    keys("claude", "chatgpt")
    seen = []

    def fake(brain, prompt):
        seen.append((brain.key, prompt))
        return 'On it.\nORDER: {"side": "buy", "symbol": "NVDA", "dollars": 500}'

    monkeypatch.setattr(chat_api, "_ask", fake)
    chat.post(db, "buy $500 of NVDA", "chatgpt")
    assert chat_api.answer_waiting(db) == 1
    brain, prompt = seen[0]
    assert brain == "chatgpt"
    assert "You are the Village" in prompt and "you run on ChatGPT" in prompt
    assert "buy $500 of NVDA" in prompt
    reply = chat.history(db)[-1]
    assert reply["role"] == "village" and reply["text"] == "On it."
    assert reply["answered_by"].startswith("ChatGPT · ")
    assert [o["status"] for o in desk.orders(db)] == ["proposed"]
    assert chat.waiting(db) == []


def test_the_next_brain_stands_in_and_a_brain_without_web_is_told(db, keys, monkeypatch):
    keys("claude", "deepseek")
    prompts = {}

    def fake(brain, prompt):
        prompts[brain.key] = prompt
        if brain.key == "claude":
            raise RuntimeError("overloaded")
        return "Here."

    monkeypatch.setattr(chat_api, "_ask", fake)
    chat.post(db, "hey", "claude")
    assert chat_api.answer_waiting(db) == 1
    assert chat.history(db)[-1]["answered_by"].startswith("DeepSeek · ")
    assert "cannot search the web" in prompts["deepseek"]
    assert "can search the web" in prompts["claude"]


def test_when_every_brain_fails_the_question_waits_for_another_try(db, keys, monkeypatch):
    keys("gemini")

    def boom(brain, prompt):
        raise RuntimeError("quota")

    monkeypatch.setattr(chat_api, "_ask", boom)
    chat.post(db, "hey", "gemini")
    assert chat_api.answer_waiting(db) == 0
    assert [q["status"] for q in chat.waiting(db)] == ["waiting"]


def test_a_question_for_the_pc_is_left_to_the_pc_for_a_while(db, keys, monkeypatch):
    keys("claude")
    monkeypatch.setattr(chat_api, "_ask", lambda brain, prompt: "ok")
    q = chat.post(db, "hello PC", chat.PC)
    assert chat.claim(db, website=True) is None
    old = (chat.utcnow() - chat.PC_GRACE - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.update("village_chat", q, {"created_at": old})
    assert chat_api.answer_waiting(db) == 1


def test_the_pc_leaves_a_question_for_the_website_brains_for_a_while(db):
    q = chat.post(db, "hi", "chatgpt")
    assert chat.claim(db) is None
    old = (chat.utcnow() - chat.PC_GRACE - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.update("village_chat", q, {"created_at": old})
    assert chat.claim(db)["id"] == q
