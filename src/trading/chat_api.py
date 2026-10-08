"""The village answers on its own: the chat, answered on Railway with its own keys.

`chat.py` keeps the conversation and builds what the model is told; until now
only the operator's PC answered it (`scripts/inbox_watch.py`, Claude Code on
the operator's own plan, checking every few minutes). With one or more keys
set on the web service, a question is answered the moment it is asked, from
the website itself, on those keys' own accounts:

    ANTHROPIC_API_KEY   Claude    (web search)
    OPENAI_API_KEY      ChatGPT   (web search)
    GEMINI_API_KEY      Gemini
    DEEPSEEK_API_KEY    DeepSeek
    GROQ_API_KEY        Groq

The operator picks which one answers on the page; if it fails, the next one
with a key answers instead and the reply says who it was. The PC still reads
what was sent (it has the signed-in browser and the speech-to-text) and still
answers the chat when no key is set at all.

Every brain gets the same snapshot and voice, and nothing else: no tool here
can trade, change a setting or approve anything. An order the operator gives
only becomes a card with a Confirm button (`desk.parse` in `chat.answer`).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Brain:
    key: str          # what the page sends
    name: str         # what the operator reads
    env: str          # the key's variable
    model: str        # default model, overridable with VILLAGE_<KEY>_MODEL
    web: bool         # can search the web here
    base_url: str = ""  # OpenAI-compatible endpoint, for the ones that offer one

    @property
    def model_id(self) -> str:
        return os.environ.get(f"VILLAGE_{self.key.upper()}_MODEL", self.model)


BRAINS = (
    Brain("claude", "Claude", "ANTHROPIC_API_KEY", "claude-opus-5-5", True),
    Brain("chatgpt", "ChatGPT", "OPENAI_API_KEY", "gpt-5", True),
    Brain("gemini", "Gemini", "GEMINI_API_KEY", "gemini-2.5-flash", False,
          "https://generativelanguage.googleapis.com/v1beta/openai/"),
    Brain("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "deepseek-reasoner", False,
          "https://api.deepseek.com"),
    Brain("groq", "Groq", "GROQ_API_KEY", "openai/gpt-oss-120b", False,
          "https://api.groq.com/openai/v1"),
)
BY_KEY = {b.key: b for b in BRAINS}

#: Claude's thinking effort. Overridable on the service.
EFFORT = os.environ.get("VILLAGE_CHAT_EFFORT", "medium")
#: Web searches one answer may make.
MAX_SEARCHES = 5
#: A server tool turn that pauses is resumed at most this many times.
MAX_RESUMES = 3
TIMEOUT_S = 240.0


def available() -> list:
    """The brains with a key on this service, in the order they stand in for each other."""
    return [b for b in BRAINS if os.environ.get(b.env, "").strip()]


def enabled() -> bool:
    return bool(available())


# =========================================================================
# one call per brain
# =========================================================================
def _ask_claude(brain: Brain, prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(max_retries=2, timeout=TIMEOUT_S)
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search",
              "max_uses": MAX_SEARCHES}]
    for _ in range(MAX_RESUMES + 1):
        with client.beta.messages.stream(
                model=brain.model_id, max_tokens=32000, messages=messages, tools=tools,
                output_config={"effort": EFFORT},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default") as stream:
            response = stream.get_final_message()
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to answer")
        return "".join(b.text for b in response.content if b.type == "text").strip()
    raise RuntimeError("too many searches for one answer")


def _ask_chatgpt(brain: Brain, prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ[brain.env], max_retries=2, timeout=TIMEOUT_S)
    response = client.responses.create(model=brain.model_id, input=prompt,
                                       tools=[{"type": "web_search"}])
    return (response.output_text or "").strip()


def _ask_compatible(brain: Brain, prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ[brain.env], base_url=brain.base_url,
                    max_retries=2, timeout=TIMEOUT_S)
    response = client.chat.completions.create(
        model=brain.model_id, messages=[{"role": "user", "content": prompt}])
    return (response.choices[0].message.content or "").strip()


def _ask(brain: Brain, prompt: str) -> str:
    if brain.key == "claude":
        return _ask_claude(brain, prompt)
    if brain.key == "chatgpt":
        return _ask_chatgpt(brain, prompt)
    return _ask_compatible(brain, prompt)


# =========================================================================
# answering
# =========================================================================
def _order(wanted: str) -> list:
    """The asked-for brain first, then the others with a key.

    A question asked of the PC only reaches here once the PC has let it wait
    (`chat.PC_GRACE`); then the website's brains answer it in their order.
    """
    brains = available()
    first = [b for b in brains if b.key == wanted]
    return first + [b for b in brains if b.key != wanted]


def answer_waiting(db) -> int:
    """Answer every question waiting in the chat. Returns how many."""
    from . import chat

    n = 0
    while True:
        q = chat.claim(db, website=True)
        if q is None:
            return n
        errors = []
        for brain in _order(chat.asked_of(q)):
            try:
                text = _ask(brain, chat.prompt(db, q, engine=brain.name, web=brain.web))
            except Exception as exc:  # noqa: BLE001 - the next brain stands in
                log.warning("village chat #%s: %s failed: %s", q["id"], brain.name, exc)
                errors.append(f"{brain.name}: {str(exc)[:100]}")
                continue
            if text:
                chat.answer(db, q["id"], text, by=f"{brain.name} · {brain.model_id}")
                n += 1
                break
            errors.append(f"{brain.name}: empty answer")
        else:
            chat.fail(db, q["id"], "; ".join(errors) or "no key set")
            return n


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


__all__ = ["BRAINS", "BY_KEY", "answer_soon", "answer_waiting", "available", "enabled"]
