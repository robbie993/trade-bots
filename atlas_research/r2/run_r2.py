"""Run Atlas R2 exactly as pre-registered in PREREG_R2.md.

    python -m atlas_research.r2.run_r2            # writes results/r2_report.json and R2_REPORT.md
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import numpy as np

from ..data import prices
from ..portfolio.engine import Market
from ..run_core import OUT, _f
from ..validation import metrics as M
from ..validation import reality_check as RC
from .engine import backtest
from .families import DEFAULTS, NAMES, R2Market
from .fitness import judge
from .search import repair, search

WINDOWS = [
    ("W1", ("2005-01-01", "2012-12-31"), ("2013-01-01", "2014-12-31"), ("2015-01-01", "2015-12-31")),
    ("W2", ("2007-01-01", "2014-12-31"), ("2015-01-01", "2016-12-31"), ("2017-01-01", "2017-12-31")),
    ("W3", ("2009-01-01", "2016-12-31"), ("2017-01-01", "2018-12-31"), ("2019-01-01", "2019-12-31")),
    ("W4", ("2011-01-01", "2018-12-31"), ("2019-01-01", "2020-12-31"), ("2021-01-01", "2021-12-31")),
    ("W5", ("2013-01-01", "2020-12-31"), ("2021-01-01", "2022-12-31"), ("2023-01-01", "2023-12-31")),
    ("W6", ("2015-01-01", "2022-12-31"), ("2023-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31")),
]
CLEAN = ("W1", "W2", "W3", "W4", "W5")      # W6's test year was spent as R1's OOS
FAMILIES = ("A", "B", "D")


def stitched(runs):
    cat = lambda f: np.concatenate([getattr(r, f) for r in runs])
    r, b, c, e = cat("ret"), cat("bench"), cat("cash"), cat("exposure")
    s, sb = M.stats(r, c), M.stats(b, c)
    return {"cagr": s.cagr, "spy_cagr": sb.cagr, "max_dd": s.max_dd, "spy_max_dd": sb.max_dd,
            "sharpe": s.sharpe, "spy_sharpe": sb.sharpe, "calmar": s.calmar, "spy_calmar": sb.calmar,
            "exposure": float(e.mean()), "beats_return": s.cagr > sb.cagr,
            "beats_drawdown": s.max_dd > sb.max_dd}, r, b


def verdict(clean, years_won, stress_ok):
    both = clean["beats_return"] and clean["beats_drawdown"]
    if both and years_won >= 3:
        return "FORWARD PAPER CANDIDATE (strong)" if stress_ok else "CANDIDATE"
    if clean["beats_return"] or clean["beats_drawdown"]:
        return "INTERESTING (one dimension)"
    return "RESEARCH FAILURE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pop0", type=int, default=60)
    ap.add_argument("--generations", type=int, default=4)
    a = ap.parse_args()
    t0 = time.time()
    rm = R2Market(Market(prices.load()))
    rep = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "prereg": "atlas_research/PREREG_R2.md", "families": {}}
    total = 0
    test_streams = {}
    for fam in FAMILIES:
        print(f"{NAMES[fam]}", flush=True)
        F = {"name": NAMES[fam], "windows": [], "default_windows": []}
        runs, druns = {}, {}
        for name, train, val, test in WINDOWS:
            (g, tv, vv), n = search(rm, fam, train, val, pop0=a.pop0, generations=a.generations)
            total += n
            run = backtest(rm, fam, g, *test)
            v = judge(run)
            dg = repair(fam, DEFAULTS[fam])
            drun = backtest(rm, fam, dg, *test)
            dv = judge(drun)
            runs[name], druns[name] = (g, run), drun
            F["windows"].append({"name": name, "train": train, "validate": val, "test": test,
                                 "clean": name in CLEAN, "genomes": n, "champion": g,
                                 "train_v": tv.row(), "validate_v": vv.row(), "test_v": v.row()})
            F["default_windows"].append({"name": name, "test_v": dv.row()})
            print(f"  {name} test {test[0][:4]}: GA {v.cagr:+.1%} / DD {v.max_dd:.1%}   default "
                  f"{dv.cagr:+.1%} / DD {dv.max_dd:.1%}   SPY {v.spy_cagr:+.1%} / DD {v.spy_max_dd:.1%}",
                  flush=True)
        clean_runs = [runs[w][1] for w in CLEAN]
        st, r, b = stitched(clean_runs)
        test_streams[f"{fam}_ga"] = r
        test_streams["SPY"] = b
        dst, dr, _ = stitched([druns[w] for w in CLEAN])
        test_streams[f"{fam}_default"] = dr
        years_won = sum(1 for w in F["windows"] if w["clean"] and w["test_v"]["beats_return"])
        stress = {}
        for label, kw in {"costs_2x": {"spread_mult": 2}, "slippage_3x": {"slippage_mult": 3}}.items():
            sr = [backtest(rm, fam, runs[w][0], *dict((x[0], x[3]) for x in WINDOWS)[w], **kw) for w in CLEAN]
            stress[label] = stitched(sr)[0]
        stress_ok = all(x["beats_return"] and x["beats_drawdown"] for x in stress.values())
        F.update(clean_stitched=st, default_clean_stitched=dst, clean_years_beating_spy=f"{years_won}/5",
                 default_clean_years_beating_spy=f"{sum(1 for w in F['default_windows'] if w['name'] in CLEAN and w['test_v']['beats_return'])}/5",
                 stress=stress, verdict=verdict(st, years_won, stress_ok))
        print(f"  clean stitched: CAGR {st['cagr']:+.2%} vs SPY {st['spy_cagr']:+.2%}, DD {st['max_dd']:.1%} "
              f"vs {st['spy_max_dd']:.1%}, years won {years_won}/5 -> {F['verdict']}", flush=True)
        rep["families"][fam] = F
        json.dump(rep, open(OUT / "r2_report.json", "w"), indent=1, default=_f)

    models = [k for k in test_streams if k != "SPY"]
    rep["reality_check_across_families"] = {
        "models": models, **RC.reality_check_and_spa(np.stack([test_streams[k] for k in models]),
                                                    test_streams["SPY"], B=2000)}
    rep["total_genomes_evaluated"] = total
    rep["runtime_seconds"] = round(time.time() - t0)
    json.dump(rep, open(OUT / "r2_report.json", "w"), indent=1, default=_f)
    print("reality check:", rep["reality_check_across_families"])


if __name__ == "__main__":
    main()
