"""Does the selection rule rank anything, and is the asking counted?

Six conclusions died in one day and the last three died to this question.
The evolver sorted candidates by in-sample fitness and adopted the top one
for its whole life without anyone checking whether that sort predicted
held-out performance. It does not: Spearman +0.056 on n=280.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.trading.backtest import BacktestResult
from src.trading.research import PValueLedger, spearman


# =========================================================================
# fitness is window-sized, not a rate
# =========================================================================
def test_two_windows_of_different_length_cannot_be_subtracted():
    """`max_drawdown_pct` only grows as a window lengthens, so a long window
    scores worse for reasons unrelated to the genome. The same 40 random
    genomes scored -0.950 over 216 bars and -1.921 over 720 — and that 0.972
    gap was read as "evolution beats random" for about an hour."""
    short = BacktestResult(firm_key="a", bars=216)
    long = BacktestResult(firm_key="a", bars=720)
    assert not short.comparable_with(long)
    with pytest.raises(ValueError, match="not comparable"):
        short.minus(long)


def test_the_same_window_compares_fine():
    # `closed_trades` must clear 10, or the small-sample scaler zeroes both
    # fitnesses and the difference is trivially nothing.
    a = BacktestResult(firm_key="a", bars=216, return_pct=Decimal(4),
                       closed_trades=40)
    b = BacktestResult(firm_key="b", bars=216, return_pct=Decimal(1),
                       closed_trades=40)
    assert a.comparable_with(b)
    assert a.minus(b) == Decimal(3)


def test_an_empty_window_is_never_comparable():
    assert not BacktestResult(firm_key="a", bars=0).comparable_with(
        BacktestResult(firm_key="b", bars=0))


# =========================================================================
# the rank statistic
# =========================================================================
def test_a_rule_that_ranks_perfectly_scores_one():
    rho, p, n = spearman([1, 2, 3, 4, 5, 6], [10, 20, 30, 40, 50, 60])
    assert rho == pytest.approx(1.0)
    assert n == 6


def test_a_rule_that_ranks_backwards_scores_minus_one():
    rho, _, _ = spearman([1, 2, 3, 4, 5, 6], [60, 50, 40, 30, 20, 10])
    assert rho == pytest.approx(-1.0)


def test_noise_scores_near_zero_and_is_not_significant():
    """The village's real answer, in miniature."""
    xs = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3, 2, 3, 8, 4]
    ys = [2, 7, 1, 8, 2, 8, 1, 8, 2, 8, 4, 5, 9, 0, 4, 5, 2, 3, 5, 3]
    rho, p, n = spearman(xs, ys)
    assert abs(rho) < 0.5 and p > 0.05 and n == 20


def test_everything_tied_is_no_ranking_rather_than_a_perfect_one():
    """A cohort where every genome scored identically has not ranked well —
    it has not ranked at all, and must not report rho=1."""
    rho, p, _ = spearman([1, 1, 1, 1], [5, 5, 5, 5])
    assert rho == 0.0 and p == 1.0


def test_too_few_points_returns_no_claim():
    assert spearman([1, 2], [2, 1]) == (0.0, 1.0, 2)


# =========================================================================
# the looks are counted
# =========================================================================
def test_the_twentieth_look_is_read_as_the_twentieth(tmp_path):
    """The whole point. p=0.04 on a first look is interesting; on a twentieth
    it is what twenty looks produce on their own."""
    ledger = PValueLedger(path=str(tmp_path / "l.json"))
    for i in range(19):
        ledger.record("evolver:firm_x", "rank test", 0.4,
                      f"2026-09-{i + 1:02d}")
    ctx = ledger.context("evolver:firm_x", 0.04)
    assert ctx["tests_including_this"] == 20
    assert not ctx["survives_bonferroni_05"], "0.04 on a 20th look is nothing"
    assert ctx["bonferroni_floor"] == pytest.approx(0.0025)


def test_a_rerun_on_the_same_day_is_not_a_new_look(tmp_path):
    """Counting it would punish fixing a bug."""
    ledger = PValueLedger(path=str(tmp_path / "l.json"))
    for _ in range(5):
        ledger.record("evolver:firm_x", "rank test", 0.2, "2026-09-13")
    assert ledger.context("evolver:firm_x", 0.2)["tests_including_this"] == 2


