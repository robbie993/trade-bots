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
once for whatever Instagram now recommends. Captions go through the same rule
as Reddit and YouTube (`crowd.extract_calls`, names turned into tickers first):
a ticker with a directional word beside it is a call, a bare mention is not.
One account is one voice.

Every post read goes to `intel` (source `instagram`); the aggregate of the last
`max_age_hours` is stored as the `instagram_calls` snapshot for
`bots/social_calls.py`. If Instagram asks the browser to prove it is human, the
run stops and says so rather than trying to get past it.
"""
from __future__ import annotations

import argparse
import json
import os
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

from scripts.social_watch import calls_in, readings  # noqa: E402
from scripts.video_watch import universe  # noqa: E402
from src.trading import intel  # noqa: E402

CONFIG = REPO / "config" / "instagram_sources.yaml"
STATE = REPO / "data" / "instagram_state.json"
SOURCE = "instagram"
SNAPSHOT = "instagram_calls"
PORT = 9333
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "village-browser"
PROFILES_PER_RUN = 6


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-railway", action="store_true")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-follow", action="store_true", help="read only this run")
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

    fresh, notes = [], []
    with sync_playwright() as p:
        b = browser(p)
        ctx = b.contexts[0]
        page = ctx.new_page()
        try:
            # 1. seed the feed, a few follows a run
            if not args.no_follow and not args.dry_run:
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
            batch = (pool[start:] + pool[:start])[:PROFILES_PER_RUN]
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
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            _check(page)
            for _ in range(3):
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(2000)
            for kind, code in post_links(page, 10):
                if f"{kind}/{code}" in seen:
                    continue
                post = read_post(page, kind, code)
                post["calls"] = calls_in(post, symbols)
                post["from_feed"] = True
                fresh.append(post)
                seen.add(post["id"])
        except Challenged as exc:
            notes.append(f"STOPPED: {exc}")
        finally:
            _save(state)
            page.close()

    for post in fresh:
        if db is not None:
            intel.upsert(db, SOURCE, post["id"],
                         title=f"@{post['author']}: {post['text'][:150]}",
                         url=post["url"], symbols=sorted({c["symbol"] for c in post["calls"]}),
                         score=len(post["calls"]),
                         detail={"author": post["author"], "published": post["published"],
                                 "calls": post["calls"], "from_feed": post.get("from_feed", False)})
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
          f"{len(out)} reading(s)" + (" (dry run)" if args.dry_run else ""))
    for post in fresh[:12]:
        called = ", ".join(f"{c['symbol']}{'+' if c['direction'] > 0 else '-'}"
                           for c in post["calls"]) or "no calls"
        print(f"  @{post['author']:<18} {post['text'][:60]!r:<64} {called}")
    return 1 if any(n.startswith("STOPPED") for n in notes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
