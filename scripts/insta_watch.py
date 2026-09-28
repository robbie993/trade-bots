"""Read Instagram for explicit calls, through the village's own logged-in browser.

    python scripts/insta_watch.py --dry-run       # read and print, write nothing
    python scripts/insta_watch.py --to-railway    # the scheduled run

Runs on the operator's PC. It drives the village's Edge profile
(%LOCALAPPDATA%/village-browser), where the operator signed in to Instagram
once by hand; this script never sees or types a password.

**Building the feed.** Instagram's feed follows who an account follows, so the
account follows the handles in `config/instagram_sources.yaml`, a few a run with
long pauses (`follows_per_run`, `follow_pause_s`). Each handle is opened first;
one that does not exist is recorded as missing and never followed. The script
likes nothing, comments on nothing and messages no one.

**Reading.** Each run opens the followed profiles (a rotating few), takes the
newest posts' captions from the page's own metadata, and scrolls the home feed
once for whatever Instagram now recommends. Then it scrolls what Instagram picks
for the account rather than who it follows, the For You side: the Reels tab one
reel at a time, pausing on each like a person watching, and the Explore grid
(`for_you_reels`, `for_you_explore`, `for_you_dwell_s`; 0 turns either off).
Those posts are read the same way and marked with where they came from, and one
is kept only if it is about markets (`topics.on_topic`) or makes a call; the
rest are remembered as skipped, so Instagram's pranks never reach the village.

**Hearing.** A reel is speech more than caption, so each new reel (at most
`reels_heard_per_run`) is also heard: yt-dlp fetches its audio as the signed-in
account, with the browser's own Instagram cookies handed over in a temporary
file, and the local Whisper model the YouTube and TikTok watchers use
transcribes it. Audio and cookie file are deleted straight after. Captions and
what is said go through the same rule as Reddit and YouTube
(`crowd.extract_calls`, names turned into tickers first): a ticker with a
directional word beside it is a call, a bare mention is not. One account is one
voice. The start of what was said is kept with the post (`said`) for the daily
research review.

Every post read goes to `intel` (source `instagram`); the aggregate of the last
`max_age_hours` is stored as the `instagram_calls` snapshot for
`bots/social_calls.py`. If Instagram asks the browser to prove it is human, the
run stops and says so rather than trying to get past it.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
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
from scripts.social_watch import calls_in, readings  # noqa: E402
from scripts.video_watch import universe  # noqa: E402
from src.trading import intel  # noqa: E402
from src.trading.topics import on_topic, tag  # noqa: E402

CONFIG = REPO / "config" / "instagram_sources.yaml"
STATE = REPO / "data" / "instagram_state.json"
SOURCE = "instagram"
SNAPSHOT = "instagram_calls"
PORT = 9333
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "village-browser"
PROFILES_PER_RUN = 6
#: What is said in a reel is kept up to this many characters: enough to know what
#: it is about, not a transcript archive.
SAID_CHARS = 1000


class Challenged(RuntimeError):
    """Instagram wants a human. The run stops; nobody tries to get past it."""


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"followed": [], "missing": [], "cursor": 0}


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def browser(p):
    """Attach to the village Edge, starting it with remote control if needed."""
    try:
        return p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", timeout=5000)
    except Exception:  # noqa: BLE001 - not running with the port: start it
        subprocess.Popen([EDGE, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
                          "--no-first-run", "--no-default-browser-check", "about:blank"])
        for _ in range(30):
            time.sleep(1)
            try:
                return p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", timeout=5000)
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError("could not start or reach the village browser")


def _check(page) -> None:
    url = page.url
    if "/challenge" in url or "/accounts/suspended" in url:
        raise Challenged(f"Instagram asked for a check at {url}")
    if "/accounts/login" in url:
        raise Challenged("the village browser is signed out of Instagram")


def _meta(page, prop: str) -> str:
    el = page.query_selector(f'meta[property="{prop}"]')
    return (el.get_attribute("content") or "") if el else ""


def open_profile(page, handle: str) -> bool:
    page.goto(f"https://www.instagram.com/{handle}/", wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    _check(page)
    body = page.inner_text("body")[:3000]
    return "Sorry, this page isn't available" not in body and "Page not found" not in body


def follow(page, handle: str) -> str:
    if not open_profile(page, handle):
        return "missing"
    button = page.get_by_role("button", name=re.compile(r"^Follow$"))
    if button.count() == 0:
        return "already"            # Following / Requested / Message
    button.first.click()
    page.wait_for_timeout(2500)
    _check(page)
    return "followed"


def post_links(page, limit: int) -> list:
    hrefs = page.eval_on_selector_all(
        'a[href*="/p/"], a[href*="/reel/"]', "els => els.map(e => e.getAttribute('href'))")
    out = []
    for h in hrefs:
        m = re.search(r"/(p|reel)/([A-Za-z0-9_-]+)", h or "")
        if m and m.group(2) not in [x[1] for x in out]:
            out.append((m.group(1), m.group(2)))
        if len(out) >= limit:
            break
    return out


def reel_code(url: str) -> str:
    """The reel a Reels-tab address is showing: /reels/<code>/ or /reel/<code>/."""
    m = re.search(r"/reels?/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else ""


def dwell(cfg: dict) -> None:
    """Stay on a post for a while, a different while each time."""
    low, high = cfg.get("for_you_dwell_s") or (4, 9)
    time.sleep(random.uniform(float(low), float(high)))


def for_you(page, cfg: dict) -> list:
    """What Instagram picks for the account: (kind, code, where), nothing read yet.

    The Reels tab plays one reel at a time and moves its address along as it
    goes, so each reel is taken from the address and the next one is a key
    press away. The Explore grid is ordinary links, scrolled a few times."""
    out, codes = [], set()
    reels = int(cfg.get("for_you_reels", 15))
    if reels:
        page.goto("https://www.instagram.com/reels/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        _check(page)
        for _ in range(reels * 2):          # a reel that never loads costs a step, not the run
            code = reel_code(page.url)
            if code and code not in codes:
                codes.add(code)
                out.append(("reel", code, "reels"))
            if len(codes) >= reels:
                break
            dwell(cfg)
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(1500)
            _check(page)
    explore = int(cfg.get("for_you_explore", 12))
    if explore:
        page.goto("https://www.instagram.com/explore/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        _check(page)
        for _ in range(3):
            page.mouse.wheel(0, 2200)
            dwell(cfg)
        for kind, code in post_links(page, explore + len(codes)):
            if code not in codes and len(out) < reels + explore:
                codes.add(code)
                out.append((kind, code, "explore"))
    return out


def read_post(page, kind: str, code: str, author_hint: str = "") -> dict:
    url = f"https://www.instagram.com/{kind}/{code}/"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    _check(page)
    desc = _meta(page, "og:description") or _meta(page, "og:title")
    # og:description reads: '123 likes, 4 comments - handle on September 25, 2026: "caption"'
    author = author_hint
    m = re.search(r" - ([A-Za-z0-9._]+) on ", desc)
    if m:
        author = m.group(1)
    caption = desc.split(": ", 1)[1].strip('"') if ": " in desc else desc
    stamp = page.eval_on_selector("time[datetime]", "e => e.getAttribute('datetime')") \
        if page.query_selector("time[datetime]") else None
    return {"id": f"{kind}/{code}", "url": url, "author": author, "text": caption,
            "title": "", "published": (stamp or datetime.now(timezone.utc).isoformat())
            .replace("Z", "+00:00")}


def jar(cookies: list) -> str:
    """The browser's cookies in the Netscape format yt-dlp reads."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain") or ""
        lines.append("\t".join((
            domain, "TRUE" if domain.startswith(".") else "FALSE", c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE", str(max(int(c.get("expires") or 0), 0)),
            c["name"], c["value"])))
    return "\n".join(lines) + "\n"


