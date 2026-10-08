"""Things the operator sends the village by hand — a link, a video, a file.

The social readers go looking on their own. This is the other way round: a
person on a phone sees a reel, a clip or a document and says "look at this".
See migration 032.

**Who reads what.**

* A **text file** (.txt, .md, .csv, .json, .html…) is read by the web service
  the moment it arrives. There is nothing to wait for.
* A **link** to Instagram, TikTok, X, YouTube or anywhere else waits for the
  operator's PC, where the village browser is signed in to those sites
  (`scripts/inbox_watch.py`). It opens the post, takes the caption, fetches the
  audio and transcribes it with the same Whisper model the reels go through.
* A **video or audio file** is kept in the ledger until the PC has transcribed
  it; a **picture or PDF** until Claude on the PC has looked at it. Then the
  bytes are dropped: the ledger keeps what was said, not the file.

**What it can do.** Everything read is kept with the explicit calls found in it,
by the social readers' own rule (`scripts/social_watch.calls_in`: a ticker with
a directional word beside it, never a bare mention). Those calls go to the idea
lab as one more publisher, ``sent_by_you``: each one bought or sold *on paper*,
held for an hour, a day and a week, and scored against SPY after costs. They
are not readings on the signal board, so no firm hears them, no debate weighs
them, and nothing sent here can place an order. If a video's tip is any good,
the lab's scoreboard is where that will show.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..db.connection import to_datetime, utcnow, utcnow_iso

#: The idea lab's name for everything sent by hand.
PUBLISHER = "sent_by_you"

#: Largest file kept. The bytes sit in Postgres until the PC has read them; a
#: phone video of a few minutes is well under this, a feature film is not.
MAX_BYTES = 80 * 1024 * 1024

#: What is kept of a transcript or a document: enough to read back what it said.
TEXT_CHARS = 20000

#: A link or file the PC failed on is tried this many times, then given up.
MAX_ATTEMPTS = 3

#: A row claimed by the PC and not finished in this long is offered again — the
#: PC dozed off or the run was killed partway.
STALE_CLAIM = timedelta(minutes=45)

#: Calls from something read more than this long ago stop being offered to the
#: lab. A stock tip sent on Friday night is entered when Monday's market opens.
LAB_WINDOW = timedelta(days=4)

#: Low on purpose: the lab only uses the sign of a call, and this says
#: "one person's tip".
CALL_CONFIDENCE = 10

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".xml",
            ".log", ".yaml", ".yml", ".srt", ".vtt", ".py", ".rtf"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp"}

PLATFORMS = (
    ("instagram", ("instagram.com", "instagr.am")),
    ("tiktok", ("tiktok.com",)),
    ("x", ("x.com", "twitter.com")),
    ("youtube", ("youtube.com", "youtu.be")),
    ("reddit", ("reddit.com", "redd.it")),
)

_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")


class Refused(ValueError):
    """Not something the inbox can take. The message says why, for the page."""


# =========================================================================
# what was sent
# =========================================================================
def platform_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for name, hosts in PLATFORMS:
        if any(host == h or host.endswith("." + h) for h in hosts):
            return name
    return "web"


def kind_of(filename: str, content_type: str = "") -> str:
    """text, video, audio, image, pdf or document."""
    ext = Path(filename or "").suffix.lower()
    ctype = (content_type or "").lower()
    if ext in TEXT_EXT or (ctype.startswith("text/") and ext not in VIDEO_EXT):
        return "text"
    if ext in VIDEO_EXT or ctype.startswith("video/"):
        return "video"
    if ext in AUDIO_EXT or ctype.startswith("audio/"):
        return "audio"
    if ext in IMAGE_EXT or ctype.startswith("image/"):
        return "image"
    if ext == ".pdf" or ctype == "application/pdf":
        return "pdf"
    return "document"


def submit_link(db, url: str, note: str = "") -> int:
    url = (url or "").strip()
    # Pasted from a share sheet, a link often arrives with words around it.
    found = re.search(r"https?://\S+", url)
    if not found:
        raise Refused("that does not look like a link (it should start with https://)")
    url = found.group(0).rstrip(").,;'\"")
    return db.insert("inbox", {
        "kind": "link", "platform": platform_of(url), "url": url[:2000],
        "note": (note or "")[:2000], "status": "queued",
        "submitted_at": utcnow_iso(),
    })


def submit_file(db, filename: str, content_type: str, data: bytes, note: str = "",
                symbols=None) -> int:
    """Keep a file. A text file is read now; anything else waits for the PC."""
    name = Path(filename or "sent").name[:200]
    if not data:
        raise Refused(f"{name} is empty")
    if len(data) > MAX_BYTES:
        raise Refused(f"{name} is {len(data) / 1e6:,.0f} MB; the most the village keeps "
                      f"is {MAX_BYTES / 1e6:,.0f} MB")
    kind = kind_of(name, content_type)
    row = {"kind": kind, "platform": "upload", "filename": name,
           "content_type": (content_type or "")[:100], "size_bytes": len(data),
           "note": (note or "")[:2000], "submitted_at": utcnow_iso()}
    if kind == "text":
        text = data.decode("utf-8", errors="replace")
        if name.lower().endswith((".html", ".htm")):
            text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
        text = " ".join(text.split())
        row.update(status="read", text=text[:TEXT_CHARS], title=name,
                   heard_by="read on the village server", read_at=utcnow_iso(),
                   calls=json.dumps(calls_for(f"{note}. {text}", symbols)))
    else:
        row.update(status="queued", body=data)
    return db.insert("inbox", row)


# =========================================================================
# reading it
# =========================================================================
def calls_for(text: str, symbols=None) -> list:
    """Explicit calls in what was sent, by the social readers' rule.

    The village's universe, plus any $CASHTAG the text names — a tip about a
    small cap is still a tip, and the idea lab can price names the firms do
    not trade.
    """
    if not text:
        return []
    from scripts.social_watch import calls_in
    from scripts.video_watch import universe

    pool = set(symbols if symbols is not None else universe())
    pool |= {m.upper() for m in _CASHTAG.findall(text)}
    found = calls_in({"title": "", "text": text}, sorted(pool))
    seen, out = set(), []
    for c in found:                        # one call per symbol: the first said
        if c["symbol"] in seen:
            continue
        seen.add(c["symbol"])
        out.append({"symbol": c["symbol"], "direction": int(c["direction"]),
                    "phrase": str(c.get("phrase") or "")[:160]})
    return out


def claim(db, kinds=None) -> Optional[dict]:
    """The oldest thing waiting for the PC, marked as being read. None if none.

    A claim older than `STALE_CLAIM` is taken back: the PC that held it is gone.
    """
    cutoff = utcnow() - STALE_CLAIM
    for row in db.query("SELECT id, claimed_at FROM inbox WHERE status = 'reading'"):
        at = to_datetime(row.get("claimed_at"))
        if at is None or at < cutoff:
            db.update("inbox", row["id"], {"status": "queued"})
    rows = db.query(
        "SELECT id, kind FROM inbox WHERE status = 'queued' ORDER BY id LIMIT 50")
    for row in rows:
        if kinds and row["kind"] not in kinds:
            continue
        db.execute(
            "UPDATE inbox SET status = 'reading', claimed_at = ?, attempts = attempts + 1 "
            "WHERE id = ? AND status = 'queued'", (utcnow_iso(), row["id"]))
        got = db.query_one("SELECT * FROM inbox WHERE id = ?", (row["id"],))
        if got and got["status"] == "reading":
            body = got.get("body")
            if body is not None and not isinstance(body, bytes):
                got["body"] = bytes(body)        # psycopg hands back a memoryview
            return got
    return None


def finish(db, row_id: int, text: str = "", title: str = "", summary: str = "",
           heard_by: str = "", symbols=None, note: str = "") -> list:
    """Record what was read and drop the file. Returns the calls found.

    Any IDEA lines in the summary (see scripts/inbox_watch.summarise) are taken
    out of it and handed to the village (`learn`).
    """
    text = " ".join((text or "").split())
    calls = calls_for(f"{note}. {title}. {text}", symbols)
    db.update("inbox", row_id, {
        "status": "read", "text": text[:TEXT_CHARS], "title": (title or "")[:500],
        "heard_by": (heard_by or "")[:200],
        "calls": json.dumps(calls), "read_at": utcnow_iso(), "body": None, "error": "",
    })
    learn(db, row_id, summary)
    return calls


def learn(db, row_id: int, summary: str) -> list:
    """Keep a send's summary, and give each idea in it to the village. Returns the ideas."""
    from . import ideas

    clean, found = ideas.parse(summary or "")
    for idea in found:
        try:
            ideas.give(db, idea["idea"], idea["to"], origin=f"something you sent (#{row_id})")
        except Exception:  # noqa: BLE001 - the summary is kept either way
            pass
    if found:
        clean += "\n\nIdeas passed to the village:\n" + "\n".join(
            f"\u2022 {i['idea']} ({'the firms' if i['to'] in ('firms', 'all') else i['to']})"
            for i in found)
    db.update("inbox", row_id, {"summary": clean[:4000]})
    return found


