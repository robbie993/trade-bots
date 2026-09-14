"""The six-point bar, pointed at the village itself.

`/Users/robbie/trade/STRATEGY_BAR.md` is a standing gate for any new strategy,
and each of its six points is there because it personally converted a fake
result into a real one during the insider/options study. The village has
independently re-derived four of the six. The morning handoff of 2026-09-14
names the two it had not, and this answers them:

  **1. Beta-adjusted alpha, not raw return.** The village compares its return
  to buy-and-hold and reports the difference. That is not alpha. A desk with
  beta 0.3 in a rising market earns a market component it did not choose, and
  `trade benchmark` credits it as skill; a desk with beta 1.4 gets punished for
  the same reason. Regress, and report alpha, beta, and what a beta-matched
  index position would have earned.

  **4. Is the loss regime in the sample?** A strategy's t-statistic means
  nothing if the state it loses money in never occurred. The bar's example: an
  index short strangle cleared t=+2.29 with a 70% win rate while its own regime
  table implied -470% of premium in a March 2020 that was not in the sample.

Point 3 (survives removing the top contributors) is included because it is
cheap here and because the village's own retention module already found one
overnight bar moving a headline from -14.4 bps to -2.1.

Reads only. Writes nothing, touches no ledger.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict
from statistics import fmean, stdev

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB = os.environ.get("BAR_DB", "data/mvv.db")
BENCH = os.environ.get("BAR_BENCHMARK", "SPY")


def village_curve(conn) -> tuple:
    """Total village equity per bar, over a **balanced panel** of firms.

    Two ways this lied before it was right, both worth keeping written down.

    *One: the table is per tick, not per bar.* `firm_performance` is
    append-only and the tick writes a row per firm per tick — 624 rows share a
    single `as_of`. Summing by `as_of` adds the same firm to itself dozens of
    times and produced a village worth $1.09 billion.

    *Two, and subtler: the set of firms changes between bars.* Firms go
    bankrupt, heirs are filed, and 28 of the 48 have never traded. Summing
    equity over whoever happens to be present makes the total jump when the
    *composition* changes rather than when prices move. That version reported
    a beta of +6.20 against SPY, a single-bar move of -47.9%, and a mean of
    -0.47% per bar — which compounds to -76% over the window while the village
    is actually down 0.98%. Every one of those numbers was a firm entering or
    leaving the sum.

    So: only firms with a row in *every* bar of the window count. That is a
    real restriction and it is the honest one — a portfolio return is only
    defined for a portfolio that exists across both ends of the interval.
    """
    rows = conn.execute("""
        SELECT p.as_of, p.firm_id, p.equity, p.capital_base
        FROM firm_performance p
        JOIN (SELECT firm_id, as_of, MAX(id) AS id
              FROM firm_performance WHERE as_of IS NOT NULL
              GROUP BY firm_id, as_of) last
          ON last.id = p.id
    """).fetchall()
    by_bar = defaultdict(dict)
    for as_of, fid, equity, base in rows:
        by_bar[as_of][fid] = (float(equity or 0), float(base or 0))
    return by_bar


def balanced(by_bar: dict, bars: list, coverage: float = 0.95) -> tuple:
    """Sum equity over a stable set of firms, on the bars they all report.

    The panel must be built inside the window being analysed, not across all
    of history: no firm has a row in every bar since 2026-08-13, because the
    village has bankrupted thirteen firms and filed twenty-eight heirs.

    A strict intersection is still empty, for a duller reason — the long-lived
    firms cover 369 of 371 bars rather than all 371, and they miss *different*
    ones. So: keep firms appearing in at least `coverage` of the window, then
    keep only the bars where every one of those firms reports. Both halves are
    needed. Selecting firms alone would leave holes that read as equity
    vanishing; selecting bars alone would let a firm's first appearance read as
    a gain.
    """
    if not bars:
        return [], [], 0, 0
    seen = defaultdict(int)
    for b in bars:
        for f in by_bar[b]:
            seen[f] += 1
    need = coverage * len(bars)
    panel = {f for f, n in seen.items() if n >= need}
    if not panel:
        return [], [], 0, len(seen)
    usable = [b for b in bars if panel <= set(by_bar[b])]
    return usable, sorted(panel), len(panel), len(seen)


def village_returns(by_bar: dict, bars: list, panel: list) -> list:
    """Per-bar village return, with capital movements excluded.

    **Equity changes are not all returns.** When a firm is wound up its capital
    is handed back and its equity goes to zero; when the allocator raises a
    firm, equity jumps. Neither is the strategy making or losing money, and
    summing equity across the panel treats both as performance. That is what
    produced a single bar of **-47.89%** and a mean of -0.37% per bar — which
    compounds to about -45% over the window while the village is in fact down
    0.98%.

    So a firm-bar only contributes a return when its `capital_base` is
    unchanged across the pair, which is the ledger's own record of how much
    capital it was entrusted with. Bars where a firm's mandate moved are
    dropped for that firm and kept for the others. The village return is then
    the equity-weighted mean of the firm returns that remain — weighted,
    because a $70,000 desk and a $1,000 desk are not equal claims on the
    village's outcome.
    """
    out = []
    for prev, now in zip(bars, bars[1:]):
        num = den = 0.0
        for f in panel:
            e0, b0 = by_bar[prev].get(f, (0.0, 0.0))
            e1, b1 = by_bar[now].get(f, (0.0, 0.0))
            if e0 <= 0 or b0 <= 0 or b0 != b1:
                continue            # no base, or the mandate moved
            num += e0 * ((e1 - e0) / e0)
            den += e0
        out.append(num / den if den else 0.0)
    return out


def returns(series: list) -> list:
    return [(b - a) / a for a, b in zip(series, series[1:]) if a]


def ols(y: list, x: list) -> dict:
    """Regress y on x. Returns alpha, beta and their t-statistics."""
    n = len(y)
    mx, my = fmean(x), fmean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    beta = sxy / sxx if sxx else 0.0
    alpha = my - beta * mx
    resid = [b - (alpha + beta * a) for a, b in zip(x, y)]
    dof = n - 2
    s2 = sum(r * r for r in resid) / dof if dof > 0 else 0.0
    se_b = (s2 / sxx) ** 0.5 if sxx and s2 > 0 else float("inf")
    se_a = (s2 * (1.0 / n + mx * mx / sxx)) ** 0.5 if sxx and s2 > 0 else float("inf")
    return {
        "n": n, "alpha": alpha, "beta": beta,
        "t_alpha": alpha / se_a if se_a else 0.0,
        "t_beta": beta / se_b if se_b else 0.0,
        "mean_y": my, "mean_x": mx,
    }


def main() -> int:
    from src.trading.config import load_trading_config
    from src.trading.data.feeds import build_feed

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    by_bar = village_curve(conn)

    bars = build_feed(load_trading_config().data).series(BENCH)
    bench = {b.as_of.strftime("%Y-%m-%dT%H:%M:%SZ"): float(b.close) for b in bars}

    common = sorted(set(by_bar) & set(bench))
    if len(common) < 30:
        print(f"only {len(common)} overlapping bars — not enough to regress")
        return 1
    usable, panel, panel_n, seen_n = balanced(by_bar, common)
    if panel_n == 0 or len(usable) < 30:
        print(f"panel {panel_n} firm(s) over {len(usable)} bar(s) — not enough "
              "to form a portfolio return")
        return 1
    common = usable

    v = village_returns(by_bar, common, panel)
    s = returns([bench[k] for k in common])
    pairs = [(a, b) for a, b in zip(s, v) if abs(a) < 0.5 and abs(b) < 0.5]
    s, v = [p[0] for p in pairs], [p[1] for p in pairs]

    print("=" * 74)
    print(f"POINT 1 — BETA-ADJUSTED ALPHA   (village vs {BENCH}, 15-minute bars)")
    print("=" * 74)
    print(f"  overlapping bars      : {len(common)}  "
          f"({common[0]} -> {common[-1]})")
    print(f"  balanced panel        : {panel_n} firm(s) present in every bar "
          f"(of {seen_n} seen)")
    r = ols(v, s)
    per = lambda x: f"{x*100:+.4f}%"
    print(f"  village mean return   : {per(r['mean_y'])} per bar")
    print(f"  {BENCH} mean return      : {per(r['mean_x'])} per bar")
    print()
    print(f"  beta                  : {r['beta']:+.4f}   (t={r['t_beta']:+.2f})")
    print(f"  alpha                 : {per(r['alpha'])} per bar  "
          f"(t={r['t_alpha']:+.2f})")
    market = r["beta"] * r["mean_x"]
    print(f"  market component      : {per(market)} per bar"
          f"  = beta x {BENCH}")
    share = (market / r["mean_y"] * 100) if r["mean_y"] else float("nan")
    print(f"  share of return that is market : {share:+.1f}%")
    print()
    print(f"  a beta-matched {BENCH} position would have earned "
          f"{per(market)} per bar")
    print(f"  VERDICT: alpha t={r['t_alpha']:+.2f} — "
          + ("distinguishable from zero" if abs(r["t_alpha"]) >= 2
             else "NOT distinguishable from zero"))

    # ---- point 4 --------------------------------------------------------
    print()
    print("=" * 74)
    print("POINT 4 — IS THE LOSS REGIME IN THE SAMPLE?")
    print("=" * 74)
    bins = [(-999, -2.0), (-2.0, -1.0), (-1.0, -0.5), (-0.5, 0.5),
            (0.5, 1.0), (1.0, 2.0), (2.0, 999)]
    print(f"  {BENCH} bar move        n     village mean      worst bar")
    for lo, hi in bins:
        sel = [(a, b) for a, b in zip(s, v) if lo <= a * 100 < hi]
        if not sel:
            print(f"  [{lo:>6.1f}%,{hi:>6.1f}%)   {0:>4}      —               —")
            continue
        vs = [b for _, b in sel]
        print(f"  [{lo:>6.1f}%,{hi:>6.1f}%)   {len(sel):>4}   {per(fmean(vs)):>12}"
              f"   {per(min(vs)):>12}")
    daily = fmean(s) * 26
    sd_bar = stdev(s) if len(s) > 1 else 0.0
    worst = min(s) * 100 if s else 0.0
    print()
    print(f"  worst single {BENCH} bar in sample : {worst:+.2f}%")
    print(f"  {BENCH} bar sd                     : {sd_bar*100:.3f}%  "
          f"(worst is {abs(worst)/(sd_bar*100):.1f} sd)")
    stressed = sum(1 for a in s if a * 100 <= -1.0)
    print(f"  bars with {BENCH} <= -1.0%          : {stressed} of {len(s)}  "
          f"({stressed/len(s)*100:.1f}%)")
    if stressed < 10:
        print(f"  *** THE LOSS REGIME IS NOT IN THE SAMPLE. {stressed} observation(s) "
              "of a down move\n      is not a test of what this village does in one.")

    # ---- point 3 --------------------------------------------------------
    print()
    print("=" * 74)
    print("POINT 3 — DOES IT SURVIVE DROPPING THE TOP CONTRIBUTORS?")
    print("=" * 74)
    rows = conn.execute("""
        SELECT symbol, SUM(realized_pnl) pnl, COUNT(*) n
        FROM fills WHERE realized_pnl IS NOT NULL AND realized_pnl != 0
        GROUP BY symbol ORDER BY pnl DESC
    """).fetchall()
    total = sum(float(r[1] or 0) for r in rows)
    print(f"  realised P&L across {len(rows)} symbols : ${total:,.2f}")
    print("  top contributors:")
    for sym, pnl, n in rows[:5]:
        print(f"    {sym:10s} ${float(pnl):>10,.2f}  ({n} closed)  "
              f"{float(pnl)/total*100 if total else 0:>6.1f}% of total")
    for k in (1, 2, 5):
        rest = sum(float(r[1] or 0) for r in rows[k:])
        print(f"  drop top {k}: ${rest:>11,.2f}"
              f"   ({rest/total*100 if total else 0:+.1f}% of original)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
