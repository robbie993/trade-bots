"""Where the data comes from, and the scale it is on.

Nothing here touches the network. Every provider is exercised against a
recorded response, which is also the only way these tests can mean anything:
a test that passes when a vendor is up and fails when it is down is a test of
the vendor.

The load-bearing test is ``test_a_genome_may_not_be_run_on_a_scale_it_was_not
_measured_on``. Everything else is plumbing; that one is the difference
between a certificate and a decoration.
"""

from __future__ import annotations

import json

import pytest

from hive_mind.engine import GodBrokerEngine
from hive_mind.evolver import BASE_GENOME
from hive_mind.lock import LockConfig, ProfileMismatch, WalkForwardLock
from hive_mind.market import MarketFeed
from hive_mind.news import (
    _parse_rss,
    calibrate,
    collect,
    read_series,
    score_text,
)
from hive_mind.providers import (
    AUTO_ORDER,
    PROVIDERS,
    ProviderError,
    Row,
    fetch,
    from_alphavantage,
    from_csv,
    from_stooq,
    from_tiingo,
    write_csv,
)
from hive_mind.real_feed import RealFeed

STOOQ_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2020-01-02,323.54,324.89,322.53,324.87,59151200\n"
    "2020-01-03,321.16,323.64,321.10,322.41,77709700\n"
    "2020-01-06,320.49,323.73,320.36,323.64,55653900\n"
)


# =========================================================================
# providers
# =========================================================================
def test_stooq_parses_a_recorded_response(monkeypatch):
    monkeypatch.setattr("hive_mind.providers._get", lambda url, headers=None: STOOQ_CSV.encode())
    rows = from_stooq("SPY", "2020-01-01", "2020-01-31")
    assert len(rows) == 3
    assert rows[0].date == "2020-01-02"
    assert rows[0].close == pytest.approx(324.87)
    assert rows[-1].volume == pytest.approx(55653900)


def test_stooq_refuses_a_response_that_is_not_a_csv(monkeypatch):
    """The failure mode that matters: an error page with a 200 on it."""
    monkeypatch.setattr(
        "hive_mind.providers._get", lambda url, headers=None: b"<html>Exceeded</html>"
    )
    with pytest.raises(ProviderError) as exc:
        from_stooq("SPY", "2020-01-01", "2020-01-31")
    assert "did not return a CSV" in str(exc.value)


def test_tiingo_needs_a_key_and_says_which_one():
    with pytest.raises(ProviderError) as exc:
        from_tiingo("SPY", "2020-01-01", "2020-01-31", api_key="")
    assert "TIINGO_API_KEY" in str(exc.value)


def test_tiingo_parses_a_recorded_response(monkeypatch):
    payload = json.dumps(
        [
            {
                "date": "2020-01-02T00:00:00.000Z",
                "open": 323.54,
                "high": 324.89,
                "low": 322.53,
                "close": 324.87,
                "volume": 59151200,
            }
        ]
    ).encode()
    monkeypatch.setattr("hive_mind.providers._get", lambda url, headers=None: payload)
    rows = from_tiingo("SPY", "2020-01-01", "2020-01-31", api_key="x")
    assert rows[0].date == "2020-01-02"
    assert rows[0].close == pytest.approx(324.87)


def test_alphavantage_treats_a_quota_note_as_a_failure(monkeypatch):
    """It answers a blown quota with HTTP 200 and prose — the worst shape."""
    payload = json.dumps({"Note": "Thank you for using Alpha Vantage! Our standard..."}).encode()
    monkeypatch.setattr("hive_mind.providers._get", lambda url, headers=None: payload)
    with pytest.raises(ProviderError) as exc:
        from_alphavantage("SPY", "2020-01-01", "2020-01-31", api_key="x")
    assert "no series" in str(exc.value)
    assert "Thank you" in str(exc.value)


def test_the_csv_provider_reads_a_file_you_already_have(tmp_path):
    path = tmp_path / "kaggle.csv"
    path.write_text(STOOQ_CSV)
    rows = from_csv("SPY", "2020-01-01", "2020-01-31", source=path)
    assert len(rows) == 3
    windowed = from_csv("SPY", "2020-01-03", "2020-01-03", source=path)
    assert len(windowed) == 1