def heard(url: str, cookies: str, max_s: int, agent: str = "") -> str:
    """What is said in one reel, up to `max_s` seconds in.

    yt-dlp fetches only the audio as the browser would, signed in with its
    cookies (`jar`) and under its user agent, and the local Whisper model
    transcribes it. The audio and the cookie file live in a temporary folder
    that is deleted on the way out."""
    import tempfile

    import yt_dlp

    with tempfile.TemporaryDirectory() as tmp:
        cookie_file = Path(tmp) / "cookies.txt"
        cookie_file.write_text(cookies, encoding="utf-8")
        opts = {"quiet": True, "no_warnings": True, "noprogress": True,
                "format": "bestaudio/best", "cookiefile": str(cookie_file),
                "outtmpl": str(Path(tmp) / "a.%(ext)s")}
        if agent:
            opts["http_headers"] = {"User-Agent": agent}
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
        audio = next(Path(tmp).glob("a.*"))
        if video_watch._whisper is None:
            from faster_whisper import WhisperModel

            video_watch._whisper = WhisperModel(str(video_watch.WHISPER_MODEL),
                                                device="cpu", compute_type="int8")
        segments, _ = video_watch._whisper.transcribe(str(audio), beam_size=1, vad_filter=True)
        words = []
        for seg in segments:
            if seg.start > max_s:
                break
            words.append(seg.text.strip())
        return " ".join(words)


