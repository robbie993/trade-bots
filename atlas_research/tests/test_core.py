"""Run with: python -m pytest atlas_research/tests -q

Synthetic prices only, so these run anywhere without the data panel.
"""
from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from atlas_research.data.prices import ETFS
from atlas_research.evolution.gauntlet import judge
from atlas_research.evolution.genome import Genome, random_genome
from atlas_research.portfolio.engine import Market, backtest, target
from atlas_research.validation import metrics as M


def synthetic(days=2600, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2003-01-01", periods=days)
    r = rng.normal(0.0003, 0.012, size=(days, len(ETFS)))
    p = pd.DataFrame(np.cumprod(1 + r, axis=0), index=idx, columns=ETFS)
    p.iloc[:300, ETFS.index("HYG")] = np.nan   # a late-listing ETF
    return p


@pytest.fixture(scope="module")
def market():
    return Market(synthetic())


def test_no_lookahead_in_decisions(market):
    """The book decided at day t must not change if every later price changes."""
    p = market.prices
    rng = random.Random(1)
    for _ in range(4):
        g = random_genome(rng)
        for t in (700, 1300, 2100):
            future = p.copy()
            future.iloc[t + 1:] = future.iloc[t + 1:] * np.exp(
                np.random.default_rng(t).normal(0, 0.05, future.iloc[t + 1:].shape))
            a, _ = target(market, g, t)
            b, _ = target(Market(future), g, t)
            assert np.allclose(a, b), (g.key(), t)


def test_trades_one_day_after_decision(market):
    """Day-one return is cash: nothing can be held before the first trade."""
    run = backtest(market, Genome(), "2005-01-03", "2006-12-29")
    assert run.exposure[0] == 0.0
    assert run.ret[0] == pytest.approx(run.cash[0])


def test_never_levered(market):
    rng = random.Random(2)
    for _ in range(5):
        run = backtest(market, random_genome(rng), "2004-06-01", "2012-12-31")
        assert run.weights.sum(axis=1).max() <= 1 + 1e-9
        assert run.weights.min() >= 0


def test_ineligible_asset_never_held(market):
    run = backtest(market, Genome(top_n=6, position_cap=1.0), "2004-01-02", "2004-12-31")
    hyg = ETFS.index("HYG")
    first_ok = np.flatnonzero(market.eligible[:, hyg])[0]
    held = run.weights[:, hyg] > 0
    days = market.dates.get_indexer(run.dates) - 1
    assert not held[days < first_ok].any()


def test_cash_cannot_win():
    """The idler: a book that sits in cash fails the gates and has Sharpe 0."""
    n = 2520
    cash = np.full(n, 0.0002)
    spy = np.random.default_rng(3).normal(0.0004, 0.012, n)
    v = judge(cash.copy(), spy, cash, np.zeros(n), trades=0, turnover=0.0)
    assert v.stats.sharpe == 0.0
    assert not v.passed
    assert not v.gates["enough_exposure"] and not v.gates["enough_trades"]


def test_metrics_on_known_series():
    r = np.array([0.10, -0.50, 0.20])
    assert M.max_drawdown(r) == pytest.approx(-0.5)
    assert M.cagr(np.full(252, (1.1) ** (1 / 252) - 1)) == pytest.approx(0.10, rel=1e-6)


def test_r2_no_lookahead_and_gross_cap(market):
    from atlas_research.r2.engine import MAX_GROSS, backtest as r2bt
    from atlas_research.r2.families import DEFAULTS, R2Market, TARGETS
    from atlas_research.r2.search import rand
    rm = R2Market(market)
    p = market.prices
    rng = random.Random(4)
    for fam in TARGETS:
        for g in [DEFAULTS[fam]] + [rand(fam, rng) for _ in range(3)]:
            for t in (900, 1800):
                future = p.copy()
                future.iloc[t + 1:] *= 1.3
                a, sa = TARGETS[fam](rm, g, t)
                b, sb = TARGETS[fam](R2Market(Market(future)), g, t)
                assert np.allclose(a, b) and sa == sb
            run = r2bt(rm, fam, g, "2005-01-03", "2011-12-30")
            # trades land at <= 120%; one day of drift may sit above before the trim
            assert run.weights.sum(axis=1).max() <= MAX_GROSS * 1.08
            assert run.weights.min() >= 0