def test_the_village_result_fails_its_own_ledger(tmp_path):
    """rho=+0.056 at n=280 gives p around 0.35 — it would fail on look one,
    let alone against the hundreds of looks the village has taken."""
    rho, p, n = spearman(list(range(280)),
                         [(i * 37) % 280 for i in range(280)])
    ledger = PValueLedger(path=str(tmp_path / "l.json"))
    assert not ledger.context("evolver:firm_x", p)["survives_bonferroni_05"]


# =========================================================================
# stored scores carry the regime they were measured under
# =========================================================================
def test_the_backfill_marks_everything_already_stored_as_pre_fix(db):
    """Rows written before 2026-09-14 measured a different quantity: the
    holdout overlapped the fitted window by up to 42% of its bars, and fitness
    had no benchmark in it. Nothing on the row said so, and subtracting across
    that boundary gives a difference that is mostly the bug.
    """
    from src.db.connection import utcnow_iso

    for i in range(3):
        db.insert("strategy_genomes", {
            "firm_id": None, "generation": 1, "genome": "{}",
            "fitness": i, "trades": 5, "selected": False,
            "notes": "written before the fix", "created_at": utcnow_iso(),
        })
    # `init-db` runs every migration again, which is when the backfill sees them.
    db.init_schema()
    rows = db.query("SELECT epoch, reason FROM genome_epoch")
    assert len(rows) == 3
    assert {r["epoch"] for r in rows} == {1}
    assert all("holdout overlapped" in r["reason"] for r in rows)


def test_the_backfill_is_idempotent_and_never_relabels(db, store):
    """Every migration re-runs on every `init-db`. Migration 024's first draft
    was three bare ALTERs that would have thrown on the second run; this one
    must survive being run repeatedly *and* must not drag an epoch-2 row back
    down to 1.
    """
    from src.db.connection import utcnow_iso
    from src.trading.brain.evolver import MEASUREMENT_EPOCH, Evolver
    from src.trading.config import TradingConfig

    genome_id = db.insert("strategy_genomes", {
        "firm_id": None, "generation": 1, "genome": "{}", "fitness": 1,
        "trades": 5, "selected": False, "notes": "", "created_at": utcnow_iso(),
    })
    Evolver(store, TradingConfig()).mark_epoch(genome_id)
    assert db.query_one(
        "SELECT epoch FROM genome_epoch WHERE genome_id = ?", (genome_id,)
    )["epoch"] == MEASUREMENT_EPOCH

    for _ in range(3):
        db.init_schema()

    rows = db.query("SELECT epoch FROM genome_epoch WHERE genome_id = ?", (genome_id,))
    assert len(rows) == 1, "the backfill duplicated a row"
    assert rows[0]["epoch"] == MEASUREMENT_EPOCH, "the backfill relabelled a fixed row"


def test_a_genome_written_now_is_stamped_with_the_current_epoch(store, firm_record,
                                                                market_data):
    """The stamp has to reach the rows the evolver actually writes, or the
    fence is a table nobody stands behind."""
    from src.trading.brain.evolver import BASE_GENOME, MEASUREMENT_EPOCH, Evolver
    from src.trading.config import BrainConfig, TradingConfig

    evolver = Evolver(store, TradingConfig(brain=BrainConfig(purge_bars=0)))
    firm = store.require_firm_by_id(firm_record.id)
    firm.genome = dict(BASE_GENOME)
    evolver.evolve(firm, market_data, generation=1)

    written = store.db.query("SELECT id FROM strategy_genomes")
    stamped = store.db.query("SELECT genome_id, epoch FROM genome_epoch")
    assert written, "nothing was written"
    assert {r["genome_id"] for r in stamped} == {r["id"] for r in written}, \
        "a stored genome went unstamped, and unmarked must not mean pre-fix"
    assert {r["epoch"] for r in stamped} == {MEASUREMENT_EPOCH}


def test_the_epoch_constant_is_ahead_of_the_backfill():
    """If these ever match, the fence marks nothing: every new row would be
    stamped with the same value the backfill gives the old ones."""
    from src.trading.brain.evolver import MEASUREMENT_EPOCH

    assert MEASUREMENT_EPOCH > 1