def keeps(post: dict) -> bool:
    """Whether a read post goes to the village. Everything from the followed side
    does; a post Instagram picked unasked (For You) only if its caption or what
    is said in it is about markets, or it makes a call."""
    if not post.get("for_you"):
        return True
    return bool(post.get("calls")) or on_topic(f"{post['text']} {post.get('heard', '')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-railway", action="store_true")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-follow", action="store_true", help="read only this run")
    ap.add_argument("--for-you-only", action="store_true",
                    help="skip follows, profiles and the home feed; scroll Reels and Explore")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    handles = [a["handle"] for a in cfg.get("accounts") or []]
    symbols = universe()
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

    fresh, notes, cookies, agent = [], [], "", ""
    skipped = state.setdefault("skipped", [])     # For You posts judged off topic
    with sync_playwright() as p:
        b = browser(p)
        ctx = b.contexts[0]
        page = ctx.new_page()
        try:
            # 1. seed the feed, a few follows a run
            if not (args.no_follow or args.dry_run or args.for_you_only):
                todo = [h for h in handles
                        if h not in state["followed"] and h not in state["missing"]]
                for h in todo[: int(cfg.get("follows_per_run", 4))]:
                    result = follow(page, h)
                    (state["missing"] if result == "missing" else state["followed"]).append(h)
                    notes.append(f"@{h}: {result}")
                    _save(state)
                    time.sleep(int(cfg.get("follow_pause_s", 45)))
            # 2. read a rotating few of the followed profiles
            pool = [h for h in handles if h not in state["missing"]]
            start = state.get("cursor", 0) % max(1, len(pool))
            batch = [] if args.for_you_only else (pool[start:] + pool[:start])[:PROFILES_PER_RUN]
            state["cursor"] = start + len(batch)
            for h in batch:
                if not open_profile(page, h):
                    continue
                for kind, code in post_links(page, int(cfg.get("posts_per_profile", 6))):
                    if f"{kind}/{code}" in seen:
                        continue
                    post = read_post(page, kind, code, author_hint=h)
                    post["calls"] = calls_in(post, symbols)
                    fresh.append(post)
                    seen.add(post["id"])
                    page.wait_for_timeout(1500)
            # 3. the home feed: what the algorithm now recommends
            if not args.for_you_only:
                page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                _check(page)
                for _ in range(3):
                    page.mouse.wheel(0, 2200)
                    page.wait_for_timeout(2000)
            for kind, code in ([] if args.for_you_only else post_links(page, 10)):
                if f"{kind}/{code}" in seen:
                    continue
                post = read_post(page, kind, code)
                post["calls"] = calls_in(post, symbols)
                post["from_feed"] = True
                fresh.append(post)
                seen.add(post["id"])
            # 4. For You: the Reels tab and Explore, what Instagram picks unasked
            for kind, code, where in for_you(page, cfg):
                if f"{kind}/{code}" in seen or f"{kind}/{code}" in skipped:
                    continue
                post = read_post(page, kind, code)
                post["calls"] = calls_in(post, symbols)
                post["from_feed"] = True
                post["for_you"] = where
                fresh.append(post)
                seen.add(post["id"])
                page.wait_for_timeout(1500)
        except Challenged as exc:
            notes.append(f"STOPPED: {exc}")
        finally:
            _save(state)
            try:
                cookies = jar(ctx.cookies("https://www.instagram.com"))
                agent = page.evaluate("() => navigator.userAgent")
            except Exception as exc:  # noqa: BLE001 - no cookies, no audio; captions still count
                notes.append(f"reels not heard: no cookies ({str(exc)[:80]})")
            page.close()

    # 5. what each new reel says, not only its caption
    reels = [post for post in fresh if post["id"].startswith("reel/")] if cookies else []
    misses = 0
    for post in reels[: int(cfg.get("reels_heard_per_run", 20))]:
        if misses >= 3:                     # Instagram is refusing: stop asking this run
            notes.append("reels not heard: three failures in a row, the rest wait")
            break
        try:
            post["heard"] = heard(post["url"], cookies, int(cfg.get("max_video_s", 600)), agent)
        except Exception as exc:  # noqa: BLE001 - one reel is not the run
            misses += 1
            notes.append(f"{post['id']}: not heard ({str(exc)[:80]})")
            continue
        misses = 0
        post["calls"] = calls_in({"title": post["text"], "text": post["heard"]}, symbols)

    kept = [post for post in fresh if keeps(post)]
    if not args.dry_run:
        skipped += [post["id"] for post in fresh if not keeps(post)]
    dropped, fresh = len(fresh) - len(kept), kept
    state["skipped"] = skipped[-1000:]
    _save(state)

    for post in fresh:
        said = post.get("heard", "")
        if db is not None:
            intel.upsert(db, SOURCE, post["id"],
                         title=f"@{post['author']}: {post['text'][:150]}",
                         url=post["url"], symbols=sorted({c["symbol"] for c in post["calls"]}),
                         score=len(post["calls"]),
                         detail={"author": post["author"], "published": post["published"],
                                 "calls": post["calls"], "from_feed": post.get("from_feed", False),
                                 "for_you": post.get("for_you", ""),
                                 "words": len(said.split()), "said": said[:SAID_CHARS],
                                 "topics": tag(f"{post['text']} {said}")})
    pool = list(fresh)
    if db is not None:
        ids = {p["id"] for p in fresh}
        for row in intel.recent(db, SOURCE, limit=2000):
            if row["item_key"] not in ids:
                d = row["detail"]
                pool.append({"author": d.get("author"), "published": d.get("published"),
                             "calls": d.get("calls") or []})
    out = readings(pool, float(cfg.get("max_age_hours", 24)))
    for r in out:
        r["note"] = r["note"].replace("Reddit:", "Instagram:", 1)
    if db is not None:
        from src.trading import fleet

        fleet.record(db, SNAPSHOT, {"readings": out, "posts_read": len(fresh)},
                     service="instagram", remote_path="config/instagram_sources.yaml")
    for n in notes:
        print(" ", n)
    print(f"\n{len(fresh)} new post(s), {sum(1 for p in fresh if p['calls'])} with calls, "
          f"{sum(1 for p in fresh if p.get('heard'))} reel(s) heard, "
          f"{dropped} For You post(s) off topic and skipped, "
          f"{len(out)} reading(s)" + (" (dry run)" if args.dry_run else ""))
    for post in fresh[:12]:
        called = ", ".join(f"{c['symbol']}{'+' if c['direction'] > 0 else '-'}"
                           for c in post["calls"]) or "no calls"
        print(f"  @{post['author']:<18} {post['text'][:60]!r:<64} {called}")
    return 1 if any(n.startswith("STOPPED") for n in notes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