def fail(db, row_id: int, error: str, retry: bool = True) -> None:
    """Note a failure. Tried again later, until `MAX_ATTEMPTS`; then the file goes."""
    row = db.query_one("SELECT attempts FROM inbox WHERE id = ?", (row_id,)) or {}
    final = not retry or int(row.get("attempts") or 0) >= MAX_ATTEMPTS
    values = {"status": "failed" if final else "queued", "error": str(error)[:500]}
    if final:
        values["body"] = None
    db.update("inbox", row_id, values)


def retry(db, row_id: int) -> None:
    """Try a failed link again. A failed file's bytes are gone; it cannot be."""
    row = db.query_one("SELECT kind FROM inbox WHERE id = ?", (row_id,))
    if row and row["kind"] == "link":
        db.update("inbox", row_id, {"status": "queued", "attempts": 0, "error": ""})


def recent(db, limit: int = 30) -> list:
    try:
        rows = db.query(
            "SELECT id, kind, platform, url, filename, content_type, size_bytes, note, "
            "status, attempts, title, text, summary, calls, heard_by, error, "
            "submitted_at, read_at FROM inbox ORDER BY id DESC LIMIT ?", (limit,))
    except Exception:  # noqa: BLE001 - an unmigrated ledger has been sent nothing
        return []
    for r in rows:
        try:
            r["calls"] = json.loads(r.get("calls") or "[]")
        except (TypeError, ValueError):
            r["calls"] = []
    return rows


