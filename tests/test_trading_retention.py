"""The retention ratio: how much edge survives the delay before acting.

`R(T) = E_a(T) / E_b`. If `T*` — the delay at which surviving edge equals cost
— is longer than the delay the system actually runs at, the design is viable.
If it is shorter, no infrastructure fixes it.
"""

from __future__ import annotations

from decimal import Decimal

from src.trading.retention import Point, Retention, measure


def _ramp(n=40, step="1"):
    """A price series that rises steadily: edge should survive any delay.

    Geometric, not linear. On a linear ramp the *percentage* return shrinks as
    the denominator grows — 4/100 then 4/108 — so a flat edge would look like
    a decaying one, and the test would be measuring arithmetic rather than
    retention.
    """
    out = [Decimal(100)]
    for _ in range(n - 1):
        out.append(out[-1] * Decimal("1.01"))
    return out


def _spike(n=40):
    """Flat except one jump at bar 5: edge exists only if you are early."""
    return [Decimal(100) if i < 5 else Decimal(110) for i in range(n)]


def test_a_slow_edge_survives_delay():
    closes = {"X": _ramp()}
    signals = [("X", i, 1) for i in range(0, 20)]
    r = measure(signals, closes, delays=(0, 1, 4), horizon=4)
    assert r.ratio_at(0) == 1
    assert r.ratio_at(4) > Decimal("0.8"), (
        "a steadily drifting edge must not evaporate over four bars"
    )


def test_an_edge_that_lives_in_one_bar_does_not_survive():
    """The case that kills a village: all the edge is in the first bar."""
    closes = {"X": _spike()}
    signals = [("X", 1, 1)]
    r = measure(signals, closes, delays=(0, 8), horizon=4)
    assert r.ratio_at(0) == 1
    assert r.ratio_at(8) == 0, "the jump is long past by then"


def test_a_short_that_falls_is_a_gain():
    """Sign handling. Getting this backwards inverts the whole curve."""
    falling = {"X": [Decimal(200) - Decimal(i) for i in range(40)]}
    r = measure([("X", 0, -1)], falling, delays=(0,), horizon=4)
    assert r.baseline > 0


def test_the_threshold_is_where_edge_meets_cost():
    r = Retention(cost_bps=Decimal(10))
    r.points = [Point(0, Decimal(50), 100), Point(1, Decimal(30), 100),
                Point(4, Decimal(12), 100), Point(8, Decimal(5), 100)]
    assert r.viable_to == 4, "8 bars is below the 10bps cost floor"


def test_no_edge_anywhere_reports_none_not_zero():
    """A strategy that never clears costs is a finding, not a broken measure."""
    r = Retention(cost_bps=Decimal(10))
    r.points = [Point(0, Decimal(2), 50), Point(1, Decimal(1), 50)]
    assert r.viable_to is None


def test_a_zero_baseline_does_not_manufacture_a_ratio():
    """Dividing by an edge of zero would invent a number out of nothing."""
    r = Retention()
    r.points = [Point(0, Decimal(0), 10), Point(1, Decimal(5), 10)]
    assert r.ratio_at(1) is None


def test_the_horizon_is_fixed_across_delays():
    """Letting the horizon grow with the delay measures a *longer* trade, not
    a later one — and in a drifting market that reads as retained edge."""
    closes = {"X": _ramp()}
    signals = [("X", i, 1) for i in range(10)]
    r = measure(signals, closes, delays=(0, 8), horizon=4)
    a = next(p for p in r.points if p.bars == 0)
    b = next(p for p in r.points if p.bars == 8)
    assert abs(a.edge_bps - b.edge_bps) < Decimal(1), (
        "on a constant-growth ramp a later 4-bar trade earns the same as an "
        "earlier one"
    )


# =========================================================================
# guards that came out of the first real village run
# =========================================================================
def test_a_negative_baseline_has_no_retention_ratio():
    """R(T) on a losing strategy printed 1.10 for an edge that got *worse*.

    The village measured -14.4 bps at zero delay and -15.8 bps one bar later.
    Dividing gave 1.10, which reads as "110% of the edge survived" — the exact
    opposite of what happened. There is no edge to retain, so there is no
    ratio.
    """
    r = Retention(points=[Point(bars=0, edge_bps=Decimal("-14.4"), n=100),
                          Point(bars=1, edge_bps=Decimal("-15.8"), n=100)])
    assert r.ratio_at(1) is None, "a deeper loss is not retained edge"
    assert r.ratio_at(0) is None


def test_one_violent_cluster_is_flagged_rather_than_averaged_away():
    """Mean -14.4 against median -1.9 was one bar, not a village-wide effect.

    22 firms bought DOGE and WIF into an overnight 5% slide. Dropping that
    single bar moved the mean to -2.1 and left the median untouched, which is
    the whole reason the median is carried alongside.
    """
    assert Point(bars=0, edge_bps=Decimal("-14.4"), n=1016,
                 median_bps=Decimal("-1.9")).fragile
    assert not Point(bars=0, edge_bps=Decimal("-2.1"), n=1016,
                     median_bps=Decimal("-1.9")).fragile


def test_the_median_is_reported_next_to_the_mean():
    closes = {"X": [Decimal(100)] * 12 + [Decimal(50)] + [Decimal(100)] * 12}
    # nine ordinary signals and one that runs into the crash
    signals = [("X", i, 1) for i in range(9)] + [("X", 8, 1)]
    r = measure(signals, closes, delays=(0,), horizon=4)
    p = r.points[0]
    assert p.median_bps == 0, "most trades went nowhere"
    assert p.edge_bps < 0, "the mean is dragged by the crash"
    assert p.fragile
