"""Watch YouTube channels for explicit calls, and carry them into the village.

    python scripts/video_watch.py --dry-run          # print, write nothing
    python scripts/video_watch.py --to-railway       # the scheduled run

Runs on the operator's PC (Windows Task Scheduler, every 30 minutes), because
YouTube is friendlier to a home connection than to a datacentre and because
this is where a later vision step would run.

For each channel in `config/video_channels.yaml` it looks at the newest uploads,
skips any it has already read, and for the rest pulls YouTube's own captions —
no audio is downloaded, no video is downloaded. The transcript goes through
`crowd.extract_calls`, the village's existing rule for what counts as a call:
a ticker with a directional word next to it ("buying Nvidia", "I'd sell
bitcoin here"), never a bare mention. Company and coin names are first turned
into tickers with the news desk's alias list, because nobody on television says
"BTC-USD".

**One video is one voice.** A call repeated ten times in one video is one call,
which is `extract_calls`' own rule for one text. A channel posting ten clips of
the same interview is ten voices, and that is a known weakness, stated here
rather than hidden.

Two things are written, both to the shared Railway ledger:

* every video read goes to `intel` (source `youtube`): title, link, channel and
  the calls found in it, so Mission Control can show what was watched;
* the calls from videos published inside `max_age_hours` are aggregated into
  readings and stored as the `youtube_calls` fleet snapshot, which the worker
  unpacks and `bots/video_calls.py` repeats onto the signal board.

What a presenter says on air has not been tested against returns. It is one
seat in a debate, and the `signal_trust` gene decides what it is worth.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from src.trading import crowd, intel  # noqa: E402
from src.trading.news import _aliases_for  # noqa: E402

CONFIG = REPO / "config" / "video_channels.yaml"
FIRMS = REPO / "config" / "firm_config.yaml"
SOURCE = "youtube"
SNAPSHOT = "youtube_calls"

#: Seconds between requests to YouTube, and new videos read per run. The first
#: run asked for fifteen transcripts back to back and YouTube answered 429 from
#: the fifteenth on. Every half hour, a handful at a time, is what a person
#: clicking around would look like; the backlog drains over a few runs.
PAUSE_S = 6.0
MAX_NEW_PER_RUN = 8


class RateLimited(RuntimeError):
    """YouTube said slow down. The run stops rather than asking again."""


def universe() -> list:
    firms = (yaml.safe_load(FIRMS.read_text()) or {}).get("firms") or {}
    out = set()
    for spec in firms.values():
        out |= {str(s).upper() for s in spec.get("universe") or []}
    return sorted(out)


def normalise(text: str, symbols) -> str:
    """Turn "bitcoin" into "BTC" and "Nvidia" into "NVDA", so the calls rule can see them."""
    for symbol in symbols:
        head = symbol.split("-")[0]
        for name in sorted(_aliases_for(symbol), key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(name)}\b", head, text, flags=re.IGNORECASE)
    return text


def latest_videos(handle: str, n: int) -> list:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
            "playlistend": n, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(f"https://www.youtube.com/@{handle}/videos", download=False)
    return [e["id"] for e in (info or {}).get("entries") or [] if e.get("id")]


def transcript(video_id: str) -> dict:
    """Title, channel, publish time and caption text. Downloads captions only."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                           "skip_download": True}) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    tracks = (info.get("subtitles") or {}).get("en") or \
        (info.get("automatic_captions") or {}).get("en") or []
    url = next((t["url"] for t in tracks if t.get("ext") == "json3"), None)
    text = ""
    if url:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - YouTube's own caption URL
            data = json.loads(r.read().decode("utf-8"))
        text = " ".join(s.get("utf8", "") for e in data.get("events", [])
                        for s in e.get("segs") or [])
    ts = info.get("timestamp")
    return {
        "id": video_id,
        "title": info.get("title") or "",
        "channel": info.get("channel") or "",
        "published": (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                      if ts else None),
        "text": " ".join(text.split()),
    }


def calls_in(video: dict, symbols) -> list:
    text = normalise(f"{video['title']}. {video['text']}", symbols)
    return [{"symbol": c.symbol, "direction": c.direction, "phrase": c.phrase}
            for c in crowd.extract_calls(text, symbols)]


