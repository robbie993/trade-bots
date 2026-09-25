"""The YouTube watcher's rules: names become tickers, calls not mentions,
one video one voice, old television does not vote, and a rate limit stops it."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vw = _load("scripts/video_watch.py", "video_watch_under_test")
SYMBOLS = ["BTC-USD", "NVDA", "TLT", "SPY", "ETH-USD"]


def _video(text, hours_ago=1, title=""):
    at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"title": title, "text": text, "published": at.isoformat()}


def test_spoken_names_are_heard_as_tickers():
    calls = vw.calls_in(_video("honestly I'm buying bitcoin here and selling Nvidia"), SYMBOLS)
    got = {(c["symbol"], c["direction"]) for c in calls}
    assert ("BTC-USD", 1) in got and ("NVDA", -1) in got


def test_a_mention_is_not_a_call():
    assert vw.calls_in(_video("bitcoin and Nvidia were in the news today"), SYMBOLS) == []


def test_one_video_is_one_voice():
    v = _video("buy bitcoin. buy bitcoin. seriously, buy bitcoin")
    v["calls"] = vw.calls_in(v, SYMBOLS)
    assert len(v["calls"]) == 1
    (r,) = vw.readings([v], max_age_hours=24)
    assert r["symbol"] == "BTC-USD" and r["confidence"] == 12.0


def test_agreeing_videos_raise_confidence_and_a_split_says_nothing():
    a, b = _video("buy bitcoin"), _video("buying bitcoin now")
    c = _video("sell ethereum")
    d = _video("buy ethereum")
    for v in (a, b, c, d):
        v["calls"] = vw.calls_in(v, SYMBOLS)
    out = {r["symbol"]: r for r in vw.readings([a, b, c, d], max_age_hours=24)}
    assert out["BTC-USD"]["confidence"] == 24.0
    assert "ETH-USD" not in out


def test_old_television_does_not_vote():
    v = _video("buy bitcoin", hours_ago=30)
    v["calls"] = vw.calls_in(v, SYMBOLS)
    assert vw.readings([v], max_age_hours=24) == []


def test_a_429_stops_the_run_instead_of_asking_again():
    with pytest.raises(vw.RateLimited):
        vw._raise_if_limited(RuntimeError("HTTP Error 429: Too Many Requests"))
    vw._raise_if_limited(RuntimeError("members only"))          # not a rate limit


def _scanner(tmp_path, readings, age_h=0.1):
    mod = _load("bots/video_calls.py", "video_calls_under_test")
    mod.SNAPSHOT = tmp_path / "youtube_calls.json"
    at = datetime.now(timezone.utc) - timedelta(hours=age_h)
    mod.SNAPSHOT.write_text(json.dumps({"fetched_at": at.isoformat(),
                                        "payload": {"readings": readings}}))
    return mod


def test_the_scanner_repeats_the_watchers_readings(tmp_path):
    r = [{"symbol": "BTC-USD", "score": 100.0, "confidence": 24.0, "note": "YouTube: 2 buy"}]
    assert _scanner(tmp_path, r).scan(None) == r


def test_the_scanner_is_silent_when_the_pc_stopped_watching(tmp_path):
    r = [{"symbol": "BTC-USD", "score": 100.0, "confidence": 24.0}]
    assert _scanner(tmp_path, r, age_h=5).scan(None) == {}
