"""R3 decision rules for the stock books (A, B) and the ETF benchmarks.

Each `decide_*` reads only `View`, which holds history up to and including
the decision close and nothing later, and sets `book.pending` when it wants
to trade at the next close.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import rules as R


@dataclass
class View:
    """Everything known at the close of `day`."""
    day: pd.Timestamp
    closes: pd.DataFrame            # dates x symbols, up to and including `day`
    dollar_volume: pd.DataFrame     # same shape
    universe: list                  # this quarter's S&P 500 snapshot
    events: list = field(default_factory=list)   # [{symbol, day0, ...}], day0 <= day

    def first_of_month(self) -> bool:
        idx = self.closes.index
        return len(idx) < 2 or idx[-2].month != idx[-1].month

    def is_friday(self) -> bool:
        return self.day.weekday() == 4

    def sma(self, n: int) -> pd.Series:
        c = self.closes.iloc[-n:]
        return c.mean() if len(c) == n else pd.Series(np.nan, index=self.closes.columns)

    def ret(self, n: int, skip: int = 0) -> pd.Series:
        c = self.closes
        if len(c) <= n:
            return pd.Series(np.nan, index=c.columns)
        return c.iloc[-1 - skip] / c.iloc[-1 - n] - 1

    def liquid(self) -> list:
        px = self.closes.iloc[-1]
        dv = self.dollar_volume.iloc[-R.DOLLAR_VOLUME_DAYS:].mean()
        return [s for s in self.universe
                if s in px.index and px.get(s, 0) >= R.MIN_PRICE and dv.get(s, 0) >= R.MIN_DOLLAR_VOLUME]

    def large(self) -> set:
        dv = self.dollar_volume.iloc[-R.DOLLAR_VOLUME_DAYS:].mean()
        names = [s for s in dv.sort_values(ascending=False).index if s in self.universe][:R.LARGE_RANK]
        return set(names) | set(R.MOM_ETFS)


# -- R3-A ------------------------------------------------------------------

def a_sleeve(v: View) -> dict:
    names = v.liquid()
    r = v.ret(R.A_LOOKBACK, R.A_SKIP)
    spy_r = r.get("SPY", np.nan)
    rs = r[names].dropna()
    if rs.empty:
        return {}
    cut = rs.quantile(R.A_PERCENTILE)
    sma = v.sma(R.A_TREND_SMA)
    px = v.closes.iloc[-1]
    ok = [s for s in rs.index if rs[s] >= cut and rs[s] > spy_r and px[s] > sma[s]]
    ok = sorted(ok, key=lambda s: -rs[s])[: R.A_NAMES]
    if not ok:
        return {}
    daily = v.closes[ok].iloc[-R.A_VOL_DAYS - 1:].pct_change().iloc[1:]
    vol = daily.std().replace(0, np.nan).fillna(daily.std().max() or 0.3)
    w = (1 / vol) / (1 / vol).sum()
    # cap at 10% of the sleeve; whatever the cap removes goes to SPY
    w = w.clip(upper=R.A_NAME_CAP)
    return w.to_dict()


def decide_A(book, v: View) -> None:
    sleeve = 1 - R.A_CORE
    if v.first_of_month() or not book.weights:
        target = {"SPY": R.A_CORE}
        for s, w in a_sleeve(v).items():
            target[s] = target.get(s, 0) + w * sleeve
        target["SPY"] += max(0.0, 1 - sum(target.values()))
        book.pending = target
    elif v.is_friday():
        sma = v.sma(R.A_TREND_SMA)
        px = v.closes.iloc[-1]
        broken = [s for s in book.weights if s != "SPY" and px.get(s, np.inf) < sma.get(s, -np.inf)]
        if broken:
            target = dict(book.weights)
            for s in broken:
                target["SPY"] = target.get("SPY", 0) + target.pop(s)
            book.pending = target


# -- R3-B ------------------------------------------------------------------

def reactions(v: View) -> list:
    """(symbol, day0, reaction) for events whose day +1 is on or before `day`."""
    idx = v.closes.index
    out = []
    for e in v.events:
        d0 = pd.Timestamp(e["day0"])
        i0 = idx.searchsorted(d0)
        if i0 >= len(idx) or i0 < 1 or i0 + 1 >= len(idx) or e["symbol"] not in v.closes.columns:
            continue
        c = v.closes
        r = c[e["symbol"]].iloc[i0 + 1] / c[e["symbol"]].iloc[i0 - 1] - 1
        m = c["SPY"].iloc[i0 + 1] / c["SPY"].iloc[i0 - 1] - 1
        if np.isfinite(r) and np.isfinite(m):
            out.append({"symbol": e["symbol"], "day0": str(d0.date()), "reaction": float(r - m),
                        "decided": str(idx[i0 + 1].date())})
    return out


def decide_B(book, v: View) -> None:
    idx = v.closes.index
    today = str(v.day.date())
    entries = book.memo.setdefault("entries", {})      # symbol -> entry decision day
    window_start = idx[max(0, len(idx) - R.B_REACTION_WINDOW)]
    rx = [x for x in reactions(v) if pd.Timestamp(x["decided"]) >= window_start]
    pool = [x["reaction"] for x in rx]
    changed = False

    # exits: held 60 trading days from the fill (decision day + 1)
    for s, d in list(entries.items()):
        held = len(idx[idx > pd.Timestamp(d)])
        if held >= R.B_HOLD_DAYS:
            entries.pop(s)
            changed = True

    todays = [x for x in rx if x["decided"] == today]
    if todays and pool:
        cut = float(np.quantile(pool, 1 - R.B_TOP_SHARE))
        liquid = set(v.liquid())
        sma = v.sma(R.B_TREND_SMA)
        px = v.closes.iloc[-1]
        r1 = v.ret(R.B_ACCEL_DAYS)
        c = v.closes
        r2 = (c.iloc[-1 - R.B_ACCEL_DAYS] / c.iloc[-1 - 2 * R.B_ACCEL_DAYS] - 1) if len(c) > 2 * R.B_ACCEL_DAYS else r1 * np.nan
        for x in sorted(todays, key=lambda x: -x["reaction"]):
            s = x["symbol"]
            if len(entries) >= R.B_SLOTS or s in entries or s not in liquid:
                continue
            if x["reaction"] >= R.B_MIN_REACTION and x["reaction"] >= cut \
                    and r1.get(s, np.nan) > r2.get(s, np.nan) and px[s] > sma[s]:
                entries[s] = today
                changed = True

    if changed or not book.weights:
        target = {}
        for s in entries:
            # an existing position keeps its drifted weight; a new one gets 5%
            target[s] = book.weights.get(s, R.B_SLOT_WEIGHT) if entries[s] != today else R.B_SLOT_WEIGHT
        target["SPY"] = max(0.0, 1 - sum(target.values()))
        book.pending = target


# -- benchmarks ------------------------------------------------------------

def decide_hold(symbol):
    def decide(book, v: View) -> None:
        if not book.weights and book.pending is None:
            book.pending = {symbol: 1.0}
    return decide


def decide_momentum(book, v: View) -> None:
    if not (v.first_of_month() or not book.weights):
        return
    r = v.ret(R.MOM_LOOKBACK, R.MOM_SKIP)[[s for s in R.MOM_ETFS if s in v.closes.columns]].dropna()
    top = r.sort_values(ascending=False).index[: R.MOM_TOP]
    book.pending = {s: 1 / R.MOM_TOP for s in top if r[s] > 0}
