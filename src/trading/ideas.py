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


# =========================================================================
# what became of each idea
# =========================================================================
def _json(v, default):
    try:
        return json.loads(v) if isinstance(v, str) else (v if v is not None else default)
    except (TypeError, ValueError):
        return default


def _firm_fate(db, firm_key: str, question_id) -> dict:
    """Where one firm got to with one idea: advice -> proposal -> test -> council."""
    if not question_id:
        return {"firm": firm_key, "stage": "kept as advice",
                "detail": "too many questions were open, so no adviser was asked "
                          "what to change"}
    ask_row = db.query_one("SELECT id, status FROM ai_questions WHERE topic = 'proposal' "
                           "AND dedupe_key LIKE ?", (f"proposal:{question_id}:%",))
    if ask_row is None:
        return {"firm": firm_key, "stage": "kept as advice",
                "detail": "no adviser was asked what to change"}
    answers = db.query("SELECT answered_by, answer FROM ai_answers WHERE question_id = ?",
                       (ask_row["id"],))
    if not answers:
        return {"firm": firm_key, "stage": "waiting",
                "detail": "waiting for an adviser (Claude on your PC) to say what to change"}
    props = db.query("SELECT id, changes, status, verdict FROM ai_proposals "
                     "WHERE question_id = ? ORDER BY id", (ask_row["id"],))
    if not props:
        why = ""
        for a in answers:
            why = str((_json(_answer_json(a.get("answer")), {}) or {}).get("why") or "")
            if why:
                break
        return {"firm": firm_key, "stage": "no change",
                "detail": "the adviser found no setting of this firm that the idea maps to"
                          + (f": {why[:200]}" if why else "")}
    p = props[-1]
    stage = {"awaiting_test": "testing", "filed": "with the Council",
             "adopted": "adopted", "refused": "refused"}.get(p["status"], p["status"])
    return {"firm": firm_key, "stage": stage,
            "detail": f"{p['changes']}: {str(p.get('verdict') or 'not tested yet')[:240]}"}


def _answer_json(text) -> str:
    text = str(text or "")
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else "{}"


def _village_fate(db, title: str) -> dict:
    """Where a village idea got to in the research review and the shortlist."""
    from . import shortlist

    key = shortlist._key(title[:200])
    reviewed, verdicts = 0, []
    for q in db.query("SELECT id, context FROM ai_questions WHERE topic = 'research' "
                      "ORDER BY id DESC LIMIT 60"):
        finds = (_json(q.get("context"), {}) or {}).get("finds") or []
        if not any(isinstance(f, dict) and shortlist._key(f.get("name") or "") == key
                   for f in finds):
            continue
        for a in db.query("SELECT answered_by, answer FROM ai_answers WHERE question_id = ?",
                          (q["id"],)):
            reviewed += 1
            for v in shortlist.verdicts(a.get("answer")):
                if shortlist._key(v["name"]) == key:
                    verdicts.append({"mind": a["answered_by"], **v})
    if not reviewed:
        return {"stage": "not reviewed yet",
                "detail": "no research review that included it has been answered"}
    tests = [v for v in verdicts if v["verdict"] == "test"]
    danger = [v for v in verdicts if v["verdict"] == "danger"]
    if danger:
        return {"stage": "flagged", "detail": f"{danger[0]['mind']}: {danger[0]['why'][:240]}"}
    minds = {v["mind"] for v in tests}
    if len(minds) >= shortlist.MIN_MINDS:
        return {"stage": "shortlisted, not built",
                "detail": f"{len(minds)} reviewers said test it ({tests[0]['test'][:200]}); "
                          "it needs someone to build it"}
    if tests:
        return {"stage": "one reviewer said test it",
                "detail": f"{tests[0]['mind']}: {tests[0]['test'][:200]}; the shortlist "
                          f"needs {shortlist.MIN_MINDS} reviewers to agree"}
    return {"stage": "reviewed, skipped",
            "detail": f"reviewed {reviewed} time(s); no reviewer said to test it"}


def status(db, limit: int = 40) -> list:
    """Every idea given, newest first, with what became of it."""
    out = []
    for r in recent(db, limit):
        d = r.get("detail") or {}
        firms = d.get("firms") or []
        questions = d.get("questions") or []
        entry = {"idea": r.get("title") or "", "origin": d.get("origin") or "",
                 "given_at": d.get("given_at") or r.get("first_seen") or "",
                 "to": d.get("to") or "village"}
        try:
            if firms:
                by_firm = {}
                for q in questions:
                    row = db.query_one("SELECT firm_key FROM ai_questions WHERE id = ?", (q,))
                    if row:
                        by_firm[row["firm_key"]] = q
                entry["firms"] = [_firm_fate(db, f, by_firm.get(f)) for f in firms]
            else:
                entry["village"] = _village_fate(db, entry["idea"])
        except Exception as exc:  # noqa: BLE001 - one idea's history is not the list
            entry["error"] = str(exc)[:160]
        out.append(entry)
    return out


def recent(db, limit: int = 12) -> list:
    from . import intel

    return intel.recent(db, SOURCE, limit)


__all__ = ["SOURCE", "describe", "give", "parse", "recent", "status"]