def waiting(db) -> int:
    try:
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM inbox WHERE status IN ('queued', 'reading')")
    except Exception:  # noqa: BLE001
        return 0
    return int((row or {}).get("n") or 0)


# =========================================================================
# the idea lab
# =========================================================================
def lab_calls(db, now=None) -> list:
    """Calls from recent sends the lab has not entered yet, as lab `Call`s.

    Offered every bar until the lab opens them, because a stock called at
    night waits for the market to open — the lab's own rule. Once a call has
    an idea open, it is not offered again.
    """
    from decimal import Decimal

    from .sandbox.ideas import Call

    now = to_datetime(now) or utcnow()
    out = []
    for row in _pending(db, now):
        for c in row["calls"]:
            if c.get("in_lab"):
                continue
            out.append(Call(PUBLISHER, str(c["symbol"]).upper(),
                            Decimal(1 if int(c["direction"]) > 0 else -1),
                            Decimal(CALL_CONFIDENCE),
                            f"#{row['id']}: {c.get('phrase', '')}"[:200]))
    return out


def mark_entered(db, now=None) -> int:
    """After the lab's bar: note which offered calls it opened. Returns how many."""
    now = to_datetime(now) or utcnow()
    pending = _pending(db, now)
    if not pending:
        return 0
    try:
        ideas = db.query(
            "SELECT symbol, opened_bar FROM sandbox_ideas WHERE publisher = ? "
            "ORDER BY id DESC LIMIT 500", (PUBLISHER,))
    except Exception:  # noqa: BLE001 - no lab table yet
        return 0
    marked = 0
    for row in pending:
        read_at = to_datetime(row.get("read_at"))
        changed = False
        for c in row["calls"]:
            if c.get("in_lab"):
                continue
            for idea in ideas:
                opened = to_datetime(idea.get("opened_bar"))
                if (str(idea["symbol"]).upper() == str(c["symbol"]).upper()
                        and opened is not None and read_at is not None
                        and opened >= read_at - timedelta(hours=1)):
                    c["in_lab"] = str(idea["opened_bar"])
                    changed = True
                    marked += 1
                    break
        if changed:
            db.update("inbox", row["id"], {"calls": json.dumps(row["calls"])})
    return marked


def _pending(db, now) -> list:
    try:
        rows = db.query(
            "SELECT id, calls, read_at FROM inbox WHERE status = 'read' "
            "AND calls IS NOT NULL AND calls <> '[]' ORDER BY id")
    except Exception:  # noqa: BLE001 - an unmigrated ledger
        return []
    out = []
    for r in rows:
        read_at = to_datetime(r.get("read_at"))
        if read_at is None or now - read_at > LAB_WINDOW:
            continue
        try:
            r["calls"] = json.loads(r["calls"])
        except (TypeError, ValueError):
            continue
        if any(not c.get("in_lab") for c in r["calls"]):
            out.append(r)
    return out


__all__ = ["PUBLISHER", "Refused", "calls_for", "claim", "fail", "finish", "kind_of", "learn",
           "lab_calls", "mark_entered", "platform_of", "recent", "retry",
           "submit_file", "submit_link", "waiting"]
