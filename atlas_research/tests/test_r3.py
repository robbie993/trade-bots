"""R3 mechanics on synthetic data only (PREREG_R3.md: no historical runs)."""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from atlas_research.r3 import forward as F
from atlas_research.r3 import options_book as OB
from atlas_research.r3 import rules as R


class FakeFeed:
    def __init__(self, seed=0, n_stocks=60, days=700):
        rng = np.random.default_rng(seed)
        idx = pd.bdate_range("2024-01-02", periods=days)
        syms = [f"S{i:02d}" for i in range(n_stocks)] + R.MOM_ETFS
        drift = rng.normal(0.0004, 0.0004, len(syms))
        r = rng.normal(drift, 0.015, size=(days, len(syms)))
        self.closes = pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=idx, columns=syms)
        self.dvol = self.closes * 2e6
        self.stocks = syms[:n_stocks]
        self.events = []
        for s in self.stocks:
            for k in range(60, days, 63):
                d = idx[k + int(rng.integers(0, 10))] if k + 10 < days else None
                if d is not None:
                    self.events.append({"symbol": s, "filed": str(d.date()),
                                        "after_close": bool(rng.random() < 0.5)})
        self.unis = {}

    def has_universe(self, q):
        return q in self.unis

    def universe(self, q):
        self.unis.setdefault(q, list(self.stocks))
        return self.unis[q], False

    def bars(self, symbols, start, end):
        m = (self.closes.index >= pd.Timestamp(start)) & (self.closes.index <= pd.Timestamp(end))
        cols = [s for s in symbols if s in self.closes.columns]
        return self.closes.loc[m, cols], self.dvol.loc[m, cols]

    def earnings_events(self, symbols, since):
        return [e for e in self.events if e["filed"] >= since.isoformat()]

    def _spy(self, day):
        return float(self.closes["SPY"].asof(pd.Timestamp(day)))

    def option_chain(self, day):
        S = self._spy(day)
        out = []
        exp = day + timedelta(days=90)
        for k in np.arange(0.80, 1.05, 0.025):
            K = round(S * k, 1)
            px, d = OB.bs_call(S, K, 90 / 365, 0.18)
            out.append({"symbol": f"SPY{exp:%y%m%d}C{int(K * 1000):08d}", "expiry": exp.isoformat(),
                        "strike": K, "bid": px * 0.99, "ask": px * 1.01, "delta": d})
        return out

    def option_quote(self, occ, day):
        exp = f"20{occ[3:5]}-{occ[5:7]}-{occ[7:9]}"
        K = int(occ[10:]) / 1000
        T = max((date.fromisoformat(exp) - day).days, 0) / 365
        px, d = OB.bs_call(self._spy(day), K, T, 0.18)
        return {"bid": px * 0.99, "ask": px * 1.01, "delta": d}


def _args(**kw):
    return argparse.Namespace(**{"accept_change": "", **kw})


@pytest.fixture
def fwd(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DIR", tmp_path)
    return tmp_path


def test_batch_equals_incremental(fwd, tmp_path_factory, monkeypatch):
    feed = FakeFeed()
    days = feed.closes.index
    start = days[450]
    F.init(_args(start=str(start.date())))
    F.run(_args(), feed=feed, today=days[-1].date())
    batch = json.loads((fwd / "state.json").read_text())

    other = tmp_path_factory.mktemp("inc")
    monkeypatch.setattr(F, "DIR", other)
    F.init(_args(start=str(start.date())))
    feed2 = FakeFeed()
    for d in days[450::37].tolist() + [days[-1]]:
        F.run(_args(), feed=feed2, today=d.date())
    inc = json.loads((other / "state.json").read_text())
    for k in batch["books"]:
        assert batch["books"][k]["history"] == inc["books"][k]["history"], k
    assert batch["c_book"]["history"] == inc["c_book"]["history"]


def test_books_behave(fwd):
    feed = FakeFeed(seed=3)
    days = feed.closes.index
    F.init(_args(start=str(days[450].date())))
    st = F.run(_args(), feed=feed, today=days[-1].date())
    a, b, c = st["books"]["R3-A"], st["books"]["R3-B"], st["c_book"]
    assert a["trades"] > 0 and b["trades"] > 0 and c["trades"] > 0
    for k, bk in st["books"].items():
        assert max(x[2] for x in bk["history"]) <= 1.0 + 1e-6, k   # stock books never levered
    assert st["books"]["SPY"]["history"][-1][1] > 0
    # R3-A always keeps at least its 50% SPY core once invested
    assert a["weights"].get("SPY", 0) >= R.A_CORE - 0.05
    # R3-B never holds more than 20 names
    assert len([s for s in b["weights"] if s != "SPY"]) <= R.B_SLOTS
    # R3-C premium stays under the cap (marked at mid, so allow the spread)
    held = sum(p["qty"] * 100 * feed.option_quote(o, days[-1].date())["ask"] for o, p in c["positions"].items())
    assert held <= R.C_PREMIUM_CAP * c["equity"] * 1.3


def test_refuses_changed_rules(fwd, monkeypatch):
    feed = FakeFeed()
    days = feed.closes.index
    F.init(_args(start=str(days[450].date())))
    monkeypatch.setattr(F, "rules_hash", lambda: "different")
    with pytest.raises(SystemExit):
        F.run(_args(), feed=feed, today=days[500].date())
    st = F.run(_args(accept_change="test: bug fix"), feed=feed, today=days[500].date())
    assert st["change_log"][0]["reason"] == "test: bug fix"


def test_no_lookahead_in_stock_decisions():
    from atlas_research.r3 import strategies as S
    from atlas_research.r3.books import WeightBook
    feed = FakeFeed(seed=5)
    c, v = feed.closes, feed.dvol
    t = 500
    view = S.View(day=c.index[t], closes=c.iloc[: t + 1], dollar_volume=v.iloc[: t + 1],
                  universe=feed.stocks)
    a = WeightBook(name="a")
    S.decide_A(a, view)
    c2 = c.copy()
    c2.iloc[t + 1:] *= 2
    view2 = S.View(day=c2.index[t], closes=c2.iloc[: t + 1], dollar_volume=v.iloc[: t + 1],
                   universe=feed.stocks)
    b = WeightBook(name="b")
    S.decide_A(b, view2)
    assert a.pending == b.pending


def test_report_runs(fwd):
    feed = FakeFeed(seed=2)
    days = feed.closes.index
    F.init(_args(start=str(days[300].date())))
    st = F.run(_args(), feed=feed, today=days[-1].date())
    s = F.summary(st)
    assert set(s["books"]) == {"R3-A", "R3-B", "R3-C", "SPY", "QQQ", "MOM"}
    assert s["books"]["SPY"]["up_capture"] == pytest.approx(1.0)
