"""The validation harness — the separate path, and its seal.

Same rule as the rest: assert the mechanism, never a verdict. These tests do
not download anything. They write CSVs in the village's own format to a temp
directory, which is also the path a person takes who has data from somewhere
other than yfinance — so exercising it here keeps that path honest.

The load-bearing test in this file is
``test_a_default_run_never_reads_a_sealed_bar``. Everything else in the harness
is convenience; the seal is the only thing standing between "we tuned the
scouts until the number looked good" and evidence.
"""

from __future__ import annotations

import csv

import pytest

from src.money import D

from hive_mind.crucible_real import (
    Split,
    buy_and_hold,
    dataset_fingerprint,
    looks_so_far,
    append_log,
)
from hive_mind.evolver import BOUNDS, create_random_genome
from hive_mind.lock import LockConfig, WalkForwardLock
from hive_mind.market import MarketFeed, WindowFeed
from hive_mind.real_feed import (
    RealDataMissing,
    RealFeed,
    real_stress_windows,
    stress_source,
)

YEARS = 20
BARS = YEARS * 252


def write_csv(directory, name, bars, column="close"):
    path = directory / f"{name}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for bar in bars:
            value = bar.vix if column == "vix" else None
            writer.writerow(
                [
                    bar.as_of.strftime("%Y-%m-%d"),
                    value or bar.open,
                    value or bar.high,
                    value or bar.low,
                    value or bar.close,
                    0 if value else int(bar.volume),
                ]
            )
    return path


@pytest.fixture
def market_dir(tmp_path):
    """Twenty years of bars on disk, in the format `src/trading/data` reads."""
    feed = MarketFeed(
        seed=4242,
        plan=[
            ("calm_bull", 900),
            ("chop", 700),
            ("crash", 120),
            ("rebound", 300),
            ("melt_up", 600),
            ("rate_shock", 400),
            ("grind_down", 300),
            ("calm_bull", 720),
        ],
    )
    bars = feed.bars()
    directory = tmp_path / "market"
    directory.mkdir()
    write_csv(directory, "SPY", bars)
    write_csv(directory, "VIX", bars, column="vix")
    return directory


@pytest.fixture
def real(market_dir):
    return RealFeed(directory=market_dir)


# =========================================================================
# a blindfold you cannot take off by splitting it
# =========================================================================
def test_a_sub_window_can_only_ever_be_narrower():
    """The crucible splits whatever feed it is handed, including a window."""
    feed = MarketFeed(seed=5, plan=[("chop", 400)])
    window = WindowFeed(feed, 100, 200)

    inner = window.window(10, 50)
    assert len(inner) == 40
    assert inner.bars()[0].close == feed.bars()[110].close

    # Asking for more than the window holds does not hand back more.
    greedy = window.window(0, 10_000)
    assert len(greedy) == 100
    assert greedy.bars()[-1].close == feed.bars()[199].close
    assert all(b.index < 200 for b in greedy.bars())

    # Nor does asking from before it started.
    backwards = window.window(-50, 20)
    assert backwards.bars()[0].close == feed.bars()[100].close


# =========================================================================
# what a price feed does not come with
# =========================================================================
def test_a_missing_vix_is_a_refusal_not_a_column_of_zeros(market_dir):
    (market_dir / "VIX.csv").unlink()
    feed = RealFeed(directory=market_dir)
    with pytest.raises(RealDataMissing) as exc:
        feed.bars()
    assert "dead code" in str(exc.value)
    assert "allow_vix_proxy" in str(exc.value)


def test_the_vix_proxy_is_available_but_never_silent(market_dir):
    (market_dir / "VIX.csv").unlink()
    feed = RealFeed(directory=market_dir, allow_vix_proxy=True)
    bars = feed.bars()
    assert "PROXY" in feed.vix_source
    assert "PROXY" in feed.describe()
    assert any(b.vix > 0 for b in bars)


def test_real_vix_is_used_when_it_is_there(real, market_dir):
    bars = real.bars()
    assert "real ^VIX" in real.vix_source
    rows = list(csv.DictReader((market_dir / "VIX.csv").open()))
    assert bars[500].vix == D(rows[500]["close"])


def test_sentiment_says_it_is_a_proxy_every_time(real):
    real.bars()
    assert "PROXY" in real.sentiment_source
    assert "not news" in real.sentiment_source
    assert "not news" in real.describe()


def test_the_bars_carry_real_dates_not_day_numbers(real):
    bars = real.bars()
    assert bars[0].day.count("-") == 2  # 1993-01-29, not "Day 1"
    assert bars[0].as_of < bars[-1].as_of
    assert bars[0].regime == ""  # a real tape does not come labelled


# =========================================================================
# the stress windows
# =========================================================================
def test_stress_windows_are_slices_of_the_real_tape(real):
    windows = real_stress_windows(real, 8, span=252)
    assert len(windows) == 8
    for label, feed, seed in windows:
        assert len(feed) == 252
        assert "-" in label  # named by the years it covers
    # Deterministic, and spread across the history rather than piled up.
    again = real_stress_windows(real, 8, span=252)
    assert [w[0] for w in windows] == [w[0] for w in again]
    assert len({w[0] for w in windows}) > 1


def test_the_lock_uses_the_windows_it_is_given(real):
    lock = WalkForwardLock(LockConfig(stress_runs=5), stress_markets=stress_source(real))
    markets = lock.stress_markets()
    assert len(markets) == 5
    assert all(len(feed) == 252 for _, feed, _ in markets)
    # And without the hook it falls back to the generated scenarios.
    assert WalkForwardLock(LockConfig(stress_runs=5)).stress_markets()[0][0] in {
        "bear_2022",
        "chop_2015",
        "covid_2020",
        "gfc_2008",
        "melt_up_2021",
        "quiet_2017",
    }


