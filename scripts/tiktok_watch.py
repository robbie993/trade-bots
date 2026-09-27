"""Watch TikTok for explicit calls: captions and what is actually said.

    python scripts/tiktok_watch.py --dry-run
    python scripts/tiktok_watch.py --to-railway      # the scheduled run

Shares the village browser with the Instagram and X readers (signed in once by
the operator; no password is ever handled here) and follows the handles in
`config/tiktok_sources.yaml` a few a run, each opened first. It likes, comments
and messages nothing.

TikTok is speech more than text, so for each new video it keeps the caption and
also transcribes the audio with the same local Whisper model the YouTube watcher
uses (`video_watch._whisper`), downloading the audio only, deleting it after,
and skipping anything longer than `max_video_s`. Caption plus transcript go
through the usual rule, `crowd.extract_calls`, one account one voice. Videos go
to `intel` (source `tiktok`), the aggregate to the `tiktok_calls` snapshot.

A verification page stops the run with a message; nothing tries to get past it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml  # noqa: E402

from scripts import video_watch  # noqa: E402
from scripts.insta_watch import browser  # noqa: E402
from scripts.social_watch import calls_in, readings  # noqa: E402
from src.trading import intel  # noqa: E402
from src.trading.topics import tag  # noqa: E402

CONFIG = REPO / "config" / "tiktok_sources.yaml"
STATE = REPO / "data" / "tiktok_state.json"
SOURCE = "tiktok"
SNAPSHOT = "tiktok_calls"


class Challenged(RuntimeError):
    """TikTok wants a human or a login. The run stops."""


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"followed": [], "missing": [], "cursor": 0}


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def _check(page) -> None:
    body = page.inner_text("body")[:3000].lower()
    if "verify to continue" in body or "drag the slider" in body or "/login" in page.url:
        raise Challenged(f"TikTok wants a check or a login ({page.url})")


def open_profile(page, handle: str) -> bool:
    page.goto(f"https://www.tiktok.com/@{handle}", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    _check(page)
    body = page.inner_text("body")[:3000]
    return "Couldn't find this account" not in body and "couldn't find this account" not in body


def follow(page, handle: str) -> str:
    if not open_profile(page, handle):
        return "missing"
    button = page.locator('[data-e2e="follow-button"]')
    if button.count() == 0 or button.first.inner_text().strip().lower() != "follow":
        return "already"
    button.first.click()
    page.wait_for_timeout(2500)
    _check(page)
    return "followed"


def video_ids(page, handle: str, limit: int) -> list:
    hrefs = page.eval_on_selector_all('a[href*="/video/"]', "els => els.map(e => e.href)")
    out = []
    for h in hrefs:
        m = re.search(r"/@([^/]+)/video/(\d+)", h or "")
        if m and m.group(1).lower() == handle.lower() and m.group(2) not in out:
            out.append(m.group(2))
        if len(out) >= limit:
            break
    return out


def heard(handle: str, vid: str, max_s: int) -> dict:
    """Caption, time and transcript for one video, through yt-dlp and Whisper."""
    import yt_dlp

    url = f"https://www.tiktok.com/@{handle}/video/{vid}"
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
        info = y.extract_info(url, download=False)
    caption = info.get("description") or info.get("title") or ""
    ts = info.get("timestamp")
    text = ""
    if (info.get("duration") or 0) <= max_s:
        with tempfile.TemporaryDirectory() as tmp:
            opts = {"quiet": True, "no_warnings": True, "noprogress": True,
                    "format": "bestaudio/best", "outtmpl": str(Path(tmp) / "a.%(ext)s")}
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([url])
            audio = next(Path(tmp).glob("a.*"))
            if video_watch._whisper is None:
                from faster_whisper import WhisperModel

                video_watch._whisper = WhisperModel(str(video_watch.WHISPER_MODEL),
                                                    device="cpu", compute_type="int8")
            segments, _ = video_watch._whisper.transcribe(str(audio), beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments)
    return {"id": vid, "url": url, "author": handle, "title": caption, "text": text,
            "published": (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                          if ts else datetime.now(timezone.utc).isoformat())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-railway", action="store_true")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-follow", action="store_true")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    handles = [a["handle"] for a in cfg.get("accounts") or []]
    symbols = video_watch.universe()
    state = _state()
    db = None
    if not args.dry_run and (args.database_url or args.to_railway):
        from scripts.fleet_sync import railway_database_url
        from src.db.connection import Database

        db = Database.from_url(args.database_url or railway_database_url())
        db.init_schema()
    seen = set()
    if db is not None:
        seen = {r["item_key"] for r in db.query(
            "SELECT item_key FROM intel WHERE source = ?", (SOURCE,))}

    fresh, notes, todo_videos = [], [], []
    with sync_playwright() as p:
        ctx = browser(p).contexts[0]
        page = ctx.new_page()
        try:
            if not args.no_follow and not args.dry_run:
                todo = [h for h in handles
                        if h not in state["followed"] and h not in state["missing"]]
                for h in todo[: int(cfg.get("follows_per_run", 3))]:
                    result = follow(page, h)
                    (state["missing"] if result == "missing" else state["followed"]).append(h)
                    notes.append(f"@{h}: {result}")
                    _save(state)
                    time.sleep(int(cfg.get("follow_pause_s", 60)))
            pool = [h for h in handles if h not in state["missing"]]
            start = state.get("cursor", 0) % max(1, len(pool))
            batch = (pool[start:] + pool[:start])[: int(cfg.get("profiles_per_run", 4))]
            state["cursor"] = start + len(batch)
            for h in batch:
                if open_profile(page, h):
                    for vid in video_ids(page, h, int(cfg.get("videos_per_profile", 3))):
                        if vid not in seen:
                            todo_videos.append((h, vid))
        except Challenged as exc:
            notes.append(f"STOPPED: {exc}")
        finally:
            _save(state)
            page.close()

    for h, vid in todo_videos:
        try:
            v = heard(h, vid, int(cfg.get("max_video_s", 600)))
        except Exception as exc:  # noqa: BLE001 - one video is not the run
            notes.append(f"{h}/{vid}: {str(exc)[:100]}")
            continue
        v["calls"] = calls_in(v, symbols)
        fresh.append(v)
        if db is not None:
            intel.upsert(db, SOURCE, vid, title=f"@{h}: {v['title'][:150]}", url=v["url"],
                         symbols=sorted({c["symbol"] for c in v["calls"]}),
                         score=len(v["calls"]),
                         detail={"author": h, "published": v["published"], "calls": v["calls"],
                                 "words": len(v["text"].split()),
                                 "topics": tag(f"{v['title']} {v['text']}")})

    pool = list(fresh)
    if db is not None:
        ids = {v["id"] for v in fresh}
        for row in intel.recent(db, SOURCE, limit=2000):
            if row["item_key"] not in ids:
                d = row["detail"]
                pool.append({"author": d.get("author"), "published": d.get("published"),
                             "calls": d.get("calls") or []})
    out = readings(pool, float(cfg.get("max_age_hours", 24)))
    for r in out:
        r["note"] = r["note"].replace("Reddit:", "TikTok:", 1)
    if db is not None:
        from src.trading import fleet

        fleet.record(db, SNAPSHOT, {"readings": out, "posts_read": len(fresh)},
                     service="tiktok", remote_path="config/tiktok_sources.yaml")
    for n in notes:
        print(" ", n)
    print(f"\n{len(fresh)} new video(s), {sum(1 for v in fresh if v['calls'])} with calls, "
          f"{len(out)} reading(s)" + (" (dry run)" if args.dry_run else ""))
    for v in fresh[:12]:
        called = ", ".join(f"{c['symbol']}{'+' if c['direction'] > 0 else '-'}"
                           for c in v["calls"]) or "no calls"
        print(f"  @{v['author']:<18} {v['title'][:60]!r:<64} {called}")
    return 1 if any(n.startswith("STOPPED") for n in notes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
