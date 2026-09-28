"""The For You scroll: what Instagram and TikTok pick for the village account.
The browser half needs a signed-in Edge; these pin the parsing it relies on."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts import insta_watch, tiktok_watch
from src.trading import topics

ROOT = Path(__file__).resolve().parents[1]


def test_a_reels_tab_address_gives_the_reel_it_is_showing():
    assert insta_watch.reel_code("https://www.instagram.com/reels/DDxt9qBNdjZ/") == "DDxt9qBNdjZ"
    assert insta_watch.reel_code("https://www.instagram.com/reel/Ab-c_1/?igsh=x") == "Ab-c_1"


def test_the_reels_tab_itself_is_not_a_reel():
    assert insta_watch.reel_code("https://www.instagram.com/reels/") == ""
    assert insta_watch.reel_code("https://www.instagram.com/") == ""


def test_the_for_you_page_keeps_every_author_once_per_video():
    hrefs = ["https://www.tiktok.com/@trader_joe/video/111",
             "https://www.tiktok.com/@trader_joe/video/111?is_from_webapp=1",
             "https://www.tiktok.com/@cryptoqueen/video/222",
             "https://www.tiktok.com/foryou",
             None]
    assert tiktok_watch.feed_videos(hrefs) == [("trader_joe", "111"), ("cryptoqueen", "222")]


def test_a_profile_still_reads_only_its_own_videos(monkeypatch):
    class Page:
        def eval_on_selector_all(self, *_):
            return ["https://www.tiktok.com/@a/video/1", "https://www.tiktok.com/@b/video/2",
                    "https://www.tiktok.com/@A/video/3"]

    assert tiktok_watch.video_ids(Page(), "a", 5) == ["1", "3"]


def test_the_scroll_is_configured_and_modest():
    ig = yaml.safe_load((ROOT / "config" / "instagram_sources.yaml").read_text(encoding="utf-8"))
    tt = yaml.safe_load((ROOT / "config" / "tiktok_sources.yaml").read_text(encoding="utf-8"))
    assert 0 < ig["for_you_reels"] <= 30 and 0 < ig["for_you_explore"] <= 30
    assert 0 < tt["for_you_videos"] <= 20
    for cfg in (ig, tt):
        low, high = cfg["for_you_dwell_s"]
        assert 2 <= low < high
    # the whole For You run fits in the hour the socials task has
    assert tt["for_you_videos"] * tt["for_you_watch_max_s"] <= 15 * 60
    assert 0 < ig["reels_heard_per_run"] <= 30 and ig["max_video_s"] <= 600


# What the feeds show that the village wants: markets, calls, and the ideas it
# already tags. What it does not: the rest of the For You page.
ON_TOPIC = ["DOGE is about to send it", "$TSLA calls printing", "I backtested 10,000 strategies",
            "Fed decision today, what it means for the stock market", "new pump.fun launch"]
OFF_TOPIC = ["landlord prank gone wrong", "best excuses for being late to work",
             "supermarket haul", "you always have options", "fed up with my job", "$100 giveaway"]


def test_markets_talk_is_on_topic_and_a_prank_is_not():
    assert all(topics.on_topic(t) for t in ON_TOPIC)
    assert not any(topics.on_topic(t) for t in OFF_TOPIC)


def test_markets_talk_alone_does_not_tag_a_post_for_the_research_review():
    assert topics.tag("stocks are up today") == []


def test_a_markets_video_is_watched_to_the_end_and_the_rest_is_left_quickly():
    cfg = {"for_you_dwell_s": [4, 9], "for_you_watch_max_s": 90}
    assert tiktok_watch.watch_for("why DOGE could double #crypto", 47.5, cfg) == 47.5
    assert tiktok_watch.watch_for("my 0DTE options strategy", 240, cfg) == 90
    assert tiktok_watch.watch_for("stocks in 5 seconds", 2, cfg) == 4
    assert tiktok_watch.watch_for("bitcoin update", 0, cfg) == 27      # length unknown: 3 x 9 s
    for _ in range(20):
        assert 1 <= tiktok_watch.watch_for("landlord prank gone wrong", 30, cfg) <= 2
        assert 4 <= tiktok_watch.watch_for("", 30, cfg) <= 9           # caption not read


def test_instagram_keeps_what_it_follows_and_only_markets_from_for_you():
    post = {"text": "landlord prank gone wrong", "calls": []}
    assert insta_watch.keeps(post)
    assert not insta_watch.keeps({**post, "for_you": "reels"})
    assert insta_watch.keeps({**post, "for_you": "reels",
                              "heard": "and that is why I bought more bitcoin"})
    assert insta_watch.keeps({**post, "for_you": "explore",
                              "calls": [{"symbol": "DOGE-USD", "direction": 1, "phrase": "x"}]})


def test_the_browser_cookies_reach_yt_dlp_in_the_netscape_format():
    text = insta_watch.jar([
        {"name": "sessionid", "value": "abc", "domain": ".instagram.com", "path": "/",
         "expires": 1893456000.5, "secure": True, "httpOnly": True},
        {"name": "rur", "value": "x", "domain": "www.instagram.com", "path": "/",
         "expires": -1, "secure": False},
    ])
    lines = text.splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    assert lines[1].split("\t") == [".instagram.com", "TRUE", "/", "TRUE", "1893456000",
                                    "sessionid", "abc"]
    assert lines[2].split("\t") == ["www.instagram.com", "FALSE", "/", "FALSE", "0", "rur", "x"]

