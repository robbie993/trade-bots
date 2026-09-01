"""The crucible and the walk-forward lock.

These tests assert the *mechanism*, never a verdict. Whether a genome passes a
gauntlet on a seeded random walk is a fact about the seed; whether the evolver
can reach the holdout window, whether a mutated genome keeps its certificate,
and whether a live venue refuses an uncertified firm are facts about this
system, and they are what has to hold.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.money import D
from src.trading.brain.crucible import (
    Crucible,
    NotEnoughHistory,
    Window,
    WindowedFeed,
    split,
)
from src.trading.brain.evolver import BASE_GENOME, Evolver
from src.trading.brain.lock import (
    GenomeNotCertified,
    WalkForwardLock,
    genome_fingerprint,
)
from src.trading.config import CrucibleConfig, DataConfig, TradingConfig
from src.trading.data.feeds import SyntheticFeed
from src.trading.data.market_data import MarketData


@pytest.fixture
def long_config(tmp_path):
    """Four hundred bars — wide enough that each fold means something."""
    return TradingConfig(
        firms_config=tmp_path / "firms.yaml",
        audit_vault=tmp_path / "vault",
        vendor_dir=tmp_path / "vendor",
        data=DataConfig(source="synthetic", seed=12345, history_days=400),
    )


@pytest.fixture
def long_feed(long_config):
    return SyntheticFeed(seed=long_config.data.seed, days=long_config.data.history_days)


# =========================================================================
# the blindfold
# =========================================================================
def test_a_windowed_feed_has_no_bars_outside_its_window(long_feed):
    whole = long_feed.series("SPY")
    windowed = WindowedFeed(long_feed, Window(0, 100))
    bars = windowed.series("SPY")
    assert len(bars) == 100
    assert bars[-1].as_of == whole[99].as_of
    # Not "refuses to return" — does not have. There is no other method.
    assert not hasattr(windowed, "future")


def test_market_data_built_on_a_window_cannot_reach_past_it(long_feed):
    data = MarketData(WindowedFeed(long_feed, Window(0, 120)), ["SPY"])
    assert data.length() == 120
    data.seek(500)  # ask for a bar well beyond the window
    assert len(data.history("SPY")) == 120


def test_the_training_window_of_every_fold_stops_where_its_test_window_starts():
    folds = split(400, folds=3, test_pct=D("0.15"), warmup=30)
    assert [f.test.start for f in folds] == [220, 280, 340]
    for fold in folds:
        assert fold.train.end == fold.test.start
    # contiguous, and the last fold runs to the final bar
    assert folds[-1].test.end == 400
    assert folds[0].test.end == folds[1].test.start


def test_rolling_folds_drop_the_oldest_bars_and_anchored_ones_keep_them():
    anchored = split(400, folds=3, test_pct=D("0.15"), warmup=30, anchored=True)
    rolling = split(400, folds=3, test_pct=D("0.15"), warmup=30, anchored=False)
    assert [f.train.start for f in anchored] == [0, 0, 0]
    assert [f.train.start for f in rolling] == [0, 60, 120]


def test_the_test_slice_carries_a_lead_in_that_is_never_traded():
    fold = split(400, folds=3, test_pct=D("0.15"), warmup=30)[0]
    assert fold.test_slice.start == fold.test.start - 30
    assert fold.test_slice.end == fold.test.end


def test_a_history_too_short_for_the_schedule_refuses_rather_than_shrinks():
    with pytest.raises(NotEnoughHistory) as exc:
        split(80, folds=3, test_pct=D("0.15"), warmup=30)
    assert "80 bars cannot support" in str(exc.value)
    assert "TRADE_HISTORY_DAYS" in str(exc.value)  # says how to fix it


# =========================================================================
# the gauntlet
# =========================================================================
def test_evolution_inside_a_fold_never_sees_the_test_window(
    store, firm_record, long_config, long_feed
):
    """The load-bearing test: what the evolver was handed, bar for bar."""
    seen: list = []

    class SpyEvolver(Evolver):
        def compete(self, firm, market, generation=1, analysts=(), backtester=None):
            seen.append(market.length())
            return super().compete(firm, market, generation, analysts, backtester)

    crucible = Crucible(store, long_config)
    crucible.evolver = SpyEvolver(store, crucible._evo_config)
    crucible.run(firm_record, long_feed, symbols=["SPY"], generations=1)

    assert seen == [220, 280, 340]  # each fold's training window, and nothing more
    assert max(seen) < 400


def test_the_gauntlet_scores_out_of_sample_and_says_so(
    store, firm_record, long_config, long_feed
):
    report = Crucible(store, long_config).run(
        firm_record, long_feed, symbols=["SPY"], generations=1
    )
    assert len(report.folds) == 3
    assert all(f.out_of_sample is not None for f in report.folds)
    # Every out-of-sample run covers exactly its unseen window, not the lead-in.
    for fold in report.folds:
        assert fold.out_of_sample.bars == fold.fold.test.bars
    assert "out of sample" in report.summary()


def test_zero_generations_freezes_the_genome_and_still_measures_it(
    store, firm_record, long_config, long_feed
):
    crucible = Crucible(store, long_config)
    report = crucible.run(firm_record, long_feed, symbols=["SPY"], generations=0)
    assert report.generations == 0
    # Nothing evolved: what came out is what went in.
    assert report.genome == crucible.evolver.normalise(firm_record.genome)
    assert all(f.in_sample is not None for f in report.folds)


def test_the_same_seed_and_the_same_data_reach_the_same_verdict(
    store, firm_record, long_config, long_feed
):
    first = Crucible(store, long_config).run(firm_record, long_feed, symbols=["SPY"])
    second = Crucible(store, long_config).run(firm_record, long_feed, symbols=["SPY"])
    assert genome_fingerprint(first.genome) == genome_fingerprint(second.genome)
    assert first.passed == second.passed
    assert first.reasons == second.reasons


def test_a_collapse_from_training_to_holdout_is_named_as_overfitting(
    store, firm_record, long_config
):
    """The decay rule, exercised on a fold whose numbers are known."""
    from src.trading.backtest import BacktestResult
    from src.trading.brain.crucible import Fold, FoldResult

    crucible = Crucible(store, long_config)
    result = FoldResult(
        fold=Fold(1, Window(0, 100), Window(100, 130), 30),
        genome=dict(BASE_GENOME),
        in_sample=BacktestResult(firm_key="x", return_pct=D("40"), closed_trades=20),
        out_of_sample=BacktestResult(
            firm_key="x", return_pct=D("1"), closed_trades=20, max_drawdown_pct=D("1")
        ),
    )
    failures = crucible.judge_fold(result)
    assert any("overfitting" in why for why in failures)
    assert result.decay_pct > D("70")


def test_a_fold_with_no_trades_is_unproven_not_passed(store, firm_record, long_config):
    from src.trading.backtest import BacktestResult
    from src.trading.brain.crucible import Fold, FoldResult

    crucible = Crucible(store, long_config)
    result = FoldResult(
        fold=Fold(1, Window(0, 100), Window(100, 130), 30),
        genome=dict(BASE_GENOME),
        in_sample=BacktestResult(firm_key="x", return_pct=D("5"), closed_trades=20),
        out_of_sample=BacktestResult(firm_key="x", return_pct=D("5"), closed_trades=0),
    )
    failures = crucible.judge_fold(result)
    assert any("unproven, not passed" in why for why in failures)


# =========================================================================
# certificates
# =========================================================================
def _pass_report(firm, source="csv", bars=400):
    """A passing report, built directly — the verdict logic is tested above."""
    from src.trading.backtest import BacktestResult
    from src.trading.brain.crucible import CrucibleReport, Fold, FoldResult

    good = BacktestResult(
        firm_key=firm.firm_key,
        return_pct=D("6"),
        max_drawdown_pct=D("3"),
        closed_trades=12,
        sharpe=D("1.2"),
    )
    report = CrucibleReport(
        firm_key=firm.firm_key,
        genome=dict(firm.genome),
        symbols=list(firm.universe),
        data_source=source,
        total_bars=bars,
    )
    report.folds = [
        FoldResult(
            fold=Fold(1, Window(0, 300), Window(300, 400), 30),
            genome=dict(firm.genome),
            in_sample=good,
            out_of_sample=good,
        )
    ]
    return report


def test_a_certificate_records_a_failure_as_well_as_a_pass(
    store, firm_record, long_config, long_feed
):
    lock = WalkForwardLock(store, long_config)
    report = Crucible(store, long_config).run(firm_record, long_feed, symbols=["SPY"])
    certificate = lock.certify(report, firm_record)
    assert certificate.id is not None
    assert certificate.verdict == report.passed
    stored = lock.certificates(firm_record.firm_key)
    assert stored and stored[0].fingerprint == certificate.fingerprint
    if not report.passed:
        assert stored[0].reasons  # the refusal is quotable, not just a boolean


def test_a_certificate_is_bound_to_the_genome_not_the_firm(store, firm_record, long_config):
    lock = WalkForwardLock(store, long_config)
    lock.certify(_pass_report(firm_record), firm_record)
    assert lock.certificate_for(firm_record.genome, firm_record.firm_key) is not None

    mutated = dict(firm_record.genome)
    mutated["fast_window"] = int(mutated.get("fast_window", 10)) + 1
    assert lock.certificate_for(mutated, firm_record.firm_key) is None


def test_a_live_firm_that_mutates_after_certification_is_locked_out_again(
    store, firm_record, long_config
):
    lock = WalkForwardLock(store, long_config)
    lock.certify(_pass_report(firm_record), firm_record)
    store.update_firm_fields(firm_record.id, venue="alpaca")
    live = store.require_firm_by_id(firm_record.id)
    assert lock.check(live).allowed

    store.update_firm_fields(
        firm_record.id, genome=json.dumps({**firm_record.genome, "trend_bias": 61})
    )
    mutated = store.require_firm_by_id(firm_record.id)
    decision = lock.check(mutated)
    assert not decision.allowed
    assert "no passing crucible certificate" in decision.reason
    with pytest.raises(GenomeNotCertified):
        lock.require(mutated)


def test_a_proof_on_the_synthetic_feed_licenses_nothing(store, firm_record, long_config):
    lock = WalkForwardLock(store, long_config)
    lock.certify(_pass_report(firm_record, source="synthetic"), firm_record)
    store.update_firm_fields(firm_record.id, venue="alpaca")
    live = store.require_firm_by_id(firm_record.id)
    decision = lock.check(live)
    assert not decision.allowed
    assert "random walk" in decision.reason


def test_a_stale_certificate_stops_licensing_the_genome(store, firm_record, long_config):
    lock = WalkForwardLock(store, long_config)
    certificate = lock.certify(_pass_report(firm_record), firm_record)
    store.update_firm_fields(firm_record.id, venue="alpaca")
    live = store.require_firm_by_id(firm_record.id)
    assert lock.check(live).allowed

    store.db.execute(
        "UPDATE genome_certificates SET created_at = ? WHERE id = ?",
        ("2020-01-01T00:00:00Z", certificate.id),
    )
    decision = lock.check(live)
    assert not decision.allowed
    assert "past the 90-day limit" in decision.reason


def test_a_paper_firm_is_never_gated(store, firm_record, long_config):
    decision = WalkForwardLock(store, long_config).check(firm_record)
    assert decision.allowed
    assert "gates live venues only" in decision.reason


def test_turning_the_lock_off_says_so_in_the_decision(store, firm_record, long_config):
    config = replace(long_config, crucible=CrucibleConfig(required_for_live=False))
    store.update_firm_fields(firm_record.id, venue="alpaca")
    live = store.require_firm_by_id(firm_record.id)
    decision = WalkForwardLock(store, config).check(live)
    assert decision.allowed
    assert "unproven" in decision.reason


# =========================================================================
# what it stops
# =========================================================================
def test_the_evolver_will_not_promote_a_live_firms_genome(
    store, firm_record, long_config, long_feed
):
    store.update_firm_fields(firm_record.id, venue="alpaca")
    live = store.require_firm_by_id(firm_record.id)
    before = dict(live.genome)

    market = MarketData(long_feed, live.universe)
    generation = Evolver(store, long_config).evolve(live, market, generation=1)
    after = store.require_firm_by_id(firm_record.id).genome

    assert after == before
    assert not generation.promoted
    # It still recorded what it found — the refusal is not silence.
    assert store.db.query("SELECT * FROM strategy_genomes WHERE firm_id = ?", (firm_record.id,))


def test_a_paper_firm_still_evolves_normally(store, firm_record, long_config, long_feed):
    market = MarketData(long_feed, firm_record.universe)
    generation = Evolver(store, long_config).evolve(firm_record, market, generation=1)
    assert generation.winner is not None
    # Promotion is still earned, but it is not blocked by the lock.
    assert Evolver(store, long_config)._live_promotion_block(firm_record) == ""


def test_a_live_firm_with_no_certificate_is_refused_before_it_can_propose(ecosystem):
    """The tick's own gate, not the venue's approval gate."""
    record = ecosystem.store.firms()[0]
    ecosystem.store.update_firm_fields(record.id, venue="alpaca")
    report = ecosystem.tick()
    assert any("crucible certificate" in refusal for refusal in report.refused_by_venue)
    # Nothing was sent anywhere: the firm never reached the venue.
    assert not ecosystem.store.fills(record.id)


def test_certify_promotes_only_on_a_pass(ecosystem, monkeypatch):
    record = ecosystem.store.firms()[0]
    before = dict(record.genome)

    failed = _pass_report(record)
    failed.reasons = ["the final fold failed"]
    monkeypatch.setattr(ecosystem.crucible, "run", lambda *a, **k: failed)
    report, certificate = ecosystem.certify(record.firm_key, promote=True)
    assert not report.passed
    assert ecosystem.store.require_firm_by_id(record.id).genome == before

    passing = _pass_report(record)
    passing.genome = {**before, "trend_bias": 71}
    monkeypatch.setattr(ecosystem.crucible, "run", lambda *a, **k: passing)
    report, certificate = ecosystem.certify(record.firm_key, promote=True)
    assert report.passed
    assert ecosystem.store.require_firm_by_id(record.id).genome["trend_bias"] == 71
    assert certificate.verdict


def test_a_pass_on_the_synthetic_feed_does_not_promote_a_live_firm(ecosystem, monkeypatch):
    """Passing is not the same as being licensed, and promotion needs the licence."""
    record = ecosystem.store.firms()[0]
    ecosystem.store.update_firm_fields(record.id, venue="alpaca")
    record = ecosystem.store.require_firm_by_id(record.id)
    before = dict(record.genome)

    passing = _pass_report(record, source="synthetic")
    passing.genome = {**before, "trend_bias": 99}
    monkeypatch.setattr(ecosystem.crucible, "run", lambda *a, **k: passing)

    report, certificate = ecosystem.certify(record.firm_key, promote=True)
    assert report.passed and certificate.verdict
    assert ecosystem.store.require_firm_by_id(record.id).genome == before
    # And the refusal is on the record, not just absent from the firm row.
    events = ecosystem.store.events(event_type="crucible")
    assert any("NOT promoted" in e["detail"] for e in events)
