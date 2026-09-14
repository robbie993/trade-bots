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
