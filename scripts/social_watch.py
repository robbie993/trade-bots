"""Read subreddits for explicit calls, and carry them into the village.

    python scripts/social_watch.py --dry-run
    python scripts/social_watch.py --to-railway      # the scheduled run

Runs on the operator's PC every 30 minutes. Reddit refuses its JSON API to
datacentres and to this PC alike (HTTP 403), but serves every subreddit's public
RSS feed to a home connection with no account. So there is no login to lose and
no browser to be detected.

Same rule as the YouTube watcher and the crowd module: a ticker with a
directional word beside it is a call, a bare mention is not, and names become
tickers through the news desk's alias list. What differs is who a voice is:
**one Reddit author is one voice** across every post and subreddit in the
window. Ten posts from one account saying "buy DOGE" are one call, because the
crowd-signal literature is about agreement between people, and a single loud
account is the cheapest thing on the internet.

Every post read goes to `intel` (source `reddit`) with its calls; posts from
inside `max_age_hours` are aggregated into readings and stored as the
`reddit_calls` snapshot, which `bots/social_calls.py` repeats onto the board.
Untested against returns, like every crowd seat.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml  # noqa: E402

from scripts.video_watch import normalise, universe  # noqa: E402
from src.trading import crowd, intel  # noqa: E402

CONFIG = REPO / "config" / "social_sources.yaml"
SOURCE = "reddit"
SNAPSHOT = "reddit_calls"
USER_AGENT = "windows:village-scout:0.1"

_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)


def _field(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.S)
    return html.unescape(m.group(1)) if m else ""


def parse_feed(xml: str) -> list:
    posts = []
    for e in _ENTRY.findall(xml):
        body = _field(r'<content type="html">(.*?)</content>', e)
        body = re.sub(r"<[^>]+>", " ", html.unescape(body))
        for noise in ("submitted by", "[link]", "[comments]"):
            body = body.replace(noise, " ")
        posts.append({
            "id": _field(r"<id>(.*?)</id>", e),
            "title": _field(r"<title>(.*?)</title>", e),
            "author": _field(r"<name>(.*?)</name>", e).replace("/u/", ""),
            "published": _field(r"<published>(.*?)</published>", e),
            "url": _field(r'<link href="(.*?)"', e),
            "sub": _field(r'<category term="(.*?)"', e),
            "text": " ".join(body.split()),
        })
    return posts


def fetch(subs: list, limit: int) -> list:
    """Every subreddit in one request: `r/a+b+c` is Reddit's own combined feed.
    Eleven requests three seconds apart drew a 429 after the first."""
    req = urllib.request.Request(
        f"https://www.reddit.com/r/{'+'.join(subs)}/new/.rss?limit={limit}",
        headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed host
        return parse_feed(r.read().decode("utf-8", "replace"))


def calls_in(post: dict, symbols) -> list:
    text = normalise(f"{post['title']}. {post['text']}", symbols)
    return [{"symbol": c.symbol, "direction": c.direction, "phrase": c.phrase}
            for c in crowd.extract_calls(text, symbols)]


def readings(posts: list, max_age_hours: float, now=None) -> list:
    """One voice per author: an author's (symbol, direction) counts once."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    voices = {}
    for p in posts:
        try:
            if datetime.fromisoformat(p["published"]) < cutoff:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        for c in p.get("calls") or []:
            voices.setdefault((p.get("author") or "?", c["symbol"], c["direction"]), c["phrase"])
    agg = {}
    for (_author, symbol, direction), phrase in voices.items():
        r = agg.setdefault(symbol, crowd.CrowdReading(symbol=symbol))
        if direction > 0:
            r.bullish += 1
        else:
            r.bearish += 1
        r.calls.append(crowd.Call(symbol, direction, "", phrase))
    return [{"symbol": r.symbol, "score": float(r.score), "confidence": float(r.confidence),
             "note": f"Reddit: {r.note}"[:240]} for r in agg.values() if r.confidence]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-railway", action="store_true")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    symbols = universe()
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

    fresh, failed = [], []
    subs = [s["name"] for s in cfg.get("subreddits") or []]
    try:
        posts = fetch(subs, int(cfg.get("per_run", 100)))
    except Exception as exc:  # noqa: BLE001 - reported, and the run ends quietly
        posts = []
        failed.append(f"reddit: {str(exc)[:100]}")
    new = [p for p in posts if p["id"] and p["id"] not in seen]
    for p in new:
        p["calls"] = calls_in(p, symbols)
        fresh.append(p)
        if db is not None:
            intel.upsert(db, SOURCE, p["id"],
                         title=f"r/{p['sub']} · u/{p['author']}: {p['title']}",
                         url=p["url"], symbols=sorted({c["symbol"] for c in p["calls"]}),
                         score=len(p["calls"]),
                         detail={"sub": p["sub"], "author": p["author"],
                                 "published": p["published"], "calls": p["calls"]})
    print(f"  {len(subs)} subreddits in one feed: {len(posts)} posts, {len(new)} new, "
          f"{sum(1 for p in new if p['calls'])} with calls")

    pool = list(fresh)
    if db is not None:
        ids = {p["id"] for p in fresh}
        for row in intel.recent(db, SOURCE, limit=2000):
            if row["item_key"] in ids:
                continue
            d = row["detail"]
            pool.append({"author": d.get("author"), "published": d.get("published"),
                         "calls": d.get("calls") or []})
    out = readings(pool, float(cfg.get("max_age_hours", 24)))
    if db is not None:
        from src.trading import fleet

        fleet.record(db, SNAPSHOT, {"readings": out, "posts_read": len(fresh)},
                     service="reddit", remote_path="config/social_sources.yaml")
    print(f"\n{len(fresh)} new post(s), {len(out)} reading(s) from the last "
          f"{cfg.get('max_age_hours', 24)}h" + (" (dry run)" if args.dry_run else ""))
    for r in sorted(out, key=lambda r: -r["confidence"]):
        print(f"  {r['symbol']:<9} {r['score']:+7.2f}  conf {r['confidence']:5.1f}  {r['note'][:90]}")
    for f in failed:
        print(f"  FAILED {f}", file=sys.stderr)
    return 1 if failed and not fresh else 0


if __name__ == "__main__":
    raise SystemExit(main())
