"""The village asks an outside mind a question, and keeps the answer.

    village writes  ->  data/ai_queue/open/<id>.json
    an agent reads  ->  answers it, moves it to answered/
    the village     ->  reads the answer on a later tick

**Why a queue and not a login.** The obvious build is to drive a logged-in
ChatGPT or DeepSeek session with a script. That is against those services'
terms, needs bot-detection defeated, breaks whenever the page changes, and would
mean storing somebody's password to do it. A queue needs none of that: it is two
directories, it works with any model, and the thing answering can be an agent
session, a paid API, or a person with a keyboard. Nothing here knows or cares
which.

**What the village may ask.** Questions about its own evidence — a number that
does not reconcile, a result it cannot explain, a strategy it has read about and
wants an opinion on before spending a backtest. It may not ask for an order, a
size, or a decision, and nothing in this module can produce one: an answer is
text on disk, read by a human or an agent, and the only path from here to a
trade runs through the same gate as everything else.

**Why the answers are stored, not acted on.** An outside model is a *source*,
with exactly the standing of a paper or a Reddit post — which is to say, none
until something tests it. This project has already measured what happens when a
confident claim is adopted because it sounded right: the Tritonix audit found
70-90% win rates that decomposed into drift and grid-search survivorship, and a
four-way stack built on a hand-picked universe tied a random control. An answer
here is evidence *that a model said something*, and nothing more. It gets a
`p-value ledger` entry if it is ever tested, like any other idea.

**Provenance is the whole point of the format.** Every answer records which
model, when, and against which question id. A claim whose origin is unrecorded
is indistinguishable from one the village invented, and this project has been
bitten by exactly that — a fee-blind +22% that nobody could trace, a figure
whose harness went missing.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "ai_queue"
OPEN = ROOT / "open"
ANSWERED = ROOT / "answered"

#: A question longer than this is a document, not a question, and the thing
#: answering it will skim. Keep it answerable.
MAX_QUESTION = 4000
MAX_ANSWER = 20000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Question:
    id: str
    asked_by: str            # which part of the village wants to know
    question: str
    context: str = ""        # the numbers, so the answer is about this village
    asked_at: str = field(default_factory=_now)
    answer: Optional[str] = None
    answered_by: str = ""    # which model, named — never "an AI"
    answered_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "asked_by": self.asked_by,
            "question": self.question,
            "context": self.context,
            "asked_at": self.asked_at,
            "answer": self.answer,
            "answered_by": self.answered_by,
            "answered_at": self.answered_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Question":
        return cls(
            id=str(d.get("id") or ""),
            asked_by=str(d.get("asked_by") or ""),
            question=str(d.get("question") or ""),
            context=str(d.get("context") or ""),
            asked_at=str(d.get("asked_at") or ""),
            answer=d.get("answer"),
            answered_by=str(d.get("answered_by") or ""),
            answered_at=str(d.get("answered_at") or ""),
        )


def ask(question: str, asked_by: str = "village", context: str = "") -> Question:
    """Put a question in the queue. Returns it; answers arrive later or never."""
    q = Question(
        id=uuid.uuid4().hex[:12],
        asked_by=str(asked_by)[:60],
        question=str(question)[:MAX_QUESTION],
        context=str(context)[:MAX_QUESTION],
    )
    OPEN.mkdir(parents=True, exist_ok=True)
    # Write beside and rename, so a reader never catches a half-written question.
    tmp = OPEN / f"{q.id}.json.tmp"
    tmp.write_text(json.dumps(q.to_dict(), indent=2))
    tmp.replace(OPEN / f"{q.id}.json")
    return q


def open_questions() -> list:
    """Everything waiting for an answer, oldest first."""
    if not OPEN.exists():
        return []
    out = []
    for path in sorted(OPEN.glob("*.json")):
        try:
            out.append(Question.from_dict(json.loads(path.read_text())))
        except (OSError, ValueError):
            continue        # a malformed question is not a reason to stop
    return sorted(out, key=lambda q: q.asked_at)


def answer(question_id: str, text: str, answered_by: str) -> Optional[Question]:
    """Record an answer and move the question to `answered/`.

    `answered_by` is required and is not allowed to be vague. "claude-opus-5",
    "gpt-5", "deepseek-v3", "robbie" — something a later reader can weigh. An
    unattributed answer is a rumour.
    """
    if not str(answered_by).strip():
        raise ValueError("answered_by is required — an unattributed answer is a rumour")

    path = OPEN / f"{question_id}.json"
    if not path.exists():
        return None
    try:
        q = Question.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return None

    q.answer = str(text)[:MAX_ANSWER]
    q.answered_by = str(answered_by)[:60]
    q.answered_at = _now()

    ANSWERED.mkdir(parents=True, exist_ok=True)
    tmp = ANSWERED / f"{q.id}.json.tmp"
    tmp.write_text(json.dumps(q.to_dict(), indent=2))
    tmp.replace(ANSWERED / f"{q.id}.json")
    path.unlink(missing_ok=True)
    return q


def answered(limit: int = 20) -> list:
    """Recent answers, newest first. Read by the console and by whoever cares."""
    if not ANSWERED.exists():
        return []
    out = []
    for path in sorted(ANSWERED.glob("*.json")):
        try:
            out.append(Question.from_dict(json.loads(path.read_text())))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda q: q.answered_at, reverse=True)[:limit]


__all__ = ["Question", "ask", "answer", "open_questions", "answered"]
