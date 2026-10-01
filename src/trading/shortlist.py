"""The weekly shortlist: the research finds the outside minds agree are worth testing.

Once a day the village asks the outside minds (Claude Code on the operator's
PC, duck.ai) which of the scouts' finds are worth testing (`ask._research_review`).
Their answers were prose, read by nobody, and a "worth testing" in the middle
of a paragraph went nowhere: nothing could count how many minds said it, or
notice that one of them called the same repository a malware lure.

So the review now ends every answer with one machine-readable line,

    VERDICTS: [{"name": ..., "verdict": "test", "test": ..., "proof": ...},
               {"name": ..., "verdict": "danger", "why": ...}]

listing only the finds worth testing or worth avoiding, and this module reads
those lines back. **A find makes the shortlist when at least two different
minds said to test it and none called it dangerous.** One mind's enthusiasm is
an opinion; two agreeing, with nobody flagging a scam, is worth a person's time.

**What the shortlist is not.** It is not a test and it starts nothing. Nothing
from a find is ever downloaded or run (a trending trading-bot repository is a
known malware lure), and a shortlisted idea reaches a firm only one way: the
operator says yes, somebody writes the idea as the village's own strategy, and
the strategy court tries it like any other recruit (`recruit.py`). This module
only decides what is worth asking the operator about, once a week.

It is kept where the operator already looks: the `research_shortlist` intel
source (Mission Control's "Outside the village"), `/api/research/shortlist`, the
notifier, and `trade shortlist`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..db.connection import to_iso

#: The line every research answer is asked to end with.
MARKER = "VERDICTS:"
#: A find needs this many different minds saying "test", and none "danger".
MIN_MINDS = 2
#: How far back a week's shortlist reads.
WINDOW = timedelta(days=7)
#: Where the week's shortlist is kept for Mission Control (see intel.py).
INTEL_SOURCE = "research_shortlist"

#: Appended to the daily research question in `ask._research_review`.
ASK = (
    f" End your answer with one line that starts with {MARKER} followed by a JSON "
    "list of only the finds worth testing or that look dangerous, each named "
    "exactly as given, like: "
    f'{MARKER} [{{"name": "...", "verdict": "test", "test": "what exactly to test", '
    '"proof": "how we would know it worked"}, {"name": "...", "verdict": "danger", '
    '"why": "..."}]. Leave out the plain skips, and write '
    f"{MARKER} [] if there are none. That line does not count toward the word limit."
)

_FENCE = re.compile(r"```(?:json)?", re.I)


def verdicts(text: str) -> list:
    """The verdicts in one answer: the last `VERDICTS:` line's list, cleaned.

    Forgiving about what a chat model does to JSON (a code fence, a trailing
    sentence) and strict about what it keeps: a name, and a verdict of `test`
    or `danger`. Anything else, or no marker at all, is no verdicts.
    """
    text = _FENCE.sub("", str(text or ""))
    at = text.rfind(MARKER)
    if at < 0:
        return []
    rest = text[at + len(MARKER):]
    start, end = rest.find("["), rest.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        raw = json.loads(rest[start:end + 1])
    except ValueError:
        return []
    out = []
    for v in raw if isinstance(raw, list) else []:
        if not isinstance(v, dict):
            continue
        name = str(v.get("name") or "").strip()
        verdict = str(v.get("verdict") or "").strip().lower()
        if not name or verdict not in ("test", "danger"):
            continue
        out.append({"name": name, "verdict": verdict,
                    **{k: str(v.get(k) or "").strip()[:400] for k in ("test", "proof", "why")}})
    return out


def _key(name: str) -> str:
    return " ".join(str(name).lower().split())


def shortlist(db, now: Optional[datetime] = None, window: timedelta = WINDOW,
              min_minds: int = MIN_MINDS) -> list:
    """This window's agreed finds, most minds first.

    Each entry is the find as the review saw it (source, url, about) with
    every mind's `test` and `proof`, keyed by the mind that said it. A find
    reviewed on two days counts each mind once.
    """
    now = now or datetime.now(timezone.utc)
    try:
        questions = db.query(
            "SELECT id, context FROM ai_questions WHERE topic = 'research' AND asked_at >= ? "
            "ORDER BY id", (to_iso(now - window),)) or []
    except Exception:  # noqa: BLE001 - an unmigrated ledger has no reviews
        return []
    found: dict = {}
    for q in questions:
        try:
            finds = (json.loads(q.get("context") or "{}") or {}).get("finds") or []
        except (TypeError, ValueError):
            finds = []
        by_name = {_key(f.get("name") or ""): f for f in finds if isinstance(f, dict)}
        for a in db.query("SELECT answered_by, answer FROM ai_answers WHERE question_id = ?",
                          (q["id"],)) or []:
            mind = str(a.get("answered_by") or "")
            for v in verdicts(a.get("answer")):
                key = _key(v["name"])
                entry = found.setdefault(key, {"name": v["name"], "find": {}, "test": {},
                                               "danger": {}})
                entry["find"] = entry["find"] or by_name.get(key) or {}
                if v["verdict"] == "test":
                    entry["test"][mind] = {"test": v["test"], "proof": v["proof"]}
                else:
                    entry["danger"][mind] = v["why"]
    agreed = [e for e in found.values() if len(e["test"]) >= min_minds and not e["danger"]]
    for e in agreed:
        f = e["find"]
        e.update(source=f.get("source") or "", url=f.get("url") or "",
                 about=str(f.get("about") or f.get("abstract") or "")[:240])
    return sorted(agreed, key=lambda e: (-len(e["test"]), e["name"].lower()))


def lines(entries: list) -> list:
    """The shortlist as plain sentences, for the notifier and the CLI."""
    if not entries:
        return ["No find had two outside minds agreeing it is worth testing this week."]
    out = []
    for i, e in enumerate(entries, 1):
        tests = "; ".join(f"{mind}: {t['test']}" for mind, t in e["test"].items() if t["test"])
        out.append(f"{i}. {e['name']} ({e['source'] or 'unknown source'}) {e['url']}".rstrip())
        if tests:
            out.append(f"   test: {tests}")
    out.append("Nothing here runs by itself. Say yes to one and it is written as a village "
               "strategy and tried by the strategy court.")
    return out


def weekly(eco, now: Optional[datetime] = None) -> list:
    """On Mondays (UTC), once: keep the week's shortlist where it is seen, and send it.

    Monday rather than "the first bar of a new week" so a deploy on a Thursday
    does not send a shortlist read from three days of reviews. A worker that is
    down for all of a Monday skips that week; the next Monday reads seven days
    again, so nothing agreed is lost for long.
    """
    now = now or datetime.now(timezone.utc)
    if now.weekday() != 0:
        return []
    week = now.strftime("%G-W%V")
    try:
        if eco.db.query_one("SELECT id FROM intel WHERE source = ? AND item_key = ?",
                            (INTEL_SOURCE, f"{week}:sent")):
            return []
    except Exception:  # noqa: BLE001 - an unmigrated ledger keeps nothing
        return []
    from . import intel

    entries = shortlist(eco.db, now)
    for e in entries:
        intel.upsert(eco.db, INTEL_SOURCE, f"{week}:{_key(e['name'])}"[:255],
                     title=f"{e['name']}: {next(iter(e['test'].values()))['test']}"[:500],
                     url=e["url"], score=len(e["test"]),
                     detail={"week": week, "source": e["source"], "minds": e["test"]})
    intel.upsert(eco.db, INTEL_SOURCE, f"{week}:sent",
                 title=f"week {week}: {len(entries)} find(s) on the shortlist", score=len(entries),
                 detail={"week": week, "names": [e["name"] for e in entries]})
    try:
        eco.notifier.send(f"Worth testing, week {week}", "\n".join(lines(entries)))
    except Exception:  # noqa: BLE001 - a notification is never a precondition
        pass
    return [f"research shortlist for {week}: {len(entries)} find(s) both minds would test"]


__all__ = ["ASK", "INTEL_SOURCE", "MARKER", "MIN_MINDS", "lines", "shortlist", "verdicts",
           "weekly"]
