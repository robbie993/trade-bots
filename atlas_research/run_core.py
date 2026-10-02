"""Atlas Core (ETFs, no options): walk-forward, final search, gauntlet, freeze, OOS.

    python -m atlas_research.run_core discover   # everything up to 2024-12-31
    python -m atlas_research.run_core oos        # the sealed 2025-26 test, once

`discover` writes results/frozen_champion.json before it exits. `oos` reads
that file, checks its hash, runs the champion and the benchmarks on the
sealed period with zero changes, and refuses to run a second time.
"""
from __future__ import annotations

import argparse
import os
import hashlib
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .data import prices, rates
from .evolution import autopsy as A
from .evolution.gauntlet import classify, judge, judge_run
from .evolution.genome import Genome
from .evolution.search import run_search
from .portfolio.engine import Market, backtest
from .validation import benchmarks as BM
from .validation import metrics as M
from .validation import reality_check as RC
from .validation import robustness as RB
from .validation import walk_forward as WF

OUT = Path(os.environ.get("ATLAS_RESULTS", Path(__file__).parent / "results"))


def _f(x):
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    raise TypeError(type(x))


def save(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=1, default=_f))


def bench_table(m, start, end, exposure, ret, cash):
    rows = {"atlas": M.stats(ret, cash).as_dict()}
    for k, r in BM.all_benchmarks(m, start, end, exposure).items():
        rows[k] = M.stats(r, cash).as_dict()
    return rows


