"""The village answers on its own: the chat, answered on Railway with its own key.

`chat.py` keeps the conversation and builds what the model is told; until now
only the operator's PC answered it (`scripts/inbox_watch.py`, Claude Code on
the operator's own plan, checking every few minutes). With `ANTHROPIC_API_KEY`
set on the web service, a question is answered the moment it is asked, from
the website itself, on that key's own account. The PC still reads what was
sent (it has the signed-in browser and the speech-to-text) and still answers
the chat when no key is set.

The model gets the same snapshot and voice as on the PC, plus web search, and
nothing else: no tool here can trade, change a setting or approve anything.
An order the operator gives still only becomes a card with a Confirm button
(`desk.parse` in `chat.answer`).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

log = logging.getLogger(__name__)

#: The model and how hard it thinks. Overridable on the service.
MODEL = os.environ.get("VILLAGE_CHAT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("VILLAGE_CHAT_EFFORT", "medium")
#: Web searches one answer may make.
MAX_SEARCHES = 5
#: A server tool turn that pauses is resumed at most this many times.
MAX_RESUMES = 3


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _ask(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(max_retries=2, timeout=240.0)
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search",
              "max_uses": MAX_SEARCHES}]
    for _ in range(MAX_RESUMES + 1):
        with client.beta.messages.stream(
                model=MODEL, max_tokens=32000, messages=messages, tools=tools,
                output_config={"effort": EFFORT},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default") as stream:
            response = stream.get_final_message()
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason == "refusal":
            return "I can't help with that one."
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or "(no answer)"
    return "That took too many searches. Ask me again, maybe a bit narrower."


def answer_waiting(db) -> int:
    """Answer every question waiting in the chat. Returns how many."""
    from . import chat

    n = 0
    while True:
        q = chat.claim(db)
        if q is None:
            return n
        try:
            text = _ask(chat.prompt(db, q))
        except Exception as exc:  # noqa: BLE001 - one question is not the service
            log.warning("village chat #%s failed: %s", q["id"], exc)
            chat.fail(db, q["id"], str(exc))
            return n
        chat.answer(db, q["id"], text, by=f"{MODEL} (api)")
        n += 1


_lock = threading.Lock()


def answer_soon(database_url: str, wait: bool = True) -> Optional[threading.Thread]:
    """Answer in the background so the page returns at once; None without a key.

    `wait=False` gives up when an answer is already being written (the page's
    polling uses it to pick up a question a restart left behind).
    """
    if not enabled():
        return None

    def run() -> None:
        from ..db.connection import Database

        if not _lock.acquire(blocking=wait):
            return
        try:
            db = Database.from_url(database_url)
            try:
                answer_waiting(db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            log.exception("village chat answerer stopped")
        finally:
            _lock.release()

    t = threading.Thread(target=run, name="village-chat", daemon=True)
    t.start()
    return t


__all__ = ["answer_soon", "answer_waiting", "enabled"]