def test_a_csv_with_a_broken_row_loses_the_row_not_the_file(tmp_path):
    path = tmp_path / "messy.csv"
    path.write_text(STOOQ_CSV + "2020-01-07,not,a,number,here\n2020-01-08,1,2,0.5,1.5,10\n")
    rows = from_csv("SPY", "2020-01-01", "2020-12-31", source=path)
    assert len(rows) == 4
    assert rows[-1].date == "2020-01-08"


def test_auto_falls_through_to_the_next_provider(tmp_path, monkeypatch, capsys):
    calls = []

    def broken(symbol, start, end, **kwargs):
        calls.append("stooq")
        raise ProviderError("stooq is having a day")

    def working(symbol, start, end, **kwargs):
        calls.append("yfinance")
        return [Row("2020-01-02", 1.0, 2.0, 0.5, 1.5, 100)]

    monkeypatch.setitem(PROVIDERS, "stooq", broken)
    monkeypatch.setitem(PROVIDERS, "yfinance", working)
    result = fetch("SPY", provider="auto", directory=tmp_path)

    assert calls == ["stooq", "yfinance"]
    assert result["provider"] == "yfinance"
    assert (tmp_path / "SPY.csv").exists()


def test_when_every_provider_refuses_it_says_what_to_do(tmp_path, monkeypatch):
    for name in AUTO_ORDER:
        monkeypatch.setitem(
            PROVIDERS, name, lambda *a, n=name, **k: (_ for _ in ()).throw(ProviderError(n))
        )
    with pytest.raises(ProviderError) as exc:
        fetch("SPY", provider="auto", directory=tmp_path)
    assert "--provider csv" in str(exc.value)
    assert all(name in str(exc.value) for name in AUTO_ORDER)


def test_a_cached_file_is_not_refetched(tmp_path, monkeypatch):
    write_csv([Row("2020-01-02", 1.0, 2.0, 0.5, 1.5, 100)], tmp_path / "SPY.csv")
    monkeypatch.setitem(
        PROVIDERS, "stooq", lambda *a, **k: (_ for _ in ()).throw(AssertionError("refetched"))
    )
    result = fetch("SPY", provider="auto", directory=tmp_path, on_note=lambda *_: None)
    assert result["provider"] == "cache"


def test_what_a_provider_writes_is_what_the_feed_reads(tmp_path):
    """The whole point of the shared format, asserted end to end."""
    rows = [
        Row(f"2020-{m:02d}-01", 100.0 + m, 101.0 + m, 99.0 + m, 100.5 + m, 1_000_000)
        for m in range(1, 13)
    ]
    write_csv(rows, tmp_path / "SPY.csv")
    write_csv([Row(r.date, 15, 15, 15, 15, 0) for r in rows], tmp_path / "VIX.csv")

    bars = RealFeed(directory=tmp_path).bars()
    assert len(bars) == 12
    assert bars[0].day == "2020-01-01"
    assert float(bars[-1].close) == pytest.approx(112.5)
    assert float(bars[0].vix) == pytest.approx(15)


# =========================================================================
# the news scorer
# =========================================================================
def test_the_lexicon_reads_direction():
    assert score_text("Stocks surge as rally broadens") > 1
    assert score_text("Wall Street plunges on recession fears") < -1
    assert score_text("Company files quarterly report") == 0


def test_a_negation_flips_the_word_it_negates():
    assert score_text("Fed will cut rates") < score_text("Fed will not cut rates")


def test_rss_and_atom_titles_are_read_without_a_dependency():
    body = (
        "<rss><channel><title>Feed Name</title>"
        "<item><title><![CDATA[Stocks plunge on recession fears]]></title></item>"
        "<item><title>Markets rally to a record high</title></item>"
        "<item><title>hi</title></item>"  # too short to be a headline
        "</channel></rss>"
    )
    headlines = _parse_rss(body, "test")
    assert len(headlines) == 2  # the channel title and the stub are both dropped
    assert headlines[0].score < 0 < headlines[1].score


def test_a_day_with_no_headlines_writes_nothing(tmp_path, monkeypatch):
    """A zero meaning 'the feeds were down' is indistinguishable, once stored."""
    monkeypatch.setattr("hive_mind.news.fetch_headlines", lambda *a, **k: [])
    assert collect(tmp_path, on_note=None) is None
    assert read_series(tmp_path) == []