# =========================================================================
# the seal
# =========================================================================
def test_the_split_seals_the_tail_and_the_windows_do_not_overlap_it(real):
    split = Split(real, holdout_years=5, forward_years=3)
    bars = real.bars()

    assert len(split.sealed) == 5 * 252
    assert len(split.forward) == 3 * 252
    assert len(split.history) == len(bars) - 8 * 252

    last_usable = list(split.forward)[-1].as_of
    first_sealed = list(split.sealed)[0].as_of
    assert last_usable < first_sealed
    assert all(b.as_of < first_sealed for b in split.history)


def test_a_history_too_short_for_the_seal_refuses(market_dir):
    feed = RealFeed(directory=market_dir)
    with pytest.raises(RealDataMissing) as exc:
        Split(feed, holdout_years=15, forward_years=5)
    assert "Load more history" in str(exc.value)


def test_a_default_run_never_reads_a_sealed_bar(real, monkeypatch):
    """The one that matters: what the pipeline was actually handed, bar for bar."""
    import hive_mind.lock as lock_module

    split = Split(real, holdout_years=5, forward_years=3)
    seal_starts_at = split.forward_end
    seen: list = []

    real_backtest = lock_module.backtest

    def spy(genome, feed, **kwargs):
        seen.extend(b.index for b in feed)
        return real_backtest(genome, feed, **kwargs)

    monkeypatch.setattr(lock_module, "backtest", spy)

    lock = WalkForwardLock(
        LockConfig(stress_runs=4, generations=1, population=2),
        seed=1,
        stress_markets=stress_source(real),
    )
    lock.prove(create_random_genome(1), split.history, split.forward, verbose=False)

    assert seen, "the pipeline ran nothing"
    assert max(seen) < seal_starts_at, (
        f"a sealed bar (index >= {seal_starts_at}) reached the pipeline"
    )


def test_the_stress_windows_stay_out_of_the_seal_too(real):
    """Phase 2b draws from the whole tape, so it needs the seal applied first."""
    split = Split(real, holdout_years=5, forward_years=3)
    usable = real.window(0, split.forward_end)
    windows = real_stress_windows(usable, 10, span=252)
    for _, feed, _ in windows:
        assert all(b.index < split.forward_end for b in feed)


# =========================================================================
# counting the looks
# =========================================================================
def test_the_dataset_fingerprint_names_this_exact_tape(real, market_dir):
    first = dataset_fingerprint(real)
    assert first == dataset_fingerprint(RealFeed(directory=market_dir))

    rows = list((market_dir / "SPY.csv").open())
    (market_dir / "SPY.csv").write_text("".join(rows[:-50]))
    shorter = RealFeed(directory=market_dir)
    assert dataset_fingerprint(shorter) != first


def test_looks_are_counted_per_dataset_and_holdout_spends_separately(tmp_path):
    assert looks_so_far(tmp_path, "abc") == 0
    append_log(tmp_path, {"dataset": "abc", "spent_holdout": False})
    append_log(tmp_path, {"dataset": "abc", "spent_holdout": True})
    append_log(tmp_path, {"dataset": "other", "spent_holdout": False})

    assert looks_so_far(tmp_path, "abc") == 2
    assert looks_so_far(tmp_path, "abc", holdout=True) == 1
    assert looks_so_far(tmp_path, "other") == 1


def test_the_log_survives_a_corrupt_line(tmp_path):
    append_log(tmp_path, {"dataset": "abc", "spent_holdout": False})
    with (tmp_path / ".crucible_log.jsonl").open("a") as handle:
        handle.write("{not json\n")  # a whole bad line
    append_log(tmp_path, {"dataset": "abc", "spent_holdout": False})
    assert looks_so_far(tmp_path, "abc") == 2


def test_a_write_cut_off_mid_line_does_not_swallow_the_next_record(tmp_path):
    """A count that silently drops looks is worse than no count at all."""
    append_log(tmp_path, {"dataset": "abc", "spent_holdout": False})
    with (tmp_path / ".crucible_log.jsonl").open("a") as handle:
        handle.write('{"dataset": "abc", "spent_h')  # killed mid-write
    append_log(tmp_path, {"dataset": "abc", "spent_holdout": False})
    assert looks_so_far(tmp_path, "abc") == 2


# =========================================================================
# the null it has to beat
# =========================================================================
def test_buy_and_hold_is_measured_on_the_same_window(real):
    split = Split(real, holdout_years=5, forward_years=3)
    baseline = buy_and_hold(split.forward)
    bars = list(split.forward)
    expected = (bars[-1].close - bars[0].close) / bars[0].close * D(100)
    assert baseline["return_pct"] == pytest.approx(expected, abs=D("0.01"))
    assert baseline["max_drawdown_pct"] >= 0


# =========================================================================
# random genomes
# =========================================================================
def test_a_random_genome_is_seeded_and_inside_its_bounds():
    assert create_random_genome(7).fingerprint() == create_random_genome(7).fingerprint()
    assert create_random_genome(7).fingerprint() != create_random_genome(8).fingerprint()
    for seed in range(20):
        genome = create_random_genome(seed)
        for name, (low, high) in BOUNDS.items():
            assert low <= getattr(genome, name) <= high
