"""The paper loop — the book that has to still be right tomorrow.

Everything else in this package can be re-run from scratch. This is the one
piece holding state across processes, so these tests are mostly about the days
it goes wrong: cron firing twice, cron not firing for a week, a file edited by
hand, a genome pointed at data it was never measured on.
"""

from __future__ import annotations

import csv
import json

import pytest

from src.money import D

from hive_mind.evolver import BASE_GENOME, Genome
from hive_mind.live import BOOK_NAME, Book, LiveRefused, open_book, run, step_once
from hive_mind.lock import strategy_fingerprint
from hive_mind.market import MarketFeed
from hive_mind.real_feed import RealFeed

PLAN = [("calm_bull", 200), ("chop", 120), ("crash", 30), ("rebound", 80)]


def write_tape(directory, bars):
    for name, column in (("SPY", None), ("VIX", "vix")):
        with (directory / f"{name}.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "open", "high", "low", "close", "volume"])
            for bar in bars:
                value = bar.vix if column else None
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


def append_days(directory, extra):
    """Rewrite the tape with `extra` further sessions on the end.

    `extra` is a total, not an increment: the tape is regenerated from the same
    seed each time, so the bars already processed keep their prices.
    """
    longer = MarketFeed(seed=99, plan=PLAN[:-1] + [(PLAN[-1][0], PLAN[-1][1] + extra)])
    write_tape(directory, longer.bars())


@pytest.fixture
def market(tmp_path):
    directory = tmp_path / "market"
    directory.mkdir()
    write_tape(directory, MarketFeed(seed=99, plan=PLAN).bars())
    return directory


def certify(directory, genome=BASE_GENOME, profile=None):
    folder = directory / "certified"
    folder.mkdir(exist_ok=True)
    payload = {
        "genome": {k: str(v) for k, v in genome.to_dict().items()},
        "fingerprint": genome.fingerprint(),
        "certificate": strategy_fingerprint(genome),
        "profile": profile if profile is not None else RealFeed(directory=directory).profile(),
        "dataset": "test",
        "look": 1,
        "spent_holdout": False,
        "certified_at": "2026-09-01T00:00:00Z",
    }
    path = folder / f"{genome.fingerprint()}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


@pytest.fixture
def certified(market):
    certify(market)
    return market


def quiet(*_, **__):
    pass


def start(directory, catch_up=True):
    """Open a book and trade the newest bar, the way a first run does."""
    assert run(directory, "100000", "", False, catch_up, False) == 0
    return Book.load(directory / BOOK_NAME)


# =========================================================================
# the days it goes wrong
# =========================================================================
def test_running_twice_on_the_same_bar_changes_nothing(certified):
    """Cron fires late, cron fires twice. A doubled bar is a doubled position."""
    first = start(certified)
    assert first.steps == 1

    run(certified, "100000", "", False, False, False)
    second = Book.load(certified / BOOK_NAME)

    assert second.steps == 1
    assert second.shares == first.shares
    assert second.cash == first.cash
    assert second.last_bar_index == first.last_bar_index


def test_a_week_of_missed_bars_is_all_processed_in_order(certified):
    before = start(certified)
    append_days(certified, 5)

    run(certified, "100000", "", False, False, False)
    after = Book.load(certified / BOOK_NAME)

    assert after.steps == before.steps + 5
    dates = [json.loads(l)["date"] for l in (certified / "live_journal.jsonl").read_text().splitlines()]
    assert dates == sorted(dates)
    assert len(dates) == after.steps


def test_the_book_survives_the_process_that_wrote_it(certified):
    start(certified)
    append_days(certified, 3)
    run(certified, "100000", "", False, False, False)

    saved = Book.load(certified / BOOK_NAME)
    # Nothing in the loop's memory: this is what a cold start reads back.
    assert saved.shares != 0 or saved.steps == 4
    assert saved.cash > 0
    assert saved.genome_fingerprint == BASE_GENOME.fingerprint()
    assert saved.scouts and len(saved.scouts) == 5
    assert all("confidence" in s for s in saved.scouts)


def test_a_new_book_starts_at_the_end_of_the_tape(certified):
    """Replaying thirty years through a live book is a backtest wearing a
    different hat, and the crucible is where backtests belong."""
    book = start(certified, catch_up=False)
    bars = RealFeed(directory=certified).bars()
    assert book.steps == 0
    assert book.last_bar_index == bars[-1].index
    assert book.equity_curve == []


def test_the_genome_never_moves(certified):
    start(certified)
    for extra in (3, 6, 9, 12):
        append_days(certified, extra)
        run(certified, "100000", "", False, False, False)
    book = Book.load(certified / BOOK_NAME)
    assert book.steps > 5
    assert book.genome_fingerprint == BASE_GENOME.fingerprint()
    assert book.strategy_fingerprint == strategy_fingerprint(BASE_GENOME)


# =========================================================================
# the refusals
# =========================================================================
def test_no_certificate_means_no_loop(market):
    with pytest.raises(LiveRefused) as exc:
        open_book(market, "100000")
    assert "crucible_real" in str(exc.value)


def test_two_certificates_and_it_will_not_pick_for_you(market):
    certify(market)
    certify(market, Genome(**{**BASE_GENOME.to_dict(), "trend_bias": D("0.9")}))
    with pytest.raises(LiveRefused) as exc:
        open_book(market, "100000")
    assert "--genome" in str(exc.value)


def test_an_edited_certificate_licenses_nothing(market):
    path = certify(market)
    payload = json.loads(path.read_text())
    payload["genome"]["leverage_cap"] = "2.90"  # the numbers, not the fingerprint
    path.write_text(json.dumps(payload))

    with pytest.raises(LiveRefused) as exc:
        open_book(market, "100000")
    assert "hash to" in str(exc.value)


def test_an_edited_book_is_refused_before_it_trades(certified):
    start(certified)
    path = certified / BOOK_NAME
    book = json.loads(path.read_text())
    book["genome"]["position_pct"] = "0.49"
    path.write_text(json.dumps(book))

    reloaded = Book.load(path)
    with pytest.raises(LiveRefused) as exc:
        step_once(reloaded, RealFeed(directory=certified), certified, on_note=quiet)
    assert "edited" in str(exc.value)


def test_a_genome_certified_on_other_data_will_not_run(certified):
    """The expensive one: proxy sentiment in training, news sentiment live."""
    book = start(certified)
    book.profile = {**book.profile, "sentiment": "news:rss-lexicon-v1"}
    book.save(certified / BOOK_NAME)
    append_days(certified, 2)

    reloaded = Book.load(certified / BOOK_NAME)
    with pytest.raises(LiveRefused) as exc:
        step_once(reloaded, RealFeed(directory=certified), certified, on_note=quiet)
    assert "certified on different data" in str(exc.value)
    assert "sentiment" in str(exc.value)


def test_a_missing_vix_stops_the_loop_before_it_opens_a_book(market, capsys):
    certify(market)
    (market / "VIX.csv").unlink()
    assert run(market, "100000", "", False, False, False) == 1
    assert "dead code" in capsys.readouterr().out


# =========================================================================
# the record
# =========================================================================
def test_the_journal_is_one_line_per_decision(certified):
    start(certified)
    append_days(certified, 4)
    run(certified, "100000", "", False, False, False)

    lines = (certified / "live_journal.jsonl").read_text().splitlines()
    book = Book.load(certified / BOOK_NAME)
    assert len(lines) == book.steps
    entry = json.loads(lines[-1])
    assert {"date", "action", "equity", "close", "vix", "genome"} <= set(entry)
    assert entry["genome"] == BASE_GENOME.fingerprint()


def test_a_half_written_book_never_replaces_a_good_one(certified, monkeypatch):
    """Written to a temp file and moved, so a crash keeps yesterday's book."""
    start(certified)
    path = certified / BOOK_NAME
    good = path.read_text()

    book = Book.load(path)
    monkeypatch.setattr(
        "pathlib.Path.replace", lambda *a, **k: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        book.save(path)
    assert path.read_text() == good


def test_status_reads_the_book_without_touching_the_feed(certified, capsys):
    start(certified)
    assert run(certified, "100000", "", True, False, False) == 0
    out = capsys.readouterr().out
    assert BASE_GENOME.fingerprint() in out
    assert "equity" in out