def discover(args):
    t0 = time.time()
    m = Market(prices.load())
    log = lambda s: (print(s, flush=True))
    report = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "data_end": str(m.dates[-1].date()), "windows": [], "search_settings": {
                  "pop0": args.pop0, "generations": args.generations, "top": 10,
                  "children_per_parent": 10, "seed": args.seed}}
    total_evaluated = 0

    # ---- walk-forward -----------------------------------------------------
    stitched = {"ret": [], "bench": [], "cash": [], "expo": [], "dates": [], "trades": 0,
                "turnover": 0.0}
    for name, train_end, val, test in WF.WINDOWS:
        WF.check_unsealed(test[1])
        log(f"{name}: train {WF.TRAIN_START}..{train_end}, validate {val[0][:4]}-{val[1][:4]}, test {test[0][:4]}-{test[1][:4]}")
        sr = run_search(m, (WF.TRAIN_START, train_end), val, seed=args.seed,
                        pop0=args.pop0, generations=args.generations, log=log)
        total_evaluated += sr.evaluated
        run = backtest(m, sr.champion, *test)
        v = judge_run(run)
        stitched["ret"].append(run.ret); stitched["bench"].append(run.bench)
        stitched["cash"].append(run.cash); stitched["expo"].append(run.exposure)
        stitched["dates"].append(run.dates); stitched["trades"] += run.trades
        stitched["turnover"] += run.turnover
        report["windows"].append({
            "name": name, "train_period": [WF.TRAIN_START, train_end], "validate_period": val,
            "test_period": test,
            "evaluated": sr.evaluated, "champion": sr.champion.as_dict(),
            "champion_key": sr.champion.key(),
            "passed_validation_gates": sr.champion_passed_validation,
            "train": sr.champion_train.row(), "validation": sr.champion_val.row(),
            "test": v.row(), "history": sr.history,
            "test_benchmarks": bench_table(m, *test, run.exposure.mean(), run.ret, run.cash),
        })
        log(f"  -> test {test[0][:4]}-{test[1][:4]}: CAGR {v.stats.cagr:+.1%} vs SPY {v.spy.cagr:+.1%}, "
            f"maxDD {v.stats.max_dd:.1%} vs {v.spy.max_dd:.1%}, passed={v.passed}")
        save("atlas_core_report.json", report)

    sr_ret = np.concatenate(stitched["ret"]); sr_b = np.concatenate(stitched["bench"])
    sr_c = np.concatenate(stitched["cash"]); sr_e = np.concatenate(stitched["expo"])
    import pandas as pd
    sdates = pd.DatetimeIndex(np.concatenate([d.values for d in stitched["dates"]]))
    wf = judge(sr_ret, sr_b, sr_c, sr_e, stitched["trades"], stitched["turnover"])
    exc = M.calendar_excess(sdates, sr_ret, sr_b)
    report["walk_forward_stitched_test"] = {
        "period": [str(sdates[0].date()), str(sdates[-1].date())], **wf.row(),
        "excess_by_year": {int(k): float(v) for k, v in exc.items()},
        "years_beating_spy": f"{int((exc > 0).sum())}/{len(exc)}",
        "rolling_3y_beat_share": M.rolling_beat_share(sr_ret, sr_b, 756),
        "largest_year_share_of_excess": M.year_concentration(exc),
    }
    log(f"WALK-FORWARD stitched test {sdates[0].date()}..{sdates[-1].date()}: CAGR {wf.stats.cagr:+.2%} "
        f"vs SPY {wf.spy.cagr:+.2%}, maxDD {wf.stats.max_dd:.1%} vs {wf.spy.max_dd:.1%}")

    # ---- final champion ---------------------------------------------------
    log(f"FINAL: train {WF.FINAL_TRAIN}, validate {WF.FINAL_VALIDATE}")
    sr = run_search(m, WF.FINAL_TRAIN, WF.FINAL_VALIDATE, seed=args.seed,
                    pop0=args.pop0, generations=args.generations, log=log)
    total_evaluated += sr.evaluated
    g = sr.champion
    insample = (WF.FINAL_TRAIN[0], WF.FINAL_VALIDATE[1])
    WF.check_unsealed(insample[1])
    full = backtest(m, g, *insample)
    vin = judge_run(full)
    vval = sr.champion_val
    log(f"  champion {g.key()}: validation CAGR {vval.stats.cagr:+.1%} vs {vval.spy.cagr:+.1%}, "
        f"maxDD {vval.stats.max_dd:.1%} vs {vval.spy.max_dd:.1%}")

    log("  gauntlet: perturbation, universe, costs, regimes, ablations ...")
    pert = RB.parameter_perturbation(m, g, *insample)
    univ = RB.universe_robustness(m, g, *insample)
    costs = RB.cost_stress(m, g, *insample)
    aut = A.autopsy(m, full)
    contrib = A.contribution(m, g, *insample)
    exc_in = M.calendar_excess(full.dates, full.ret, full.bench)

    log("  overfitting statistics over the final search ...")
    R = sr.train_returns.astype(float)
    rc = RC.reality_check_and_spa(R, sr.train_bench, B=args.bootstrap)
    excess_cash = R - sr.train_cash[None, :]
    pbo = RC.pbo_cscv(excess_cash)
    all_sr = excess_cash.mean(axis=1) / excess_cash.std(axis=1, ddof=1)
    champ_train = backtest(m, g, *WF.FINAL_TRAIN)
    dsr = RC.deflated_sharpe(champ_train.ret - champ_train.cash, all_sr)

    cost_pass = costs["costs_2x"]["beats_both"] and costs["slippage_3x"]["beats_both"]
    wf_pass = wf.beats_return and wf.beats_drawdown
    verdict = classify(vval, vin, pert["share_beating_spy_both"], cost_pass, wf_pass, None)

    report["final"] = {
        "champion": g.as_dict(), "champion_key": g.key(),
        "passed_validation_gates": sr.champion_passed_validation,
        "train": sr.champion_train.row(), "validation": vval.row(), "insample_2002_2024": vin.row(),
        "insample_benchmarks": bench_table(m, *insample, full.exposure.mean(), full.ret, full.cash),
        "validation_benchmarks": bench_table(m, *WF.FINAL_VALIDATE,
                                             backtest(m, g, *WF.FINAL_VALIDATE).exposure.mean(),
                                             backtest(m, g, *WF.FINAL_VALIDATE).ret,
                                             backtest(m, g, *WF.FINAL_VALIDATE).cash),
        "finalists": [{"key": f[0].key(), "train": f[1].row(), "validation": f[2].row()} for f in sr.finalists],
        "history": sr.history,
        "gauntlet": {
            "5_parameter_perturbation": {k: v for k, v in pert.items() if k != "rows"},
            "5_rows": pert["rows"],
            "6_universe": univ,
            "7_8_costs_and_slippage": costs,
            "9_regimes": aut.by_regime,
            "4_year_concentration": M.year_concentration(exc_in),
            "years_beating_spy": f"{int((exc_in > 0).sum())}/{len(exc_in)}",
        },
        "autopsy": aut.as_dict(),
        "layer_contribution": contrib,
        "overfitting": {"reality_check_spa": rc, "pbo": pbo, "deflated_sharpe": dsr},
        "verdict_before_oos": verdict,
    }
    report["trial_counts"] = {
        "total_genomes_evaluated": total_evaluated,
        "searches": len(WF.WINDOWS) + 1,
        "generations_per_search": args.generations,
        "restarts": 0,
        "genes": len(Genome().as_dict()),
        "signal_families_tested": ["momentum (4 fixed lookbacks)", "5-year reversal value",
                                   "low volatility", "trend filter", "volatility target",
                                   "panic regime (5 fixed inputs)", "stop rule"],
        "note": "every genome the GA ever evaluated is counted; no run was discarded",
    }
    report["runtime_seconds"] = round(time.time() - t0)
    save("atlas_core_report.json", report)

    frozen = {"frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "genome": g.as_dict(), "key": g.key(),
              "sha256": hashlib.sha256(json.dumps(g.as_dict(), sort_keys=True).encode()).hexdigest(),
              "verdict_before_oos": verdict, "oos_period": WF.FINAL_OOS}
    save("frozen_champion.json", frozen)
    log(f"FROZEN champion {g.key()} -> results/frozen_champion.json; verdict before OOS: {verdict}")


