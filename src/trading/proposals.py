"""Advice turned into a proposal, tested, and put to the council.

The loop the operator asked for: a firm asks an outside mind (ask.py), hears
the answer, talks back — "then what exactly would you change in me?" — and
whatever comes back is proposed to the village rather than trusted.

    1. **Talk back.** Once a firm's question is answered, it asks the same mind
       for the advice as a bounded change to its own genes, as JSON. The genes,
       their current values and their legal ranges are in the question, so the
       answer can only name things the firm actually has.
    2. **Parse, don't obey.** `parse` keeps only real genes, clamps each value to
       the evolver's range and takes at most `MAX_CHANGES` of them. Anything
       else in the answer is ignored.
    3. **The mind tests it.** `test_one` backtests the proposal against the
       firm's current genome on `Evolver.trial` — evolution's own split, early
       bars and a held-out tail neither was chosen on. The numbers are written
       to `ai_proposals`.
    4. **Only a held-out winner reaches the council.** It is filed as an
       `adopt_genome` approval. The council's ADOPT panel reads the numbers
       from the ledger row, not from the request, and vetoes anything that did
       not win out of sample.
    5. **Adopted like a promotion.** If granted, `adopt` writes the new genes
       and keeps every non-gene key (an heir's seats, its inheritance), exactly
       as evolution does.

A proposal that loses the held-out bars is recorded as refused, with its
numbers, and goes no further. An outside mind can make the village test an
idea; it cannot make the village adopt one.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP
from typing import Optional

from ..db.connection import utcnow_iso
from ..money import D

MAX_CHANGES = 4

#: Topics whose answers a firm follows up on. The research review and a
#: proposal question itself are not advice to one firm's genome.
FOLLOW_UP = ("heir", "quiet", "review", "estate")


def genes_for(firm) -> dict:
    """Each gene the firm has: its current value and legal range."""
    from .brain.evolver import BASE_GENOME, GENES

    genome = firm.genome or {}
    return {name: {"now": str(genome.get(name, BASE_GENOME.get(name))),
                   "min": str(lo), "max": str(hi), "integer": is_int}
            for name, (lo, hi, is_int) in GENES.items()}


def follow_up(db, question: dict, answered_by: str, answer_text: str) -> Optional[int]:
    """The firm talks back: turn this advice into gene changes. Once per answer."""
    from . import ask

    if question.get("topic") not in FOLLOW_UP:
        return None
    ctx = question.get("context") or {}
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except ValueError:
            ctx = {}
    firm_key = ctx.get("deliver_to") or question["firm_key"]
    firm = db.query_one("SELECT * FROM firms WHERE firm_key = ?", (firm_key,))
    if firm is None or firm.get("status") not in ("active", "paused"):
        return None
    from .models import FirmRecord

    record = FirmRecord.from_row(firm)
    return ask.ask(
        db, firm_key, "proposal",
        "Thank you. Turn that advice into specific changes to my genes, if any gene "
        "change would help. Reply with ONLY a JSON object like "
        '{"changes": {"gene_name": value}, "why": "one sentence"} using at most '
        f"{MAX_CHANGES} genes from the list, each inside its min and max. If no gene "
        'change would help, reply {"changes": {}, "why": "..."}. The village will '
        "backtest your change on held-out history before its council decides.",
        {"your_advice": answer_text[:2500], "advised_by": answered_by,
         "genes": genes_for(record), "original_question": question.get("question")},
        f"proposal:{question['id']}:{answered_by}")


def parse(text: str) -> tuple:
    """(changes, why) from an answer. Only real genes, clamped, at most MAX_CHANGES."""
    from .brain.evolver import GENES

    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}, ""
    try:
        doc = json.loads(m.group(0))
    except ValueError:
        return {}, ""
    changes = {}
    for name, value in (doc.get("changes") or {}).items():
        if name not in GENES or len(changes) >= MAX_CHANGES:
            continue
        lo, hi, is_int = GENES[name]
        try:
            v = max(lo, min(hi, D(str(value))))
        except Exception:  # noqa: BLE001 - a value that is not a number is not a change
            continue
        # Round, don't truncate: "4.6" meant five, not four.
        changes[name] = (int(v.quantize(D("1"), rounding=ROUND_HALF_UP)) if is_int
                         else str(v.quantize(D("0.01"))))
    return changes, str(doc.get("why") or "")[:500]


def record(db, question: dict, answered_by: str, text: str) -> Optional[int]:
    """File a proposal from the answer to a `proposal` question."""
    changes, why = parse(text)
    if not changes:
        return None
    firm = db.query_one("SELECT genome FROM firms WHERE firm_key = ?", (question["firm_key"],))
    if firm is None:
        return None
    before = json.loads(firm.get("genome") or "{}") if isinstance(firm.get("genome"), str) \
        else (firm.get("genome") or {})
    if all(str(before.get(k)) == str(v) for k, v in changes.items()):
        return None                       # proposes what the firm already is
    # The same change for the same firm, already waiting or already judged, is
    # one idea: two advisers agreeing does not earn it a second backtest.
    for other in db.query("SELECT changes FROM ai_proposals WHERE firm_key = ?",
                          (question["firm_key"],)):
        if json.loads(other["changes"] or "{}") == changes:
            return None
    return db.insert("ai_proposals", {
        "firm_key": question["firm_key"], "question_id": question["id"],
        "proposed_by": answered_by, "why": why, "changes": json.dumps(changes),
        "genome_before": json.dumps(before), "genome_proposed": json.dumps({**before, **changes}),
        "status": "awaiting_test", "created_at": utcnow_iso(),
    })


def test_one(eco, market) -> Optional[str]:
    """The mind tests the oldest untested proposal. One per call: backtests are slow."""
    from ..agents.human_gate import ApprovalAction

    row = eco.db.query_one(
        "SELECT * FROM ai_proposals WHERE status = 'awaiting_test' ORDER BY id LIMIT 1")
    if row is None:
        return None
    firm = eco.store.get_firm(row["firm_key"])
    if firm is None or firm.status not in ("active", "paused"):
        eco.db.update("ai_proposals", row["id"], {
            "status": "refused", "verdict": "the firm is no longer alive",
            "tested_at": utcnow_iso()})
        return f"proposal #{row['id']} dropped: {row['firm_key']} is no longer alive"

    changes = json.loads(row["changes"])
    proposed = {**(firm.genome or {}), **changes}
    # Record what it is being tested against, so adoption can tell if that moved.
    eco.db.update("ai_proposals", row["id"], {"genome_before": json.dumps(firm.genome or {})})
    analysts = tuple((firm.genome or {}).get("analysts") or ("technical", "sentiment", "macro"))
    t = eco.evolver.trial(firm, market, proposed, analysts=analysts)
    values = {k: t[k] for k in ("fitted_before", "fitted_after", "holdout_before",
                                "holdout_after", "holdout_bars")}
    values["tested_at"] = utcnow_iso()

    if not t["enough_holdout"]:
        verdict, status = (f"only {t['holdout_bars']} held-out bar(s): not enough to judge", "refused")
    elif D(t["holdout_after"]) <= D(t["holdout_before"]):
        verdict, status = (f"lost the held-out bars ({t['holdout_before']} -> "
                           f"{t['holdout_after']})", "refused")
    else:
        verdict, status = (f"won the held-out bars ({t['holdout_before']} -> "
                           f"{t['holdout_after']}); filed to the council", "filed")
    values.update({"verdict": verdict, "status": status})
    if status == "refused" and t["enough_holdout"]:
        _try_again(eco.db, row, firm, verdict, t)
    if status == "filed":
        approval = eco.gate.request(
            ApprovalAction.ADOPT_GENOME.value,
            f"Adopt {row['proposed_by']}'s change to {firm.firm_key}: "
            f"{', '.join(f'{k}={v}' for k, v in changes.items())} — {verdict}",
            details={"firm": firm.firm_key, "proposal_id": row["id"], "changes": changes},
            notify=False, dedupe_key=f"proposal:{row['id']}")
        values["approval_id"] = approval.id
    eco.db.update("ai_proposals", row["id"], values)
    return f"proposal #{row['id']} for {firm.firm_key}: {verdict}"


#: Rounds a firm may go back to its advisers in one day after a refusal. Two
#: machines can otherwise argue in a circle all day for free.
RETRIES_PER_DAY = 3


def _try_again(db, row, firm, verdict: str, trial: dict) -> Optional[int]:
    """The firm tells the adviser its idea lost, with the numbers, and asks again."""
    from . import ask

    today = utcnow_iso()[:10]
    retries = db.query_one(
        "SELECT COUNT(*) AS n FROM ai_questions WHERE firm_key = ? AND topic = 'proposal' "
        "AND dedupe_key LIKE 'retry:%' AND asked_at LIKE ?", (firm.firm_key, f"{today}%"))
    if int((retries or {}).get("n") or 0) >= RETRIES_PER_DAY:
        return None
    return ask.ask(
        db, firm.firm_key, "proposal",
        f"The village tested your change {row['changes']} and refused it: {verdict}. "
        "Given that result, is there a different gene change worth testing? Reply with "
        'ONLY a JSON object like {"changes": {"gene_name": value}, "why": "one sentence"}, '
        f'at most {MAX_CHANGES} genes, each inside its min and max, or {{"changes": {{}}, '
        '"why": "..."} if nothing else is worth trying.',
        {"refused_change": json.loads(row["changes"]), "result": {
            k: str(trial.get(k)) for k in ("fitted_before", "fitted_after",
                                           "holdout_before", "holdout_after", "holdout_bars")},
         "genes": genes_for(firm), "earlier_reasoning": row.get("why")},
        f"retry:{row['id']}")


def adopt(eco, details: dict, approved_by: str) -> str:
    """Write the granted genes, keeping everything that is not a gene."""
    from .brain.evolver import GENES

    row = eco.db.query_one("SELECT * FROM ai_proposals WHERE id = ?", (details["proposal_id"],))
    firm = eco.store.get_firm(details["firm"])
    if row is None or firm is None:
        raise ValueError("the proposal or its firm no longer exists")
    changes = {k: v for k, v in json.loads(row["changes"]).items() if k in GENES}
    # **Only adopt against the genome it was tested against.** Two proposals for
    # one firm were each tested against the same incumbent, both won, both were
    # granted — and the second adoption overwrote the first, leaving the firm
    # on the weaker change (live, 2026-09-27: fast_window 9 won the held-out
    # bars by 0.50, fast_window 6 by 0.03, and 6 is what stuck). If the firm's
    # genes have moved since the test, the win no longer means anything: the
    # proposal goes back to be tested against what the firm is now.
    tested_on = json.loads(row.get("genome_before") or "{}")
    current = firm.genome or {}
    moved = [g for g in GENES if str(tested_on.get(g)) != str(current.get(g))]
    if moved:
        eco.db.update("ai_proposals", row["id"], {
            "status": "awaiting_test", "tested_at": None,
            "verdict": f"not adopted: {', '.join(moved)} changed since it was tested; re-testing",
            "genome_before": json.dumps(current)})
        return (f"proposal #{row['id']} for {firm.firm_key} not adopted: the firm changed "
                "since it was tested, so it will be tested again")
    genome = {**current, **changes}
    eco.store.update_firm_fields(firm.id, genome=json.dumps(genome, sort_keys=True))
    eco.store.record_event(
        "evolution",
        f"{firm.firm_key}: adopted {row['proposed_by']}'s proposal #{row['id']} "
        f"({', '.join(f'{k}={v}' for k, v in changes.items())}), approved by {approved_by}",
        firm_id=firm.id, payload={"changes": changes, "proposal_id": row["id"]})
    eco.db.update("ai_proposals", row["id"], {"status": "adopted"})
    return f"{firm.firm_key} adopted proposal #{row['id']}"


def recent(db, limit: int = 12) -> list:
    try:
        return db.query("SELECT * FROM ai_proposals ORDER BY id DESC LIMIT ?", (limit,))
    except Exception:  # noqa: BLE001
        return []


__all__ = ["FOLLOW_UP", "MAX_CHANGES", "adopt", "follow_up", "genes_for", "parse",
           "record", "recent", "test_one"]
