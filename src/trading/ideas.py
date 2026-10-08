"""The operator's ideas, put where the village learns.

The operator talks to the village (`chat.py`) and sends it things
(`inbox.py`). Much of what they say is not a trade but an idea: "the momentum
firms should sit out the first half hour", "look at this way of cutting AI
costs", a reel about an agent that funds itself. Until now those were words in
a chat log. This hands each idea to the part of the village that can use it,
through doors that already existed:

* **To a firm** (one, or every firm): the idea is filed as a question the firm
  asked and the operator answered (`ask.answer`, topic ``operator``). That puts
  it in the firm's memory as outside advice, where its lessons live, and the
  firm talks back: it asks the outside minds to turn the advice into concrete
  gene changes (`proposals.follow_up`). A change is backtested on held-out bars
  it was never chosen on, and only a held-out winner goes to the council. The
  operator can make a firm *try* an idea; the evidence decides whether it keeps
  it, exactly as with any other adviser.
* **To the village as a whole** (the council, the mind, a new tool or data
  source, how the village is run): the idea joins the daily research review
  (`ask._research_review`), where the outside minds say whether and how to test
  it, and two of them agreeing puts it on the weekly shortlist for the operator.

Every idea is kept in `intel` as source ``your_ideas`` (who it went to, where it
came from, the questions it opened), so the chat can say what became of it.
Nothing here moves money, changes a gene directly or skips a check.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Optional

from ..db.connection import utcnow_iso

SOURCE = "your_ideas"
WHO = "Robbie (operator)"
#: Targets that mean "every firm".
ALL_FIRMS = ("firms", "all", "every firm", "all firms")

_IDEA_LINE = re.compile(r"^\s*IDEA:\s*(\{.*\})\s*$", re.M)
_ANY_IDEA_LINE = re.compile(r"^\s*IDEA:.*$", re.M)


def parse(text: str) -> tuple:
    """(the text without its IDEA lines, the ideas). Bad lines are dropped."""
    out = []
    for raw in _IDEA_LINE.findall(text or ""):
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            continue
        idea = str((d or {}).get("idea") or "").strip() if isinstance(d, dict) else ""
        if idea:
            out.append({"idea": idea[:1500], "to": str(d.get("to") or "village").strip()[:80]})
    clean = _ANY_IDEA_LINE.sub("", text or "").strip()
    return re.sub(r"\n{3,}", "\n\n", clean), out


def _firms(db, to: str) -> list:
    from .desk import DESK

    rows = db.query("SELECT firm_key, status FROM firms WHERE status IN ('active', 'paused') "
                    "ORDER BY id")
    keys = [r["firm_key"] for r in rows if r["firm_key"] != DESK]
    if to.lower() in ALL_FIRMS:
        return keys
    return [k for k in keys if k == to]


def _to_firm(db, firm_key: str, idea: str, origin: str, digest: str) -> Optional[int]:
    from . import ask

    q = ask.ask(db, firm_key, "operator",
                f"{WHO}, who runs the village, has an idea for you ({origin}).",
                {"idea": idea, "from": WHO, "origin": origin},
                f"operator:{digest}:{firm_key}")
    if q is None:
        # Too many questions open, or already given: keep it as advice anyway.
        firm = db.query_one("SELECT id FROM firms WHERE firm_key = ?", (firm_key,))
        db.insert("trade_memory", {
            "firm_id": (firm or {}).get("id"), "symbol": "", "memory_type": "outside_advice",
            "summary": f"{WHO} ({origin}): {idea[:600]}",
            "payload": json.dumps({"origin": origin}), "outcome": "", "reward": 0,
            "created_at": utcnow_iso()})
        return None
    ask.answer(db, q, WHO, idea, model="operator")
    return q


def give(db, idea: str, to: str = "village", origin: str = "in the chat") -> dict:
    """Hand one idea to whoever can use it. Returns where it went."""
    from . import intel

    idea = " ".join((idea or "").split())
    to = (to or "village").strip()
    digest = hashlib.sha1(f"{idea}|{to}".encode()).hexdigest()[:12]
    firms = _firms(db, to)
    questions = []
    for key in firms:
        try:
            q = _to_firm(db, key, idea, origin, digest)
        except Exception:  # noqa: BLE001 - one firm is not the idea
            q = None
        if q:
            questions.append(q)
    where = "firms" if firms else "village"
    intel.upsert(db, SOURCE, digest, title=idea[:500],
                 detail={"to": to, "went_to": where, "firms": firms, "questions": questions,
                         "origin": origin, "given_at": utcnow_iso()})
    return {"to": to, "firms": firms, "questions": questions, "village": not firms}


def describe(result: dict) -> str:
    """One line for under the village's reply: where the idea went."""
    firms = result.get("firms") or []
    if firms:
        names = ", ".join(firms[:6]) + (f" and {len(firms) - 6} more" if len(firms) > 6 else "")
        return (f"Idea given to {names} as advice. Each will work out what it would change "
                "and test it on past prices before the council decides.")
    return ("Idea added to the village's research review. The outside minds will say "
            "whether and how to test it.")


def recent(db, limit: int = 12) -> list:
    from . import intel

    return intel.recent(db, SOURCE, limit)


__all__ = ["SOURCE", "describe", "give", "parse", "recent"]
