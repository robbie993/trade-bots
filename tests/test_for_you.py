"""The For You scroll: what Instagram and TikTok pick for the village account.
The browser half needs a signed-in Edge; these pin the parsing it relies on."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts import insta_watch, tiktok_watch

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