def test_a_day_is_never_recorded_twice(tmp_path, monkeypatch):
    from hive_mind.news import Headline

    monkeypatch.setattr(
        "hive_mind.news.fetch_headlines",
        lambda *a, **k: [Headline("test", "Markets rally to a record high", 2.0)],
    )
    first = collect(tmp_path, on_note=None)
    assert first is not None and len(read_series(tmp_path)) == 1
    collect(tmp_path, on_note=None)
    assert len(read_series(tmp_path)) == 1


# =========================================================================
# the scale, and what happens when it changes
# =========================================================================
def test_calibrate_calls_two_different_scales_different():
    proxy = [0.1, -0.2, 0.3, 0.05] * 30
    news = [1.4, -1.9, 2.1, 0.4] * 30
    result = calibrate(proxy, news)
    assert not result.interchangeable
    assert result.scale_ratio > 2
    assert "DIFFERENT SCALES" in result.summary()


def test_calibrate_refuses_to_judge_a_short_sample():
    result = calibrate([0.1, -0.1] * 5, [0.1, -0.1] * 5)
    assert not result.interchangeable
    assert "too few days" in result.summary()


def test_a_feed_reports_the_scales_its_numbers_are_on(tmp_path):
    generated = MarketFeed(seed=1, plan=[("chop", 40)]).profile()
    assert generated["sentiment"].startswith("generated:")

    rows = [Row(f"2020-{m:02d}-01", 100, 101, 99, 100.5, 1_000) for m in range(1, 13)]
    write_csv(rows, tmp_path / "SPY.csv")
    write_csv([Row(r.date, 15, 15, 15, 15, 0) for r in rows], tmp_path / "VIX.csv")

    real = RealFeed(directory=tmp_path).profile()
    assert real["vix"] == "real:^VIX"
    assert real["sentiment"].startswith("proxy:")

    without_vix = RealFeed(directory=tmp_path, vix_symbol="ABSENT", allow_vix_proxy=True)
    assert without_vix.profile()["vix"].startswith("proxy:")


def test_a_window_keeps_the_profile_of_the_tape_it_came_from():
    feed = MarketFeed(seed=1, plan=[("chop", 40)])
    assert feed.window(0, 10).profile() == feed.profile()


class NewsScaleFeed(MarketFeed):
    """The same tape, with sentiment now coming from a news scorer."""

    def profile(self) -> dict:
        return {**super().profile(), "sentiment": "news:rss-lexicon-v1"}


@pytest.fixture
def certified_lock():
    history = MarketFeed(seed=7, plan=[("calm_bull", 300), ("chop", 200)])
    forward = MarketFeed(seed=8, plan=[("chop", 120)])
    lock = WalkForwardLock(LockConfig(stress_runs=2, generations=1, population=2), seed=7)
    lock.prove(BASE_GENOME, history, forward, verbose=False)
    return lock, history


def test_a_certificate_records_the_scales_it_was_earned_on(certified_lock):
    lock, history = certified_lock
    assert lock.certified_profile == history.profile()
    assert lock.check_profile(history).allowed


def test_a_genome_may_not_be_run_on_a_scale_it_was_not_measured_on(certified_lock):
    """The expensive mistake: proxy sentiment in training, real news live.

    Nothing about it raises on its own. The thresholds still parse, the engine
    still runs, and the equity curve is quietly answering a different question.
    """
    lock, _ = certified_lock
    live = NewsScaleFeed(seed=9, plan=[("chop", 120)])

    verdict = lock.check_profile(live)
    assert not verdict.allowed
    assert "sentiment" in verdict.reason
    assert "news:rss-lexicon-v1" in verdict.reason

    engine = GodBrokerEngine(BASE_GENOME, seed=1, lock=lock)
    with pytest.raises(ProfileMismatch):
        engine.run(live)


def test_an_uncertified_lock_blocks_nothing(certified_lock):
    """Before anything is proved there is nothing to protect."""
    fresh = WalkForwardLock(LockConfig())
    assert fresh.check_profile(NewsScaleFeed(seed=9, plan=[("chop", 40)])).allowed


def test_a_backtest_is_not_gated_by_the_profile_check():
    """Measuring is always allowed; it is deploying that needs the licence."""
    from hive_mind.engine import backtest

    result = backtest(BASE_GENOME, NewsScaleFeed(seed=9, plan=[("chop", 120)]), seed=1)
    assert result.bars == 120
