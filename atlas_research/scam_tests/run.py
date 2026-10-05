"""Do the methods the AI-trading scams and hype accounts advertise actually work?

Robbie (2026-10-05): "even for the ones that are scams because they only show
their wins can't we see how they're winning ... and see if it actually works?"
The methods and rule sets come from
/mnt/project-files/village-health/scam-strategies-to-test-2026-10-05.md. This
runs the ones that need nothing but price bars, on real history:

    R1  martingale / averaging down        ("95-100% win rate, 5-10%/month")
    R2  DCA bot with safety orders          ("95%+ win rate")
    R3  grid bot                            ("profits in any market")
    R5  indicator scalper (UT Bot+STC+Hull) ("98.3% win rate")
    R6  Telegram signal format              ("90% of signals hit")
    R8  copy trading, entries 1-4 bars late (what the copier really gets)
    R10 prop-firm challenge pass rate       (+10% before -5% day / -10% total)
    R11 leverage lottery: 1,000 random bots ("our best bot made X%")
    R12 hidden leverage: QQQ x1.6 vs SPY    (the chatbot stock-pick result)

Data: BTC/USD 15-minute bars (Alpaca crypto) and SPY/QQQ daily (Alpaca SIP),
via the same fetchers as atlas_research/btc_hours and r3. Costs: crypto 0.10%
a side (0.05% for the grid, a maker order), stocks 0.01% a side. Every result
is beside buy-and-hold over the same bars. Offline: nothing here trades.

    python -m atlas_research.scam_tests.run      # writes results/scam_tests.json
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(os.environ.get("SCAM_TESTS_OUT", Path(__file__).parent / "results"))
CRYPTO_COST = 0.0010
GRID_COST = 0.0005
STOCK_COST = 0.0001
RNG = np.random.default_rng(7)


# -- shared ---------------------------------------------------------------
def max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float(((equity - peak) / peak).min())


def hold(px: np.ndarray, cost: float) -> float:
    return float(px[-1] / px[0] * (1 - cost) ** 2 - 1)


def summary(equity: np.ndarray, wins: int, deals: int, worst: float, px, cost, **extra):
    return {"return": round(float(equity[-1] / equity[0] - 1), 4),
            "max_drawdown": round(max_drawdown(equity), 4),
            "win_rate": round(float(wins) / deals, 4) if deals else None,
            "deals": deals, "worst_deal": round(float(worst), 4),
            "buy_and_hold": round(hold(px, cost), 4), **extra}


# -- R1 martingale --------------------------------------------------------
def martingale(px, step, cost, base=0.01, adds=6, take=0.005, stop=False):
    """Long base x equity; add 2^k x base every `step` lower; exit at average +take."""
    cash, units, spent = 1.0, 0.0, 0.0
    equity = np.empty(len(px))
    level, last_buy = 0, None
    wins = deals = 0
    worst = 0.0
    start_eq = 1.0
    for i, p in enumerate(px):
        if units == 0:
            start_eq = cash
            size = base * cash
            units, spent, cash = size * (1 - cost) / p, size, cash - size
            level, last_buy = 0, p
        else:
            avg = spent / units
            if p >= avg * (1 + take):
                cash += units * p * (1 - cost)
                deals += 1
                r = cash / start_eq - 1
                wins += r > 0
                worst = min(worst, r)
                units = 0.0
            elif p <= last_buy * (1 - step):
                if level < adds:
                    level += 1
                    size = min(base * start_eq * 2 ** level, max(cash, 0.0))
                    if size > 0:
                        units += size * (1 - cost) / p
                        spent += size
                        cash -= size
                    last_buy = p
                elif stop:
                    cash += units * p * (1 - cost)
                    deals += 1
                    r = cash / start_eq - 1
                    worst = min(worst, r)
                    units = 0.0
        equity[i] = cash + units * p
        if equity[i] <= 0.01:
            equity[i:] = equity[i]
            break
    return summary(equity, wins, deals, worst, px, cost)


# -- R2 DCA bot -------------------------------------------------------------
def dca(px, cost, base=0.005, first=0.01, scale=1.5, dev=0.018, widen=1.2, safety=8, take=0.015):
    """Base order, then up to 8 safety orders at widening steps; exit at average +1.5%."""
    sizes = [first * scale ** k for k in range(safety)]
    steps = np.cumsum([dev * widen ** k for k in range(safety)])
    cash, units, spent, entry, filled = 1.0, 0.0, 0.0, None, 0
    equity = np.empty(len(px))
    wins = deals = 0
    worst = 0.0
    realized = 0.0
    deal_start = 1.0
    for i, p in enumerate(px):
        if units == 0:
            deal_start = cash
            size = base * cash
            units, spent, cash, entry, filled = size * (1 - cost) / p, size, cash - size, p, 0
        else:
            while filled < safety and p <= entry * (1 - steps[filled]):
                size = min(sizes[filled] * deal_start, max(cash, 0.0))
                units += size * (1 - cost) / p
                spent += size
                cash -= size
                filled += 1
            if p >= spent / units * (1 + take):
                proceeds = units * p * (1 - cost)
                realized += proceeds - spent
                cash += proceeds
                deals += 1
                wins += proceeds > spent
                worst = min(worst, proceeds / spent - 1)
                units = 0.0
        equity[i] = cash + units * p
    open_loss = (units * px[-1] - spent) if units else 0.0
    return summary(equity, wins, deals, worst, px, cost,
                   realized_pnl=round(realized, 4), open_deal_pnl=round(open_loss, 4),
                   safety_orders_used_at_end=filled if units else 0)


# -- R3 grid ----------------------------------------------------------------
def grid(px, cost, width=0.15, levels=50, relaunch_after=None):
    """Geometric grid ±width around launch; buy one level down, sell one up."""
    def launch(p):
        lv = p * np.geomspace(1 - width, 1 + width, levels)
        return lv
    lv = launch(px[0])
    per = 1.0 / levels
    cash, held = 1.0, {}          # level index -> units bought there
    # start like a real grid bot: hold the levels above the price
    cur = int(np.searchsorted(lv, px[0]))
    for k in range(cur, levels - 1):
        held[k] = per * (1 - cost) / px[0]
        cash -= per
    grid_profit = 0.0
    outside = 0
    equity = np.empty(len(px))
    prev = px[0]
    for i, p in enumerate(px):
        lo, hi = min(prev, p), max(prev, p)
        for k in range(levels - 1):
            buy, sell = lv[k], lv[k + 1]
            if k in held and hi >= sell:
                proceeds = held.pop(k) * sell * (1 - cost)
                grid_profit += proceeds - per
                cash += proceeds
            elif k not in held and lo <= buy < prev and cash >= per * 0.999:
                held[k] = per * (1 - cost) / buy
                cash -= per
        outside = outside + 1 if (p < lv[0] or p > lv[-1]) else 0
        if relaunch_after and outside >= relaunch_after:
            cash += sum(u * p * (1 - cost) for u in held.values())
            held = {}
            lv = launch(p)
            per = cash / levels
            cur = int(np.searchsorted(lv, p))
            for k in range(cur, levels - 1):
                held[k] = per * (1 - cost) / p
                cash -= per
            outside = 0
        equity[i] = cash + sum(u * p for u in held.values())
        prev = p
    return summary(equity, 0, 0, 0.0, px, cost, grid_profit=round(grid_profit, 4))


# -- R5 indicator scalper, and R8 copying it late ---------------------------
def atr(h, l, c, n):
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def ut_bot(c, a, key=1.0):
    stop = np.zeros_like(c)
    for i in range(1, len(c)):
        loss = key * a[i]
        if c[i] > stop[i - 1] and c[i - 1] > stop[i - 1]:
            stop[i] = max(stop[i - 1], c[i] - loss)
        elif c[i] < stop[i - 1] and c[i - 1] < stop[i - 1]:
            stop[i] = min(stop[i - 1], c[i] + loss)
        else:
            stop[i] = c[i] - loss if c[i] > stop[i - 1] else c[i] + loss
    buy = (c > stop) & (np.roll(c, 1) <= np.roll(stop, 1))
    buy[0] = False
    return buy, stop


def stc(c, fast=23, slow=50, cycle=10):
    s = pd.Series(c)
    macd = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()

    def stoch(x):
        lo, hi = x.rolling(cycle).min(), x.rolling(cycle).max()
        return (100 * (x - lo) / (hi - lo).replace(0, np.nan)).ffill().fillna(50)
    k = stoch(macd).ewm(alpha=0.5, adjust=False).mean()
    return stoch(k).ewm(alpha=0.5, adjust=False).mean().to_numpy()


def hull(c, n=55):
    s = pd.Series(c)

    def wma(x, m):
        w = np.arange(1, m + 1)
        return x.rolling(m).apply(lambda v: np.dot(v, w) / w.sum(), raw=True)
    return wma(2 * wma(s, n // 2) - wma(s, n), int(np.sqrt(n))).to_numpy()


def scalper_signals(df):
    c, h, l = df["c"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy()
    a = atr(h, l, c, 10)
    buy, stop = ut_bot(c, a)
    st = stc(c)
    hm = hull(c)
    ok = buy & (st < 25) & (st > np.roll(st, 1)) & (hm > np.roll(hm, 1))
    return np.where(ok)[0], a, stop


def scalper_trades(df, entries, a, cost, delay=0, extra_slip=0.0, rr=1.5):
    c, h, l = df["c"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy()
    out, busy_until = [], -1
    for e in entries:
        i = e + delay
        if i <= busy_until or i >= len(c) - 1:
            continue
        entry = c[i] * (1 + extra_slip)
        risk = a[e]
        sl, tp = entry - risk, entry + rr * risk
        j = i + 1
        while j < len(c):
            if l[j] <= sl:
                exit_ = sl
                break
            if h[j] >= tp:
                exit_ = tp
                break
            j += 1
        else:
            exit_ = c[-1]
        out.append(exit_ / entry * (1 - cost) ** 2 - 1)
        busy_until = j
    r = np.array(out)
    return {"trades": len(r), "win_rate": round(float((r > 0).mean()), 4) if len(r) else None,
            "mean_net_per_trade": round(float(r.mean()), 5) if len(r) else None,
            "sum_of_net_returns": round(float(r.sum()), 4) if len(r) else None}


def random_entries(n, count):
    return np.sort(RNG.choice(np.arange(60, n - 10), size=min(count, n - 70), replace=False))


# -- R6 Telegram signal format ----------------------------------------------
def signal_format(df, cost, n=3000, targets=(0.01, 0.02, 0.04), stop=0.05):
    c, h, l = df["c"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy()
    first_hit = net = 0
    nets = []
    for e in random_entries(len(c), n):
        entry, left, pnl, hit_any = c[e], 1.0, 0.0, False
        tgt = [entry * (1 + t) for t in targets]
        sl = entry * (1 - stop)
        k = 0
        for j in range(e + 1, min(e + 96 * 14, len(c))):
            if l[j] <= sl:
                pnl += left * (sl / entry - 1)
                left = 0
                break
            while k < 3 and h[j] >= tgt[k]:
                pnl += (1 / 3) * targets[k]
                left -= 1 / 3
                hit_any = True
                k += 1
            if left <= 1e-9:
                break
        else:
            pnl += left * (c[min(e + 96 * 14, len(c) - 1)] / entry - 1)
        pnl -= 2 * cost
        first_hit += hit_any
        nets.append(pnl)
    r = np.array(nets)
    return {"signals": len(r), "advertised_win_rate_first_target": round(first_hit / len(r), 4),
            "net_win_rate": round(float((r > 0).mean()), 4),
            "mean_net_per_signal": round(float(r.mean()), 5)}


# -- R10 prop challenge -------------------------------------------------------
def prop_pass_rate(daily, leverage, days=30, target=0.10, day_limit=0.05, total=0.10):
    r = daily.pct_change().dropna().to_numpy()
    passes = tries = 0
    for s in range(0, len(r) - days):
        eq = 1.0
        ok = None
        for x in r[s:s + days]:
            d = leverage * x
            if d <= -day_limit:
                ok = False
                break
            eq *= 1 + d
            if eq <= 1 - total:
                ok = False
                break
            if eq >= 1 + target:
                ok = True
                break
        tries += 1
        passes += bool(ok)
    return round(passes / tries, 4) if tries else None


# -- R11 leverage lottery -------------------------------------------------------
def lottery(c, bots=1000, trades=20, window=17 * 96, cost=CRYPTO_COST):
    def run(start):
        res = []
        for _ in range(bots):
            eq = 1.0
            for _ in range(trades):
                lev = RNG.uniform(10, 20)
                i = RNG.integers(start, start + window - 100)
                n = RNG.integers(4, 96)
                side = RNG.choice([-1, 1])
                path = c[i:i + n + 1] / c[i] - 1
                worst = path.min() if side > 0 else -path.max()
                if worst * lev <= -1:
                    ret = -1.0
                else:
                    ret = side * path[-1] * lev - 2 * cost * lev
                eq *= max(1 + 0.1 * ret, 0.0)    # 10% of equity margined per trade
                if eq <= 0:
                    break
            res.append(eq - 1)
        return np.array(res)
    a = run(len(c) - 2 * window)
    b = run(len(c) - window)
    top = np.argsort(a)[-10:]
    return {"bots": bots, "profitable_share": round(float((a > 0).mean()), 4),
            "median": round(float(np.median(a)), 4), "best": round(float(a.max()), 4),
            "worst": round(float(a.min()), 4),
            "top10_next_window_mean": round(float(b[top].mean()), 4),
            "all_bots_next_window_mean": round(float(b.mean()), 4),
            "rank_correlation_between_windows": round(float(
                pd.Series(a).rank().corr(pd.Series(b).rank())), 4)}


# -- R12 hidden leverage ----------------------------------------------------------
def leveraged(daily_q, daily_spy, lev=1.6, financing=0.05):
    rq = daily_q.pct_change().dropna()
    rs = daily_spy.pct_change().dropna().reindex(rq.index).fillna(0)
    lr = lev * rq - (lev - 1) * financing / 252
    eq_l, eq_s = (1 + lr).cumprod().to_numpy(), (1 + rs).cumprod().to_numpy()
    beta = float(np.cov(lr, rs)[0, 1] / np.var(rs))
    return {"qqq_x1.6_return": round(float(eq_l[-1] - 1), 4),
            "spy_return": round(float(eq_s[-1] - 1), 4),
            "qqq_x1.6_max_drawdown": round(max_drawdown(eq_l), 4),
            "spy_max_drawdown": round(max_drawdown(eq_s), 4), "beta_to_spy": round(beta, 3)}


def main():
    from atlas_research.btc_hours.run import fetch as fetch_btc
    from atlas_research.r3.feed import LiveFeed

    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    btc = fetch_btc(end - timedelta(days=3 * 365), end)
    btc_c = btc["c"].to_numpy()
    btc_daily = btc["c"].resample("1D").last().dropna()
    feed = LiveFeed(OUT / "cache")
    closes, _ = feed.bars(["SPY", "QQQ"], date.today() - timedelta(days=5 * 365), date.today())
    spy, qqq = closes["SPY"].dropna(), closes["QQQ"].dropna()

    res = {"data": {"btc_15m_bars": len(btc), "btc_from": str(btc.index[0]),
                    "btc_to": str(btc.index[-1]), "spy_days": len(spy),
                    "spy_from": str(spy.index[0].date())}}
    res["R1_martingale"] = {
        "btc_15m_no_stop": martingale(btc_c, 0.015, CRYPTO_COST),
        "btc_15m_stop_after_last_add": martingale(btc_c, 0.015, CRYPTO_COST, stop=True),
        "spy_daily_no_stop": martingale(spy.to_numpy(), 0.01, STOCK_COST),
        "qqq_daily_no_stop": martingale(qqq.to_numpy(), 0.01, STOCK_COST)}
    res["R2_dca_bot"] = {"btc_15m": dca(btc_c, CRYPTO_COST),
                         "spy_daily": dca(spy.to_numpy(), STOCK_COST)}
    res["R3_grid"] = {"btc_15m_launch_once": grid(btc_c, GRID_COST),
                      "btc_15m_relaunch_after_7_days_out": grid(btc_c, GRID_COST, relaunch_after=7 * 96)}
    entries, a, _ = scalper_signals(btc)
    res["R5_scalper_btc_15m"] = {
        "rule": scalper_trades(btc, entries, a, CRYPTO_COST),
        "random_entries_same_count": scalper_trades(
            btc, random_entries(len(btc), max(len(entries), 1)), a, CRYPTO_COST),
        "advertised_win_rate": 0.983}
    res["R8_copying_the_scalper_late"] = {
        f"{d}_bars_late": scalper_trades(btc, entries, a, CRYPTO_COST, delay=d, extra_slip=0.001)
        for d in (1, 2, 4)}
    res["R6_signal_format_random_btc"] = signal_format(btc, CRYPTO_COST)
    res["R10_prop_challenge_pass_rate"] = {
        **{f"spy_x{k}": prop_pass_rate(spy, k) for k in (1, 3, 10)},
        **{f"btc_x{k}": prop_pass_rate(btc_daily, k) for k in (1, 3, 10)}}
    res["R11_leverage_lottery_btc"] = lottery(btc_c)
    res["R12_hidden_leverage"] = leveraged(qqq, spy)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "scam_tests.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
