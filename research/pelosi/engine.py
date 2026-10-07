"""Backtest engine for the Pelosi study: prices, entry rules, event study,
copy portfolio (stock-only and options-aware) and metrics.

Conventions
- Total-return prices (adj_close) for stocks and benchmarks.
- Options are MODELED (Black-Scholes on split-adjusted close, vol = 1.1 x
  trailing 63-day realized vol, floor 20%, rate = 13-week T-bill). No
  historical option quotes were available; every options number is a model.
- Entry/exit happen at the close of the chosen trading day.
- Idle cash earns BIL.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

DATA = Path(__file__).parent / "data"


def basket_nav_from(ret: pd.DataFrame) -> pd.Series:
    """Equal-weight basket of the columns, rebalanced on the first day of each month."""
    nav, w, month, out = 1.0, None, None, []
    for d, row in ret.iterrows():
        avail = row.dropna()
        if w is not None and len(w):
            g = float((w * (1 + avail.reindex(w.index).fillna(0))).sum())
            nav *= g
            w = w * (1 + avail.reindex(w.index).fillna(0)) / g
        if w is None or d.month != month:
            w = pd.Series(1 / len(avail), index=avail.index) if len(avail) else None
            month = d.month
        out.append(nav)
    return pd.Series(out, index=ret.index)


class Prices:
    def __init__(self, path=DATA / "pelosi_prices.parquet"):
        p = pd.read_parquet(path)
        p["date"] = pd.to_datetime(p["date"]).dt.tz_localize(None).dt.normalize()
        self.adj = p.pivot_table(index="date", columns="ticker", values="adj_close").sort_index()
        self.close = p.pivot_table(index="date", columns="ticker", values="close").sort_index()
        spl = p.pivot_table(index="date", columns="ticker", values="splits").sort_index().fillna(0)
        spl = spl.where(spl > 0, 1.0)
        # factor F(t): product of split ratios strictly after t -> raw = close * F
        self.split_after = spl[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
        self.cal = self.adj["SPY"].dropna().index
        self.adj = self.adj.reindex(self.cal)
        self.close = self.close.reindex(self.cal)
        self.split_after = self.split_after.reindex(self.cal).fillna(1.0)
        # Tickers whose early history belongs to a different company (see
        # ledger.NO_PRICE): blank those dates so they are never priced.
        for t, before in (("SUNE", "2100-01-01"), ("HTZ", "2021-07-01"), ("AA", "2016-11-01"),
                          ("DOW", "2019-04-01"), ("FB", "2100-01-01")):
            if t in self.adj:
                m = self.adj.index < pd.Timestamp(before)
                self.adj.loc[m, t] = np.nan
                self.close.loc[m, t] = np.nan
        self.ret = self.adj.pct_change(fill_method=None)
        # Mega-cap tech basket (equal weight, monthly rebalance) as a pseudo-ticker.
        self.adj["TECH6"] = basket_nav_from(self.ret[["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA"]])
        self.close["TECH6"] = self.adj["TECH6"]
        self.ret["TECH6"] = self.adj["TECH6"].pct_change()
        lr = np.log(self.close / self.close.shift(1))
        self.vol63 = lr.rolling(63).std() * math.sqrt(252)
        irx = self.close.get("^IRX")
        self.rate = (irx.ffill() / 100.0) if irx is not None else pd.Series(0.02, index=self.cal)

    def has(self, ticker, i) -> bool:
        if ticker not in self.adj:
            return False
        v = self.adj[ticker].iloc[i]
        return bool(np.isfinite(v))

    # ---- calendar helpers
    def idx_on_or_after(self, d) -> int | None:
        i = self.cal.searchsorted(pd.Timestamp(d), side="left")
        return int(i) if i < len(self.cal) else None

    def idx_after(self, d) -> int | None:
        i = self.cal.searchsorted(pd.Timestamp(d), side="right")
        return int(i) if i < len(self.cal) else None


# ---------------------------------------------------------------- entry rules
RULES = {
    # name: (anchor column, calendar-day offset, mode)
    "T+0 (trade date, diagnostic)": ("tdate", 0, "on"),
    "T+1": ("tdate", 1, "on"),
    "T+3": ("tdate", 3, "on"),
    "T+7": ("tdate", 7, "on"),
    "T+14": ("tdate", 14, "on"),
    "T+30": ("tdate", 30, "on"),
    "Public, same day (optimistic)": ("public_date", 0, "on"),
    "Public, next day (realistic)": ("public_date", 0, "after"),
    "Public +1 week": ("public_date", 7, "after"),
    "Public +1 month": ("public_date", 30, "after"),
}


def entry_index(P: Prices, row, rule: str, extra_td: int = 0):
    col, off, mode = RULES[rule]
    d = pd.Timestamp(row[col]) + pd.Timedelta(days=off)
    i = P.idx_on_or_after(d) if mode == "on" else P.idx_after(d)
    if i is None:
        return None
    i += extra_td
    return i if i < len(P.cal) else None


# ---------------------------------------------------------------- options model
def bs_call(S, K, T, r, sig):
    if T <= 0 or sig <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def option_path(P: Prices, ticker, i0, i1, strike_raw, expiry):
    """Modeled value path (per share of the underlying) from i0..i1 inclusive.

    After expiry the position is rolled into the stock at intrinsic value (what
    she does: she exercises). Returns np.array of values, or None if unpriced.
    """
    S = P.close[ticker].values
    F0 = P.split_after[ticker].values[i0]
    K = strike_raw / F0                       # strike in today's split-adjusted units
    sig0 = P.vol63[ticker].values[i0]
    sig0 = 0.35 if not np.isfinite(sig0) else sig0
    exp = pd.Timestamp(expiry) if pd.notna(expiry) else P.cal[i0] + pd.Timedelta(days=365)
    vals = []
    rolled = None
    adj = P.adj[ticker].values
    for i in range(i0, i1 + 1):
        s = S[i]
        if not np.isfinite(s):
            return None
        if rolled is not None:
            vals.append(rolled * adj[i])
            continue
        T = (exp - P.cal[i]).days / 365.0
        sig = P.vol63[ticker].values[i]
        sig = max(0.20, 1.1 * (sig if np.isfinite(sig) else sig0))
        r = float(P.rate.values[i]) if np.isfinite(P.rate.values[i]) else 0.02
        if T <= 0:
            iv = max(s - K, 0.0)
            vals.append(iv)
            rolled = iv / adj[i] if iv > 0 else 0.0
            continue
        vals.append(bs_call(s, K, T, r, sig))
    return np.array(vals)


# ---------------------------------------------------------------- event study
def event_returns(P: Prices, events: pd.DataFrame, rule: str, horizons=(21, 63, 126, 252),
                  bench=("SPY", "QQQ"), extra_td=0, cost_bps=10.0):
    rows = []
    for _, e in events.iterrows():
        t = e.px_ticker
        i = entry_index(P, e, rule, extra_td)
        rec = {"eventId": e.eventId, "ticker": t, "kind": e.kind, "tdate": e.tdate,
               "public_date": e.public_date, "mid_usd": e.mid_usd, "entry": None}
        if i is None or not P.has(t, i):
            rec["status"] = "no price" if i is not None else "after data end"
            rows.append(rec)
            continue
        rec["entry"] = P.cal[i]
        rec["status"] = "ok"
        for h in horizons:
            j = i + h
            if j >= len(P.cal) or not np.isfinite(P.adj[t].iloc[j]):
                continue
            r = P.adj[t].iloc[j] / P.adj[t].iloc[i] - 1 - cost_bps / 1e4
            rec[f"r{h}"] = r
            for b in bench:
                rb = P.adj[b].iloc[j] / P.adj[b].iloc[i] - 1
                rec[f"x{h}_{b}"] = r - rb
        rows.append(rec)
    return pd.DataFrame(rows)


def cluster_boot(x: pd.Series, clusters: pd.Series, n=2000, seed=0):
    """Mean and 95% CI, resampling filings (clusters) with replacement."""
    ok = x.notna()
    x, clusters = x[ok], clusters[ok]
    if len(x) < 3:
        return (x.mean() if len(x) else np.nan, np.nan, np.nan)
    g = pd.DataFrame({"x": x.values, "c": clusters.astype(str).values}).groupby("c").x.agg(["sum", "count"])
    s, c = g["sum"].values, g["count"].values
    rng = np.random.default_rng(seed)
    k = len(g)
    idx = rng.integers(0, k, size=(n, k))
    means = s[idx].sum(1) / c[idx].sum(1)
    return (x.mean(), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


# ---------------------------------------------------------------- portfolio
@dataclass
class Pos:
    ticker: str
    i0: int
    i1: int
    usd: float
    path: np.ndarray            # value multiplier path, path[0] = 1
    kind: str
    eventId: str = ""
    is_opt: bool = False


def build_positions(P: Prices, led: pd.DataFrame, rule: str, *, use_options=False,
                    hold_td: int | None = None, extra_td: int = 0, weight="usd",
                    buys=("buy_stock", "buy_call"), exit_on_sale=True, end_i=None):
    end_i = len(P.cal) - 1 if end_i is None else end_i
    sells = led[led.kind.isin(["sell_stock", "sell_call"])]
    sell_idx = {}
    for _, s in sells.iterrows():
        j = entry_index(P, s, rule, extra_td)
        if j is not None:
            sell_idx.setdefault(s.px_ticker, []).append(j)
    pos, skipped = [], []
    for _, e in led[led.kind.isin(buys)].iterrows():
        t = e.px_ticker
        i0 = entry_index(P, e, rule, extra_td)
        if i0 is None or i0 >= end_i:
            continue
        if not P.has(t, i0):
            skipped.append((e.tdate.date(), t, e.kind, e.mid_usd))
            continue
        i1 = end_i
        if hold_td:
            i1 = min(i1, i0 + hold_td)
        if exit_on_sale:
            later = [j for j in sell_idx.get(t, []) if j > i0]
            if later:
                i1 = min(i1, min(later))
        # stop if the price series ends (delisting)
        a = P.adj[t].values[i0:i1 + 1]
        bad = np.where(~np.isfinite(a))[0]
        if len(bad):
            i1 = i0 + bad[0] - 1
        if i1 <= i0:
            continue
        if use_options and e.kind == "buy_call" and pd.notna(e.strike):
            v = option_path(P, t, i0, i1, e.strike, e.expiry)
            if v is None or v[0] <= 0:
                skipped.append((e.tdate.date(), t, "option unpriced", e.mid_usd))
                continue
            path = v / v[0]
        else:
            path = a[: i1 - i0 + 1] / a[0]
        usd = e.mid_usd if (weight == "usd" and np.isfinite(e.mid_usd)) else 1.0
        is_opt = bool(use_options and e.kind == "buy_call" and pd.notna(e.strike))
        pos.append(Pos(t, i0, i1, float(usd), path, e.kind, e.eventId, is_opt))
    return pos, skipped


def simulate(P: Prices, pos: list[Pos], start_i: int, end_i: int | None = None,
             stock_cost_bps=5.0, option_cost_pct=1.0, cash="BIL"):
    """NAV path. At every open/close the book is re-weighted to each open
    position's disclosed dollars; between events positions drift. Idle -> cash.
    Costs: stock legs `stock_cost_bps` per side, option legs `option_cost_pct`
    of premium per side. Returns (nav, exposure flags, one-way turnover/yr)."""
    end_i = len(P.cal) - 1 if end_i is None else end_i
    n = end_i - start_i + 1
    nav = np.ones(n)
    expo = np.zeros(n)
    cash_r = P.ret[cash].fillna(0).values
    opens, closes = {}, {}
    for p in pos:
        opens.setdefault(p.i0, []).append(p)
        closes.setdefault(p.i1, []).append(p)
    hold = {}                     # id -> [pos, value]
    cash_amt, traded = 1.0, 0.0

    def cost(p):
        return option_cost_pct / 100 if p.is_opt else stock_cost_bps / 1e4

    for k in range(1, n):
        i = start_i + k
        for h in hold.values():
            p, off = h[0], i - h[0].i0
            prev, cur = p.path[off - 1], p.path[off]
            h[1] *= (cur / prev) if prev > 0 else 0.0
        cash_amt *= 1 + cash_r[i]
        changed = False
        for p in closes.get(i, []):
            h = hold.pop(id(p), None)
            if h is not None:
                traded += h[1]
                cash_amt += h[1] * (1 - cost(p))
                changed = True
        for p in opens.get(i, []):
            if p.i1 > i:
                hold[id(p)] = [p, 0.0]
                changed = True
        if changed and hold:
            total = cash_amt + sum(h[1] for h in hold.values())
            tot_usd = sum(h[0].usd for h in hold.values())
            fee = 0.0
            for h in hold.values():
                tgt = total * h[0].usd / tot_usd
                traded += abs(tgt - h[1])
                fee += abs(tgt - h[1]) * cost(h[0])
            total -= fee
            for h in hold.values():
                h[1] = total * h[0].usd / tot_usd
            cash_amt = 0.0
        nav[k] = cash_amt + sum(h[1] for h in hold.values())
        expo[k] = 1.0 if hold else 0.0
    s = pd.Series(nav, index=P.cal[start_i:end_i + 1])
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    return s, expo, traded / 2 / yrs


def metrics(nav: pd.Series, P: Prices, bench="SPY", rf="BIL", exposure=None):
    r = nav.pct_change().dropna()
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = nav.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    rfr = P.ret[rf].reindex(r.index).fillna(0)
    ex = r - rfr
    vol = r.std() * math.sqrt(252)
    sharpe = ex.mean() / r.std() * math.sqrt(252) if r.std() > 0 else np.nan
    dn = r[r < 0].std() * math.sqrt(252)
    sortino = ex.mean() * 252 / dn if dn > 0 else np.nan
    dd = (nav / nav.cummax() - 1).min()
    b = P.ret[bench].reindex(r.index).fillna(0)
    cov = np.cov(r, b)
    beta = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else np.nan
    alpha = ((r - rfr) - beta * (b - rfr)).mean() * 252
    out = dict(total=nav.iloc[-1] - 1, cagr=cagr, vol=vol, sharpe=sharpe, sortino=sortino,
               maxdd=dd, calmar=(cagr / -dd if dd < 0 else np.nan), beta=beta, alpha=alpha,
               start=str(nav.index[0].date()), end=str(nav.index[-1].date()))
    if exposure is not None:
        out["exposure"] = float(np.mean(exposure))
    return out


def bench_nav(P: Prices, ticker, start_i, end_i=None):
    end_i = len(P.cal) - 1 if end_i is None else end_i
    a = P.adj[ticker].iloc[start_i:end_i + 1]
    if not np.isfinite(a.iloc[0]):
        return None
    return a / a.iloc[0]


def basket_nav(P: Prices, tickers, start_i, end_i=None):
    """Equal-weight basket, rebalanced monthly."""
    end_i = len(P.cal) - 1 if end_i is None else end_i
    r = P.ret[list(tickers)].iloc[start_i + 1:end_i + 1]
    idx = P.cal[start_i:end_i + 1]
    nav = [1.0]
    w = None
    month = None
    for d, row in r.iterrows():
        avail = row.dropna()
        if w is None or d.month != month or set(w.index) != set(avail.index):
            w = pd.Series(1 / len(avail), index=avail.index) if len(avail) else None
            month = d.month
        if w is None:
            nav.append(nav[-1])
            continue
        g = (w * (1 + avail[w.index])).sum()
        nav.append(nav[-1] * g)
        w = w * (1 + avail[w.index]) / g
    return pd.Series(nav, index=idx)


def regress_alpha(nav: pd.Series, P: Prices, factors=("SPY", "QQQ", "MTUM"), rf="BIL"):
    """Daily OLS of excess returns on factor excess returns; annualized alpha + t."""
    r = nav.pct_change().dropna()
    rfr = P.ret[rf].reindex(r.index).fillna(0)
    X = pd.concat([P.ret[f].reindex(r.index) - rfr for f in factors], axis=1).dropna()
    y = (r - rfr).reindex(X.index)
    A = np.column_stack([np.ones(len(X)), X.values])
    coef, *_ = np.linalg.lstsq(A, y.values, rcond=None)
    resid = y.values - A @ coef
    s2 = resid @ resid / (len(y) - A.shape[1])
    se = np.sqrt(np.diag(s2 * np.linalg.inv(A.T @ A)))
    return dict(alpha_ann=coef[0] * 252, alpha_t=coef[0] / se[0],
                **{f"b_{f}": c for f, c in zip(factors, coef[1:])})
