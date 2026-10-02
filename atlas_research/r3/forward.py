"""The R3 forward paper ledger.

    python -m atlas_research.r3.forward init --start 2026-10-05   # once, the first trading day after merge
    python -m atlas_research.r3.forward run                       # any day after 16:20 New York; catches up
    python -m atlas_research.r3.forward report                    # where the books stand vs SPY

State lives in ATLAS_FORWARD_DIR (default ./atlas_forward). `run` processes
every trading day since the last one it finished, so missed days are filled
in from the same bars. It refuses to run if the frozen rule code changed,
unless `--accept-change "<reason>"` records why (bug fixes only; see
PREREG_R3.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.rates import TBILL_3M_ANNUAL_PCT
from . import options_book as OB
from . import rules as R
from . import strategies as S
from .books import WeightBook, stock_cost

HERE = Path(__file__).parent
FROZEN_FILES = ["rules.py", "strategies.py", "books.py", "options_book.py"]
DIR = Path(os.environ.get("ATLAS_FORWARD_DIR", "atlas_forward"))
BOOKS = {
    "R3-A": ("R3-A relative strength + SPY core", S.decide_A),
    "R3-B": ("R3-B earnings-reaction drift", S.decide_B),
    "SPY": ("SPY buy and hold", S.decide_hold("SPY")),
    "QQQ": ("QQQ buy and hold", S.decide_hold("QQQ")),
    "MOM": ("12-month ETF momentum", S.decide_momentum),
}


def rules_hash() -> str:
    h = hashlib.sha256()
    for f in FROZEN_FILES:
        h.update((HERE / f).read_bytes())
    return h.hexdigest()


def quarter(d) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def daily_cash(d) -> float:
    pct = TBILL_3M_ANNUAL_PCT.get(d.year, TBILL_3M_ANNUAL_PCT[max(TBILL_3M_ANNUAL_PCT)])
    return (1 + pct / 100) ** (1 / 252) - 1


def load(dirp: Path) -> dict:
    return json.loads((dirp / "state.json").read_text())


def save(dirp: Path, st: dict) -> None:
    tmp = dirp / "state.json.tmp"
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(dirp / "state.json")


def init(args):
    DIR.mkdir(parents=True, exist_ok=True)
    if (DIR / "state.json").exists():
        sys.exit("already initialised; the forward clock cannot be restarted")
    st = {"rules_hash": rules_hash(), "forward_start": args.start,
          "initialised": datetime.now().isoformat(timespec="seconds"),
          "books": {k: WeightBook(name=v[0]).to_dict() for k, v in BOOKS.items()},
          "c_book": OB.OptionBook().to_dict(), "events": [], "late_universes": [],
          "change_log": []}
    save(DIR, st)
    print(f"initialised; forward start {args.start}; rules {st['rules_hash'][:12]}")


def _first_weekday_of_quarter(d: date) -> date:
    x = date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x


def _event_day0(e, idx: pd.DatetimeIndex):
    d = pd.Timestamp(e["filed"])
    i = idx.searchsorted(d)
    if i < len(idx) and e["after_close"] and idx[i] == d:
        i += 1
    return idx[i] if i < len(idx) else None


def run(args, feed=None, today: date | None = None):
    st = load(DIR)
    h = rules_hash()
    if h != st["rules_hash"]:
        if not args.accept_change:
            sys.exit("frozen R3 code changed since the forward start; refusing. If this is a bug fix "
                     "that makes the code match PREREG_R3.md, rerun with --accept-change \"<reason>\"")
        st["change_log"].append({"at": datetime.now().isoformat(timespec="seconds"),
                                 "old": st["rules_hash"], "new": h, "reason": args.accept_change})
        st["rules_hash"] = h
    if feed is None:
        from .feed import LiveFeed
        feed = LiveFeed(DIR / "cache")
    today = today or date.today()
    start = pd.Timestamp(st["forward_start"])
    last = pd.Timestamp(st["books"]["SPY"]["last_day"]) if st["books"]["SPY"]["last_day"] else None

    q_now = quarter(today)
    if not feed.has_universe(q_now) and q_now not in st["late_universes"] and \
            today > _first_weekday_of_quarter(today):
        st["late_universes"].append(q_now)
    universe_now, _ = feed.universe(q_now)
    held = {s for b in st["books"].values() for s in b["weights"]}
    symbols = set(universe_now) | set(R.MOM_ETFS) | held
    hist_from = (last or start) - timedelta(days=620)
    closes, dvol = feed.bars(symbols, hist_from.date(), today)
    idx = closes.index
    todo = [d for d in idx if d >= start and (last is None or d > last) and d.date() <= today]
    if not todo:
        print("nothing new to process")
        return st

    new_events = feed.earnings_events(sorted(universe_now), (todo[0] - timedelta(days=140)).date())
    seen = {(e["symbol"], e["filed"]) for e in st["events"]}
    st["events"] += [e for e in new_events if (e["symbol"], e["filed"]) not in seen]
    events = []
    for e in st["events"]:
        d0 = _event_day0(e, idx)
        if d0 is not None:
            events.append({"symbol": e["symbol"], "day0": d0})

    books = {k: WeightBook.from_dict(v) for k, v in st["books"].items()}
    cb = OB.OptionBook.from_dict(st["c_book"])
    spy = closes["SPY"]
    spy_r = spy.pct_change()
    for d in todo:
        i = idx.get_loc(d)
        q = quarter(d)
        had = feed.has_universe(q)
        uni, _ = feed.universe(q)
        first_of_q = [x for x in idx if quarter(x) == q][0]
        if not had and first_of_q.date() < today and q not in st["late_universes"]:
            # snapshotted after the quarter began: today's list stands in for it
            st["late_universes"].append(q)
        view = S.View(day=d, closes=closes.iloc[: i + 1], dollar_volume=dvol.iloc[: i + 1],
                      universe=uni, events=[e for e in events if e["day0"] <= d])
        rets = (closes.iloc[i] / closes.iloc[i - 1] - 1).dropna().to_dict() if i > 0 else {}
        cost_of = stock_cost(view.large())
        cash = daily_cash(d)
        for k, b in books.items():
            b.earn(str(d.date()), rets, cash)
            b.fill(cost_of)
            BOOKS[k][1](b, view)
            b.record(str(d.date()))
        sma = spy.iloc[max(0, i - R.C_TREND_SMA + 1): i + 1].mean()
        vol = float(spy_r.iloc[max(1, i - 20): i + 1].std() * np.sqrt(252)) or 0.18
        OB.step(cb, feed, d.date(), float(spy.iloc[i]), float(sma), vol, cash)

    st["books"] = {k: b.to_dict() for k, b in books.items()}
    st["c_book"] = cb.to_dict()
    st["last_run"] = datetime.now().isoformat(timespec="seconds")
    save(DIR, st)
    write_ledgers(st)
    print(f"processed {len(todo)} day(s), {todo[0].date()} to {todo[-1].date()}")
    return st


def write_ledgers(st):
    for k, b in st["books"].items():
        pd.DataFrame(b["history"], columns=["day", "equity", "invested"]).to_csv(DIR / f"ledger_{k}.csv", index=False)
    pd.DataFrame(st["c_book"]["history"], columns=["day", "equity", "delta_exposure"]).to_csv(
        DIR / "ledger_R3-C.csv", index=False)


def summary(st) -> dict:
    eq = {k: pd.Series({d: e for d, e, _ in b["history"]}) for k, b in st["books"].items()}
    eq["R3-C"] = pd.Series({d: e for d, e, _ in st["c_book"]["history"]})
    spy = eq["SPY"]
    out = {"forward_start": st["forward_start"], "days": len(spy), "books": {}}
    for k, s in eq.items():
        if len(s) < 2:
            continue
        s = s.reindex(spy.index).ffill()
        r, b = s.pct_change().dropna(), spy.pct_change().dropna()
        tot = s.iloc[-1] / s.iloc[0] - 1
        n = len(r)
        cagr = (1 + tot) ** (252 / n) - 1 if n else 0.0
        dd = float((s / s.cummax() - 1).min())
        mo = pd.DataFrame({"r": r, "b": b})
        mo.index = pd.to_datetime(mo.index)
        m = mo.groupby([mo.index.year, mo.index.month]).apply(lambda x: pd.Series({
            "r": (1 + x.r).prod() - 1, "b": (1 + x.b).prod() - 1}))
        up, dn = m[m.b > 0], m[m.b < 0]
        cash = np.array([daily_cash(pd.Timestamp(d)) for d in r.index])
        ex = r.values - cash
        sharpe = float(ex.mean() / ex.std(ddof=1) * np.sqrt(252)) if n > 2 and ex.std(ddof=1) > 0 else None
        roll = None
        if n > 252:
            a = (1 + r).rolling(252).apply(np.prod, raw=True) - (1 + b).rolling(252).apply(np.prod, raw=True)
            roll = float((a.dropna() > 0).mean())
        out["books"][k] = {
            "total_return": float(tot), "cagr": float(cagr), "max_dd": dd, "sharpe": sharpe,
            "rolling_12m_excess_positive_share": roll,
            "up_capture": float(up.r.mean() / up.b.mean()) if len(up) else None,
            "down_capture": float(dn.r.mean() / dn.b.mean()) if len(dn) else None,
            "trades": (st["books"].get(k) or st["c_book"])["trades"],
        }
    return out


def report(args):
    st = load(DIR)
    s = summary(st)
    (DIR / "summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps(s, indent=1))


def main():
    try:
        from src import dotenv   # the village's .env loader: names only, never values
        dotenv.load()
    except ImportError:
        pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--start", required=True)
    r = sub.add_parser("run")
    r.add_argument("--accept-change", default="")
    sub.add_parser("report")
    a = ap.parse_args()
    {"init": init, "run": run, "report": report}[a.cmd](a)


if __name__ == "__main__":
    main()
