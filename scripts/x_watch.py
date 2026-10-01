"""Read X for explicit calls, through the village's own logged-in browser.

    python scripts/x_watch.py --dry-run
    python scripts/x_watch.py --to-railway      # the scheduled run

Same design as `insta_watch.py`, and it shares its browser: the village Edge
profile, signed in to X once by the operator. This script never sees a password.

It follows the handles in `config/x_sources.yaml` a few a run (each opened
first; a missing or suspended account is recorded and skipped), then reads the
**Following** timeline, the voices the village chose. After that it reads a few
tweets off the **For you** tab (`for_you_tweets`; 0 turns it off), the way the
Instagram and TikTok readers scroll theirs, and keeps one only if it is about
markets (`topics.on_topic`) or makes a call, so whatever else X promotes never
reaches the village. It posts, likes, reposts and messages nothing. A tweet is
kept as a call only by the same rule as every other crowd seat
(`crowd.extract_calls`, names turned into tickers first), and one account is
one voice. Posts go to `intel` (source `x`); the aggregate to the `x_calls`
snapshot, merged onto the board by `bots/social_calls.py`.

If X asks the browser to prove it is human, or shows the account locked, the
run stops and says so.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml  # noqa: E402

from scripts.insta_watch import browser  # noqa: E402
from scripts.social_watch import calls_in, readings  # noqa: E402
from scripts.video_watch import universe  # noqa: E402
from src.trading import intel  # noqa: E402
from src.trading.topics import on_topic, tag  # noqa: E402

CONFIG = REPO / "config" / "x_sources.yaml"
STATE = REPO / "data" / "x_state.json"
SOURCE = "x"
SNAPSHOT = "x_calls"


class Challenged(RuntimeError):
    """X wants a human, or the account is locked. The run stops."""


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"followed": [], "missing": []}


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def _check(page) -> None:
    url = page.url
    if "/account/access" in url or "/i/flow/login" in url or "/login" in url:
        raise Challenged(f"X wants the account checked or signed in again ({url})")


def follow(page, handle: str) -> str:
    page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    _check(page)
    body = page.inner_text("body")[:4000]
    if "This account doesn’t exist" in body or "This account doesn't exist" in body \
            or "Account suspended" in body:
        return "missing"
    button = page.locator('[data-testid$="-follow"]')
    if button.count() == 0:
        return "already"
    button.first.click()
    page.wait_for_timeout(2500)
    _check(page)
    return "followed"


def read_timeline(page, limit: int, tab_name: str = "Following") -> list:
    """Tweets on one Home tab ("Following" or "For you"), as the page renders them."""
    page.goto("https://x.com/home", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    _check(page)
    tab = page.get_by_role("tab", name=tab_name)
    if tab.count():
        tab.first.click()
        page.wait_for_timeout(3000)
    found: dict = {}
    for _ in range(12):
        for t in page.eval_on_selector_all('article[data-testid="tweet"]', """els => els.map(a => {
            const text = a.querySelector('[data-testid="tweetText"]');
            const time = a.querySelector('time');
            const link = time ? time.closest('a') : null;
            const user = a.querySelector('[data-testid="User-Name"] a[href^="/"]');
            return {text: text ? text.innerText : '',
                    published: time ? time.getAttribute('datetime') : null,
                    href: link ? link.getAttribute('href') : null,
                    author: user ? user.getAttribute('href').slice(1) : ''};
        })"""):
            if t.get("href") and t["href"] not in found:
                found[t["href"]] = t
        if len(found) >= limit:
            break
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(1800)
    out = []
    for href, t in list(found.items())[:limit]:
        out.append({"id": href.strip("/"), "url": f"https://x.com{href}",
                    "author": t.get("author") or href.split("/")[1],
                    "title": "", "text": t.get("text") or "",
                    "published": (t.get("published") or "").replace("Z", "+00:00")})
    return out


def keeps(tweet: dict) -> bool:
    """Whether a read tweet goes to the village. Everything from Following does;
    one X picked unasked (For you) only if it is about markets or makes a call."""
    if not tweet.get("for_you"):
        return True
    return bool(tweet.get("calls")) or on_topic(tweet.get("text") or "")


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
    shown = kept = 0                                # the For you tab's share on topic
    with sync_playwright() as p:
        ctx = browser(p).contexts[0]
        page = ctx.new_page()
        try:
            if not args.no_follow and not args.dry_run:
                todo = [h for h in handles
                        if h not in state["followed"] and h not in state["missing"]]
                for h in todo[: int(cfg.get("follows_per_run", 4))]:
                    result = follow(page, h)
                    (state["missing"] if result == "missing" else state["followed"]).append(h)
                    notes.append(f"@{h}: {result}")
                    _save(state)
                    time.sleep(int(cfg.get("follow_pause_s", 45)))
            for tweet in read_timeline(page, int(cfg.get("tweets_per_run", 60))):
                if tweet["id"] in seen or not tweet["published"]:
                    continue
                tweet["calls"] = calls_in(tweet, symbols)
                fresh.append(tweet)
            # What X picks unasked, after the voices the village chose
            taken = {t["id"] for t in fresh}
            for_you = int(cfg.get("for_you_tweets", 0))
            for tweet in (read_timeline(page, for_you, "For you") if for_you else []):
                if tweet["id"] in seen or tweet["id"] in taken or not tweet["published"]:
                    continue
                taken.add(tweet["id"])
                tweet["calls"] = calls_in(tweet, symbols)
                tweet["for_you"] = "for_you"
                shown += 1
                if keeps(tweet):
                    kept += 1
                    fresh.append(tweet)
        except Challenged as exc:
            notes.append(f"STOPPED: {exc}")
        finally:
            _save(state)
            page.close()

    for t in fresh:
        if db is not None:
            intel.upsert(db, SOURCE, t["id"], title=f"@{t['author']}: {t['text'][:150]}",
                         url=t["url"], symbols=sorted({c["symbol"] for c in t["calls"]}),
                         score=len(t["calls"]),
                         detail={"author": t["author"], "published": t["published"],
                                 "calls": t["calls"], "topics": tag(t["text"]),
                                 "for_you": t.get("for_you", "")})
    pool = list(fresh)
    if db is not None:
        ids = {t["id"] for t in fresh}
        for row in intel.recent(db, SOURCE, limit=2000):
            if row["item_key"] not in ids:
                d = row["detail"]
                pool.append({"author": d.get("author"), "published": d.get("published"),
                             "calls": d.get("calls") or []})
    out = readings(pool, float(cfg.get("max_age_hours", 24)))
    for r in out:
        r["note"] = r["note"].replace("Reddit:", "X:", 1)
    if db is not None:
        from src.trading import fleet

        fleet.record(db, SNAPSHOT, {"readings": out, "posts_read": len(fresh)},
                     service="x", remote_path="config/x_sources.yaml")
    for n in notes:
        print(" ", n)
    print(f"\n{len(fresh)} new tweet(s), {sum(1 for t in fresh if t['calls'])} with calls, "
          f"{len(out)} reading(s)" + (" (dry run)" if args.dry_run else ""))
    if int(cfg.get("for_you_tweets", 0)):
        print(f"For You: {kept} of {shown} on topic, {shown - kept} off topic and skipped")
    for t in fresh[:12]:
        called = ", ".join(f"{c['symbol']}{'+' if c['direction'] > 0 else '-'}"
                           for c in t["calls"]) or "no calls"
        print(f"  @{t['author']:<18} {t['text'][:60]!r:<64} {called}")
    return 1 if any(n.startswith("STOPPED") for n in notes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
