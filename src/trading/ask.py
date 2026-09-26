"""Firms ask other minds questions, and hear the answers as outside advice.

The village already had the idea (`council_of_ais.py`): a question queue any
mind can answer, where an answer is a *source* — evidence that a model said
something, never an instruction. That queue was files on the asking machine's
disk, which on Railway is a container nothing can reach. This is the same
contract in the shared ledger (migration 028).

**When a firm asks.** Three moments, each asked once (`dedupe_key`):

* **a new heir** asks what to change, given exactly why its predecessor died;
* **a funded firm that has gone quiet** — no fill in three days — asks why it
  is finding nothing to do;
* **every funded firm, weekly**, asks for a read on its own numbers.

At most `MAX_OPEN` questions wait unanswered at a time, so a village with no
answerer running stops asking rather than piling up a backlog.

**What an answer can do.** It is stored against the question, and delivered to
the asking firm's memory as `outside_advice` — where the firm's lessons already
live — with who said it. It changes no gene, sizes no order and moves no
capital. A firm that "takes the advice" does so only through the ordinary
machinery: evolution, the court, or a person.

**Who answers.** `scripts/answer_questions.py` on the operator's PC, through
Claude Code on the operator's own plan. The browser-driven models (ChatGPT,
Gemini, DeepSeek) answer the same questions through the same `answer()` when
that is running; each mind answers each question at most once.

Off unless `TRADE_ASK_ENABLED` is set.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..db.connection import to_datetime, utcnow_iso

MAX_OPEN = 24
QUIET_DAYS = 3
#: A firm with less than this in cash has nothing to ask about yet.
MIN_CASH = 1000

GUARDRAILS = (
    "You are advising one firm in a paper-trading village of competing strategy "
    "firms. Answer the question in under 250 words. Be specific to the numbers "
    "given. Suggest what to test or change and why; say plainly when the sample "
    "is too small to conclude anything. You cannot place orders and should not "
    "name position sizes. If you do not know, say so."
)


def ask(db, firm_key: str, topic: str, question: str, context: dict,
        dedupe_key: str) -> Optional[int]:
    """File a question once. Returns its id, or None if it was already asked or
    too many are waiting."""
    try:
        if db.query_one("SELECT id FROM ai_questions WHERE dedupe_key = ?", (dedupe_key,)):
            return None
        waiting = db.query_one("SELECT COUNT(*) AS n FROM ai_questions WHERE status = 'open'")
        if int((waiting or {}).get("n") or 0) >= MAX_OPEN:
            return None
        return db.insert("ai_questions", {
            "firm_key": firm_key, "topic": topic, "question": question,
            "context": json.dumps(context, default=str), "dedupe_key": dedupe_key,
            "status": "open", "asked_at": utcnow_iso(),
        })
    except Exception:  # noqa: BLE001 - an unmigrated ledger cannot ask
        return None


def open_questions(db, answered_by: str = "", limit: int = 20) -> list:
    """Questions still waiting — or, given a mind, ones that mind has not answered."""
    if answered_by:
        rows = db.query(
            "SELECT q.* FROM ai_questions q WHERE NOT EXISTS ("
            " SELECT 1 FROM ai_answers a WHERE a.question_id = q.id AND a.answered_by = ?)"
            " ORDER BY q.id LIMIT ?", (answered_by, limit))
    else:
        rows = db.query("SELECT * FROM ai_questions WHERE status = 'open' ORDER BY id LIMIT ?",
                        (limit,))
    for r in rows:
        try:
            r["context"] = json.loads(r.get("context") or "{}")
        except (TypeError, ValueError):
            r["context"] = {}
    return rows


def prompt_for(q: dict) -> str:
    return (f"{GUARDRAILS}\n\nFirm: {q['firm_key']}\nTopic: {q['topic']}\n"
            f"Question: {q['question']}\n\nWhat the village knows (JSON):\n"
            f"{json.dumps(q['context'], indent=1, default=str)[:6000]}")


def answer(db, question_id: int, answered_by: str, text: str, model: str = "") -> bool:
    """Record one mind's answer and hand it to the firm's memory. Idempotent per mind."""
    text = (text or "").strip()
    if not text:
        return False
    q = db.query_one("SELECT * FROM ai_questions WHERE id = ?", (question_id,))
    if q is None or db.query_one(
            "SELECT id FROM ai_answers WHERE question_id = ? AND answered_by = ?",
            (question_id, answered_by)):
        return False
    db.insert("ai_answers", {"question_id": question_id, "answered_by": answered_by,
                             "model": model, "answer": text, "answered_at": utcnow_iso()})
    db.execute("UPDATE ai_questions SET status = 'answered' WHERE id = ?", (question_id,))
    try:
        heir = (json.loads(q.get("context") or "{}") or {}).get("deliver_to") or ""
    except (TypeError, ValueError):
        heir = ""
    for key, prefix in ((q["firm_key"], ""), (heir, f"from {q['firm_key']}, which died: ")):
        if not key:
            continue
        firm = db.query_one("SELECT id FROM firms WHERE firm_key = ?", (key,))
        db.insert("trade_memory", {
            "firm_id": (firm or {}).get("id"),
            "symbol": "",
            "memory_type": "outside_advice",
            "summary": f"{prefix}{answered_by} on '{q['question'][:80]}': {text[:600]}",
            "payload": json.dumps({"question_id": question_id, "model": model,
                                   "topic": q["topic"], "asked_by": q["firm_key"]}),
            "outcome": "",
            "reward": 0,
            "created_at": utcnow_iso(),
        })
    return True


def advice_for(db, firm_id) -> list:
    """What outside minds have told this firm, newest first."""
    try:
        return [r["summary"] for r in db.query(
            "SELECT summary FROM trade_memory WHERE firm_id = ? AND memory_type IN "
            "('outside_advice', 'inherited_advice') ORDER BY id DESC LIMIT 8", (firm_id,))]
    except Exception:  # noqa: BLE001
        return []


def recent(db, limit: int = 10) -> list:
    try:
        qs = db.query("SELECT * FROM ai_questions ORDER BY id DESC LIMIT ?", (limit,))
        for q in qs:
            q["answers"] = db.query(
                "SELECT * FROM ai_answers WHERE question_id = ? ORDER BY id", (q["id"],))
        return qs
    except Exception:  # noqa: BLE001
        return []


# -- when firms ask ------------------------------------------------------------
def _card_summary(card) -> dict:
    if card is None:
        return {}
    keys = ("equity", "cash", "return_pct", "drawdown_pct", "win_rate_pct", "sharpe",
            "trades", "closed_trades", "consecutive_losses", "score", "sufficient_data")
    return {k: getattr(card, k, None) for k in keys}


def _profile(firm, card) -> dict:
    genome = dict(firm.genome or {})
    for bulky in ("inherited_lesson", "predecessor_diagnosis"):
        genome.pop(bulky, None)
    return {"firm": firm.firm_key, "name": firm.name, "strategy": firm.strategy,
            "asset_class": firm.asset_class, "universe": list(firm.universe or []),
            "allocation": str(firm.allocation), "genome": genome,
            "scorecard": _card_summary(card)}


def _with_advice(db, firm, profile: dict) -> dict:
    return {**profile, "advice_already_given": advice_for(db, firm.id)}


def _estates_ask(eco) -> list:
    """A wound-up firm with a living heir asks, once, what its heir should learn.

    Asked by the dead, answered into the heir's memory as well as the estate's.
    Only the direct predecessor of a living firm asks: the other thirty-odd
    estates have no one left to tell.
    """
    notes = []
    living = {f.firm_key: f for f in eco.store.firms()
              if f.status in ("active", "paused")}
    by_key = {f.firm_key: f for f in eco.store.firms()}
    for heir in living.values():
        dead_key = (heir.genome or {}).get("inherited_from")
        dead = by_key.get(dead_key or "")
        if dead is None or dead.status in ("active", "paused"):
            continue
        lessons = [r["summary"] for r in eco.db.query(
            "SELECT summary FROM trade_memory WHERE firm_id = ? AND memory_type = "
            "'bankruptcy' ORDER BY id DESC LIMIT 2", (dead.id,))]
        fills = eco.store.fills(dead.id, limit=40)
        q = ask(eco.db, dead.firm_key, "estate",
                f"I was wound up ({dead.kill_reason or dead.status}). Looking at my whole "
                f"record, what should my heir {heir.firm_key} learn from me, and what "
                "should it stop doing?",
                {**_profile(dead, None),
                 "cause_of_death": dead.kill_reason,
                 "postmortem": lessons,
                 "advice_i_was_given": advice_for(eco.db, dead.id),
                 "last_fills": [{"symbol": f.symbol, "side": f.side, "qty": str(f.quantity),
                                 "price": str(f.price), "pnl": str(f.realized_pnl)}
                                for f in fills],
                 "deliver_to": heir.firm_key},
                f"estate:{dead.firm_key}")
        if q:
            notes.append(f"{dead.firm_key} (wound up) asked what {heir.firm_key} should learn (#{q})")
    return notes


def _research_review(eco, now: datetime) -> list:
    """Once a day, the village asks what in its GitHub and Hugging Face finds is worth testing.

    The repo scout only records; nothing reads its finds. This hands the newest
    ones to the outside minds with the village's own constraints, and asks for a
    verdict per find. The answer is advice for whoever turns a find into a test:
    the strategy court still judges any strategy before it trades, and nothing
    found is ever run.
    """
    from . import intel

    finds = []
    for source in ("github", "huggingface_models", "huggingface_datasets"):
        for r in intel.recent(eco.db, source, limit=8):
            finds.append({"source": source, "name": r["item_key"], "url": r.get("url"),
                          "stars_or_likes": r.get("score"),
                          "about": (r.get("title") or "")[:200],
                          "detail": r.get("detail")})
    if not finds:
        return []
    q = ask(eco.db, "village", "research",
            "These are the newest GitHub repositories and Hugging Face models and "
            "datasets the village's scout found. For each, is it worth testing here, "
            "and if so what exactly would we test and how would we know it worked? "
            "Flag anything that looks like a scam, a malware lure, or survivorship-"
            "biased backtesting. Be brief per item; most will be 'skip'.",
            {"village": "paper-trading firms on Alpaca: US equities/ETFs and crypto "
                        "majors plus DOGE/SHIB/PEPE/WIF, 15-minute bars; strategies are "
                        "small genomes judged by a strategy court; no code from these "
                        "finds is ever executed without review",
             "finds": finds},
            f"research:{now.strftime('%Y-%m-%d')}")
    return [f"the village asked outside minds to review {len(finds)} research find(s) (#{q})"] if q else []


def consider(eco, cards_by_id: dict, now: Optional[datetime] = None) -> list:
    """File whatever questions are due this bar. Returns one note per question."""
    now = now or datetime.now(timezone.utc)
    week = now.strftime("%G-W%V")
    notes = _estates_ask(eco) + _research_review(eco, now)
    for firm in eco.store.active_firms():
        # Cash, not allocation: a wound-up estate brought back to "active" can
        # carry a few hundred dollars of allocation on paper and nothing in the
        # till. The first live run let two of those fill half the queue.
        if float(firm.cash or 0) < MIN_CASH:
            continue
        card = cards_by_id.get(firm.id)
        profile = _with_advice(eco.db, firm, _profile(firm, card))
        genome = firm.genome or {}

        if genome.get("inherited_from"):
            q = ask(eco.db, firm.firm_key, "heir",
                    f"My predecessor {genome['inherited_from']} was wound up. Given why it "
                    "died, what should I do differently, and what should I test first?",
                    {**profile, "predecessor_lesson": genome.get("inherited_lesson"),
                     "predecessor_diagnosis": genome.get("predecessor_diagnosis")},
                    f"heir:{firm.firm_key}")
            if q:
                notes.append(f"{firm.firm_key} asked about its predecessor (#{q})")

        last = eco.store.fills(firm.id, limit=1)
        last_at = to_datetime(getattr(last[0], "as_of", None)) if last else None
        if last_at is None or now - last_at > timedelta(days=QUIET_DAYS):
            recent_props = [
                {"symbol": p.symbol, "side": p.side, "status": p.status,
                 "why": (p.rationale or "")[:160] if hasattr(p, "rationale") else ""}
                for p in eco.store.proposals(firm.id, limit=8)
            ]
            q = ask(eco.db, firm.firm_key, "quiet",
                    f"I have not filled a trade in over {QUIET_DAYS} days. Given my "
                    "strategy, universe and settings, why might I be finding nothing, "
                    "and is that correct behaviour or a problem?",
                    {**profile, "last_fill": str(last_at) if last_at else None,
                     "recent_proposals": recent_props},
                    f"quiet:{firm.firm_key}:{week}")
            if q:
                notes.append(f"{firm.firm_key} asked why it has gone quiet (#{q})")

        q = ask(eco.db, firm.firm_key, "review",
                "Here is my week. What is your read on these numbers, and what one "
                "thing would you look at first?",
                profile, f"review:{firm.firm_key}:{week}")
        if q:
            notes.append(f"{firm.firm_key} asked for its weekly review (#{q})")
    return notes


__all__ = ["GUARDRAILS", "MAX_OPEN", "answer", "ask", "consider", "open_questions",
           "prompt_for", "recent"]