def readings(videos: list, max_age_hours: float, now=None) -> list:
    """One reading per called symbol, from videos young enough to still vote."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    agg: dict = {}
    for v in videos:
        published = v.get("published")
        if not published or datetime.fromisoformat(published) < cutoff:
            continue
        for c in v.get("calls") or []:
            r = agg.setdefault(c["symbol"], crowd.CrowdReading(symbol=c["symbol"]))
            if c["direction"] > 0:
                r.bullish += 1
            else:
                r.bearish += 1
            r.calls.append(crowd.Call(c["symbol"], c["direction"], "", c["phrase"]))
    out = []
    for r in agg.values():
        if not r.confidence:
            continue                       # split evenly: loud, and says nothing
        out.append({"symbol": r.symbol, "score": float(r.score),
                    "confidence": float(r.confidence),
                    "note": f"YouTube: {r.note}"[:240]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-railway", action="store_true", help="write to the village's Railway Postgres")
    ap.add_argument("--database-url", default="", help="write to this database instead")
    ap.add_argument("--dry-run", action="store_true", help="read and print; write nothing")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    symbols = universe()
    db = None
    if not args.dry_run and (args.database_url or args.to_railway):
        from src.db.connection import Database
        from scripts.fleet_sync import railway_database_url

        db = Database.from_url(args.database_url or railway_database_url())
        db.init_schema()

    seen = set()
    if db is not None:
        seen = {r["item_key"] for r in db.query(
            "SELECT item_key FROM intel WHERE source = ?", (SOURCE,))}

    read, failed = [], []
    budget = int(cfg.get("max_new_per_run", MAX_NEW_PER_RUN))
    try:
        for ch in cfg.get("channels") or []:
            handle = ch["handle"]
            if budget <= 0:
                break
            try:
                ids = latest_videos(handle, int(cfg.get("per_channel", 5)))
            except Exception as exc:  # noqa: BLE001 - one channel is not the watch
                _raise_if_limited(exc)
                failed.append(f"@{handle}: {str(exc)[:100]}")
                continue
            time.sleep(PAUSE_S)
            budget = _read_channel(ch, ids, seen, symbols, db, read, failed, budget)
    except RateLimited as exc:
        failed.append(f"stopped early, YouTube is rate limiting: {exc}")
    return _finish(cfg, db, read, failed, args)


def _raise_if_limited(exc) -> None:
    if "429" in str(exc) or "Too Many Requests" in str(exc):
        raise RateLimited(str(exc)[:100]) from exc


def _read_channel(ch, ids, seen, symbols, db, read, failed, budget) -> int:
    handle = ch["handle"]
    for vid in ids:
        if vid in seen or budget <= 0:
            continue
        budget -= 1
        try:
            video = transcript(vid)
        except Exception as exc:  # noqa: BLE001
            _raise_if_limited(exc)
            if "members" in str(exc).lower():
                # Never readable without a paid membership: remember it as
                # seen so it is not asked for again every half hour.
                if db is not None:
                    intel.upsert(db, SOURCE, vid, title=f"[members only] @{handle}",
                                 url=f"https://www.youtube.com/watch?v={vid}",
                                 detail={"skipped": "members only"})
                print(f"  @{handle:<16} [members only, skipped]")
            else:
                failed.append(f"{vid}: {str(exc)[:100]}")
            time.sleep(PAUSE_S)
            continue
        time.sleep(PAUSE_S)
        video["calls"] = calls_in(video, symbols) if video["text"] else []
        video["group"] = ch.get("group", "")
        video["handle"] = handle
        read.append(video)
        called = ", ".join(f"{c['symbol']}{'+' if c['direction'] > 0 else '-'}"
                           for c in video["calls"]) or "no calls"
        caps = "" if video["text"] else " [no captions]"
        print(f"  @{handle:<16} {video['title'][:60]:<60} {called}{caps}")
        if db is not None:
            intel.upsert(db, SOURCE, vid,
                         title=f"{video['channel']}: {video['title']}",
                         url=f"https://www.youtube.com/watch?v={vid}",
                         symbols=sorted({c["symbol"] for c in video["calls"]}),
                         score=len(video["calls"]),
                         detail={"channel": video["channel"], "group": video["group"],
                                 "published": video["published"], "calls": video["calls"],
                                 "words": len(video["text"].split()),
                                 "captions": bool(video["text"])})
    return budget


def _finish(cfg, db, read, failed, args) -> int:
    # Readings come from everything still young enough, not only this run's
    # new videos — otherwise a quiet half-hour would erase the morning's calls.
    pool = list(read)
    if db is not None:
        read_ids = {v["id"] for v in read}
        for row in intel.recent(db, SOURCE, limit=300):
            if row["item_key"] in read_ids:
                continue
            d = row["detail"]
            pool.append({"published": d.get("published"), "calls": d.get("calls") or []})
    out = readings(pool, float(cfg.get("max_age_hours", 24)))

    if db is not None:
        from src.trading import fleet

        fleet.record(db, SNAPSHOT, {"readings": out, "videos_read": len(read)},
                     service="youtube", remote_path="config/video_channels.yaml")
    print(f"\n{len(read)} new video(s) read, {len(out)} reading(s) from the last "
          f"{cfg.get('max_age_hours', 24)}h" + (" (dry run)" if args.dry_run else ""))
    for r in out:
        print(f"  {r['symbol']:<9} {r['score']:+7.2f}  conf {r['confidence']:5.1f}  {r['note'][:80]}")
    for f in failed:
        print(f"  FAILED {f}", file=sys.stderr)
    return 1 if failed and not read else 0


if __name__ == "__main__":
    raise SystemExit(main())
