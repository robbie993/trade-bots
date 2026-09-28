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

It also scrolls the For You page, one video at a time (`for_you_videos`; 0 turns
it off), so what TikTok recommends to an account that follows markets people is
read too, whoever posted it. How long a video is watched is what For You learns
from, so the scroll steers it: a video whose caption is about markets is watched
to the end (up to `for_you_watch_max_s`), anything else is left after a second
or two. Those videos are transcribed like the rest, and one is kept only if its
caption or what is said is about markets (`topics.on_topic`) or makes a call;
the rest are remembered as skipped. Kept ones are marked `for_you` in `intel`,
and every kept video carries the start of what was said (`said`), which the
daily research review reads.

A verification page stops the run with a message; nothing tries to get past it.
"""
from __future__ import annotations

import argparse
import json
import random
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
from src.trading.topics import on_topic, tag  # noqa: E402

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


def feed_videos(hrefs) -> list:
    """(author, video id) for each distinct video link, in page order."""
    out, ids = [], set()
    for h in hrefs:
        m = re.search(r"/@([^/?#]+)/video/(\d+)", h or "")
        if m and m.group(2) not in ids:
            ids.add(m.group(2))
            out.append((m.group(1), m.group(2)))
    return out


def video_ids(page, handle: str, limit: int) -> list:
    hrefs = page.eval_on_selector_all('a[href*="/video/"]', "els => els.map(e => e.href)")
    return [vid for author, vid in feed_videos(hrefs)
            if author.lower() == handle.lower()][:limit]


# Each For You video is an <article data-scroll-index=N> that only fills in near
# the screen. It carries no link to itself: the author is the avatar's /@handle
# link and the video id is in the player's element id, xgwrapper-0-<id>. The
# caption is its video-desc when that is there, else the article's own text.
FOR_YOU_ITEM = """a => {
  const who = a.querySelector('a[href^="/@"]');
  const player = [...a.querySelectorAll('[id^="xgwrapper-"]')].map(e => e.id)[0] || '';
  const id = (player.match(/(\\d{15,})$/) || [])[1];
  const desc = a.querySelector('[data-e2e="video-desc"]');
  const video = a.querySelector('video');
  return {
    link: who && id ? 'https://www.tiktok.com' + who.getAttribute('href') + '/video/' + id : '',
    caption: (desc || a).innerText || '',
    duration: video && isFinite(video.duration) ? video.duration : 0,
  };
}"""

#: What is said is kept up to this many characters: enough to know what a video
#: is about, not a transcript archive.
SAID_CHARS = 1000


def watch_for(caption: str, duration: float, cfg: dict) -> float:
    """Seconds to stay on one For You video.

    Watch time is what the For You page learns from, so this is what steers it
    toward markets: a video about them is watched to the end (at most
    `for_you_watch_max_s`), anything else is left after a second or two. A
    caption that could not be read gets the ordinary pause, `for_you_dwell_s`."""
    low, high = cfg.get("for_you_dwell_s") or (4, 9)
    if not (caption or "").strip():
        return random.uniform(float(low), float(high))
    if on_topic(caption):
        whole = duration if duration and duration > 0 else float(high) * 3
        return max(float(low), min(float(whole), float(cfg.get("for_you_watch_max_s", 90))))
    return random.uniform(1.0, 2.0)


def for_you(page, cfg: dict) -> tuple:
    """(author, video id) from the For You page, nothing transcribed yet, and
    how many of the videos scrolled past had captions on topic.

    The page plays one video at a time. Each is brought on screen in turn, read,
    and watched for as long as `watch_for` says before moving on. It keeps
    scrolling (at most `for_you_scan` videos) until `for_you_videos` are found
    whose caption is on topic or unreadable; a caption plainly about something
    else is not worth downloading and transcribing. The on-topic share is the
    number that says whether the steering is working, run over run."""
    limit = int(cfg.get("for_you_videos", 8))
    stats = {"scrolled": 0, "on_topic": 0}
    if not limit:
        return [], stats
    page.goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    _check(page)
    out = []
    for i in range(int(cfg.get("for_you_scan", 30))):
        item = page.locator(f'article[data-scroll-index="{i}"]')
        for _ in range(3):                 # the next batch loads once the last one is on screen
            if item.count():
                break
            last = page.locator("article[data-scroll-index]").last
            if last.count():
                last.scroll_into_view_if_needed()
            page.wait_for_timeout(5000)
        if item.count() == 0:
            break
        item.first.scroll_into_view_if_needed()
        page.wait_for_timeout(2000)
        try:
            shown = item.first.evaluate(FOR_YOU_ITEM, timeout=5000) or {}
        except Exception:  # noqa: BLE001 - a video that went away costs a step
            shown = {}
        caption = shown.get("caption", "")
        pairs = feed_videos([shown.get("link")])
        if pairs:
            stats["scrolled"] += 1
            stats["on_topic"] += on_topic(caption)
        if pairs and (on_topic(caption) or not caption.strip()) \
                and pairs[0][1] not in [v for _, v in out]:
            out.append(pairs[0])
        time.sleep(watch_for(caption, shown.get("duration", 0), cfg))
        _check(page)
        if len(out) >= limit:
            break
    return out[:limit], stats


def search_seed(page, cfg: dict, state: dict) -> list:
    """One market search a run, rotating through `search_terms`.

    What an account searches for is the other thing TikTok builds its For You
    page from, after watch time. The top `search_videos` results are heard too."""
    terms = cfg.get("search_terms") or []
    n = int(cfg.get("search_videos", 3))
    if not terms or not n:
        return []
    term = terms[state.get("search_cursor", 0) % len(terms)]
    state["search_cursor"] = state.get("search_cursor", 0) + 1
    page.goto("https://www.tiktok.com/search/video?q=" + term.replace(" ", "%20"),
              wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    _check(page)
    low, high = cfg.get("for_you_dwell_s") or (4, 9)
    for _ in range(2):
        page.mouse.wheel(0, 1500)
        time.sleep(random.uniform(float(low), float(high)))
    hrefs = page.eval_on_selector_all('a[href*="/video/"]', "els => els.map(e => e.href)")
    return feed_videos(hrefs)[:n]


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
    ap.add_argument("--for-you-only", action="store_true",
                    help="skip follows and profiles; scroll the For You page")
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

    fresh, notes, todo_videos, for_you_ids = [], [], [], set()
    skipped = state.setdefault("skipped", [])     # For You videos judged off topic
    feed = {"scrolled": 0, "on_topic": 0}
    with sync_playwright() as p:
        ctx = browser(p).contexts[0]
        page = ctx.new_page()
        try:
            if not (args.no_follow or args.dry_run or args.for_you_only):
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
            batch = [] if args.for_you_only else \
                (pool[start:] + pool[:start])[: int(cfg.get("profiles_per_run", 4))]
            state["cursor"] = start + len(batch)
            for h in batch:
                if open_profile(page, h):
                    for vid in video_ids(page, h, int(cfg.get("videos_per_profile", 3))):
                        if vid not in seen:
                            todo_videos.append((h, vid))
            # A market search (it steers the feed too, and its top results
            # are read), then For You: what TikTok picks unasked
            queued = {v for _, v in todo_videos}
            for h, vid in search_seed(page, cfg, state):
                if vid not in seen and vid not in queued:
                    todo_videos.append((h, vid))
                    queued.add(vid)
            picked, feed = for_you(page, cfg)
            for h, vid in picked:
                if vid not in seen and vid not in queued and vid not in skipped:
                    todo_videos.append((h, vid))
                    queued.add(vid)
                    for_you_ids.add(vid)
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
        said = f"{v['title']} {v['text']}"
        # TikTok picked it unasked: kept only if it is about markets or calls a trade
        if vid in for_you_ids and not (v["calls"] or on_topic(said)):
            if not args.dry_run:
                skipped.append(vid)
            notes.append(f"{h}/{vid}: For You, off topic, skipped")
            continue
        fresh.append(v)
        if db is not None:
            intel.upsert(db, SOURCE, vid, title=f"@{h}: {v['title'][:150]}", url=v["url"],
                         symbols=sorted({c["symbol"] for c in v["calls"]}),
                         score=len(v["calls"]),
                         detail={"author": h, "published": v["published"], "calls": v["calls"],
                                 "words": len(v["text"].split()),
                                 "said": v["text"][:SAID_CHARS],
                                 "for_you": vid in for_you_ids,
                                 "topics": tag(said)})
    state["skipped"] = skipped[-1000:]
    _save(state)

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

        fleet.record(db, SNAPSHOT, {"readings": out, "posts_read": len(fresh),
                                    "for_you_feed": feed},
                     service="tiktok", remote_path="config/tiktok_sources.yaml")
    for n in notes:
        print(" ", n)
    if feed["scrolled"]:
        print(f"  For You: {feed['on_topic']} of {feed['scrolled']} captions on topic")
    print(f"\n{len(fresh)} new video(s), {sum(1 for v in fresh if v['calls'])} with calls, "
          f"{len(out)} reading(s)" + (" (dry run)" if args.dry_run else ""))
    for v in fresh[:12]:
        called = ", ".join(f"{c['symbol']}{'+' if c['direction'] > 0 else '-'}"
                           for c in v["calls"]) or "no calls"
        print(f"  @{v['author']:<18} {v['title'][:60]!r:<64} {called}")
    return 1 if any(n.startswith("STOPPED") for n in notes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