def oos(args):
    fz = json.loads((OUT / "frozen_champion.json").read_text())
    g = Genome(**fz["genome"])
    if hashlib.sha256(json.dumps(g.as_dict(), sort_keys=True).encode()).hexdigest() != fz["sha256"]:
        sys.exit("frozen champion file was edited after freezing; refusing")
    rep_path = OUT / "atlas_core_report.json"
    report = json.loads(rep_path.read_text())
    if "frozen_oos" in report and not args.again:
        sys.exit("the frozen OOS has already been run once; its result stands")
    m = Market(prices.load())
    start, end = fz["oos_period"]
    run = backtest(m, g, start, end)
    v = judge_run(run)
    fin = report["final"]
    pert = fin["gauntlet"]["5_parameter_perturbation"]["share_beating_spy_both"]
    costs = fin["gauntlet"]["7_8_costs_and_slippage"]
    wf = report["walk_forward_stitched_test"]

    class _V:  # rebuild the minimum classify needs from the saved rows
        def __init__(self, row):
            self.beats_return = row["cagr"] > row["spy_cagr"]
            self.beats_drawdown = row["max_dd"] > row["spy_max_dd"]
    verdict = classify(_V(fin["validation"]), _V(fin["insample_2002_2024"]), pert,
                       costs["costs_2x"]["beats_both"] and costs["slippage_3x"]["beats_both"],
                       wf["cagr"] > wf["spy_cagr"] and wf["max_dd"] > wf["spy_max_dd"], v)
    report["frozen_oos"] = {
        "period": [str(run.dates[0].date()), str(run.dates[-1].date())],
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "champion_key": g.key(), **v.row(),
        "benchmarks": bench_table(m, start, end, run.exposure.mean(), run.ret, run.cash),
        "cash_rate_note": f"T-bill rates for {rates.ESTIMATED_YEARS} are estimates",
        "verdict": verdict,
    }
    rep_path.write_text(json.dumps(report, indent=1, default=_f))
    print(f"FROZEN OOS {run.dates[0].date()}..{run.dates[-1].date()}: CAGR {v.stats.cagr:+.2%} vs SPY "
          f"{v.spy.cagr:+.2%}, maxDD {v.stats.max_dd:.1%} vs {v.spy.max_dd:.1%} -> {verdict}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--pop0", type=int, default=80)
    d.add_argument("--generations", type=int, default=5)
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--bootstrap", type=int, default=1000)
    o = sub.add_parser("oos")
    o.add_argument("--again", action="store_true", help="rerun after a bug fix; say so in the report")
    a = ap.parse_args()
    {"discover": discover, "oos": oos}[a.cmd](a)


if __name__ == "__main__":
    main()
