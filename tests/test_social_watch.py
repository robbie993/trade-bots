"""The Reddit watcher: one combined feed, one author one voice, calls not
mentions. The feed text below is the shape Reddit's RSS returns, trimmed from a
real r/wallstreetbets response of 2026-09-26."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("social_watch_ut", ROOT / "scripts" / "social_watch.py")
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)

SYMBOLS = ["BTC-USD", "NVDA", "SPY", "DOGE-USD"]
NOW = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)

FEED = """<feed><entry><author><name>/u/Itsallaboutmargin</name></author>
<category term="wallstreetbets" label="r/wallstreetbets"/>
<content type="html">&lt;p&gt;Spread on MU. Hope this prints!&lt;/p&gt; submitted by /u/Itsallaboutmargin [link] [comments]</content>
<id>t3_1wp2abc</id><link href="https://www.reddit.com/r/wallstreetbets/comments/1wp2abc/x/" />
<published>2026-09-26T03:09:18+00:00</published><title>MU earning play.</title></entry></feed>"""


def test_the_feed_is_parsed_with_its_subreddit_and_author():
    (p,) = sw.parse_feed(FEED)
    assert p["id"] == "t3_1wp2abc" and p["author"] == "Itsallaboutmargin"
    assert p["sub"] == "wallstreetbets" and "Spread on MU" in p["text"]
    assert "submitted by" not in p["text"]


def _post(author, text, hours_ago=1):
    p = {"author": author, "title": "", "text": text,
         "published": (NOW - timedelta(hours=hours_ago)).isoformat()}
    p["calls"] = sw.calls_in(p, SYMBOLS)
    return p


def test_one_author_is_one_voice():
    spam = [_post("loud", "buy bitcoin now") for _ in range(10)]
    (r,) = sw.readings(spam, 24, now=NOW)
    assert r["symbol"] == "BTC-USD" and r["confidence"] == 12.0


def test_agreeing_authors_add_up():
    out = {r["symbol"]: r for r in sw.readings(
        [_post("a", "buying bitcoin"), _post("b", "buy bitcoin"), _post("c", "bought nvidia")],
        24, now=NOW)}
    assert out["BTC-USD"]["confidence"] == 24.0 and out["NVDA"]["score"] == 100.0


def test_a_mention_and_an_old_post_do_not_vote():
    assert sw.readings([_post("a", "bitcoin was in the news"),
                        _post("b", "buy bitcoin", hours_ago=30)], 24, now=NOW) == []
