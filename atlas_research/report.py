"""Render results/atlas_core_report.json as results/REPORT.md."""
from __future__ import annotations

import json
from pathlib import Path

from .run_core import OUT
from .validation.walk_forward import WINDOWS


def pct(x, d=1):
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def dd(x):
    return f"{x * 100:.1f}%"


def stat_rows(table: dict) -> list:
    names = {"atlas": "**Atlas**", "A_spy": "A SPY buy & hold", "B_spy_cash": "B SPY + cash (same exposure)",
             "C_simple_momentum": "C simple momentum", "D_vol_managed_spy": "D vol-managed SPY",
             "E_60_40": "E 60/40 SPY/IEF"}
    out = ["| | CAGR | max DD | Sharpe | Sortino | Calmar | worst 12m | longest underwater |",
           "|---|---|---|---|---|---|---|---|"]
    for k, s in table.items():
        out.append(f"| {names.get(k, k)} | {pct(s['cagr'])} | {dd(s['max_dd'])} | {s['sharpe']:.2f} | "
                   f"{s['sortino']:.2f} | {s['calmar']:.2f} | {pct(s['worst_12m'])} | "
                   f"{s['longest_underwater_days']} days |")
    return out


def render(r: dict) -> str:
    L = ["# Atlas Core (ETFs, no options): measured results", "",
         f"Generated {r['generated']} from `run_core.py`. Data through {r['data_end']}. "
         "Every number below is a backtest on the data described in `data/prices.py`; "
         "none of it is live trading.", ""]
    f = r["final"]
    oos = r.get("frozen_oos")
    L += ["## Verdict", ""]
    if oos:
        L += [f"**{oos['verdict']}** after the frozen OOS ({oos['period'][0]} to {oos['period'][1]}).", ""]
    L += [f"Before the OOS: **{f['verdict_before_oos']}**.", ""]

    wf = r["walk_forward_stitched_test"]
    L += ["## Walk-forward (the search process, out of sample)", "",
          "Each window searched TRAIN, picked its champion on VALIDATE and ran it once on TEST. "
          "The TEST years are never used to choose anything.", "",
          "| window | test | Atlas CAGR | SPY CAGR | Atlas max DD | SPY max DD | exposure | passed gates |",
          "|---|---|---|---|---|---|---|---|"]
    for w in r["windows"]:
        t = w["test"]
        period = w.get("test_period") or dict((x[0], x[3]) for x in WINDOWS)[w["name"]]
        L.append(f"| {w['name']} | {period[0][:4]}-{period[1][:4]} | {pct(t['cagr'])} | {pct(t['spy_cagr'])} | "
                 f"{dd(t['max_dd'])} | {dd(t['spy_max_dd'])} | {t['exposure']:.0%} | "
                 f"{'yes' if t['passed'] else 'no: ' + ', '.join(t['failed_gates'])} |")
    L += ["", f"Stitched TEST record {wf['period'][0]} to {wf['period'][1]}: Atlas CAGR {pct(wf['cagr'], 2)} vs SPY "
          f"{pct(wf['spy_cagr'], 2)}, max DD {dd(wf['max_dd'])} vs {dd(wf['spy_max_dd'])}, Sharpe "
          f"{wf['sharpe']:.2f} vs {wf['spy_sharpe']:.2f}, Calmar {wf['calmar']:.2f} vs {wf['spy_calmar']:.2f}. "
          f"Calendar years beating SPY: {wf['years_beating_spy']}. Rolling 3-year windows beating SPY: "
          f"{wf['rolling_3y_beat_share']:.0%}.", ""]

    L += ["## Final champion", "", f"Key `{f['champion_key']}`, chosen on train 2002-2022 and validation 2023-2024 "
          f"({'passed' if f['passed_validation_gates'] else 'no finalist passed'} the validation gates).", "",
          "```", json.dumps(f["champion"], indent=1), "```", "",
          "### In sample, 2002-2024, against the five benchmarks", ""]
    L += stat_rows(f["insample_benchmarks"])
    L += ["", "### Validation, 2023-2024", ""]
    L += stat_rows(f["validation_benchmarks"])
    if oos:
        L += ["", f"### Frozen OOS, {oos['period'][0]} to {oos['period'][1]} (run once, zero changes)", ""]
        L += stat_rows(oos["benchmarks"])
        L += ["", oos["cash_rate_note"] + "."]

    g = f["gauntlet"]
    p = g["5_parameter_perturbation"]
    c4 = g["4_year_concentration"]
    conc = "n/a (no positive total excess)" if c4 is None or c4 != c4 else f"{c4:.0%}"
    L += ["", "## Gauntlet (in sample 2002-2024)", "",
          f"* Gate 4, stability: largest single year's share of total excess return {conc}; "
          f"calendar years beating SPY {g['years_beating_spy']}.",
          f"* Gate 5, parameters: {p['variants']} single-gene moves of +/-10-20%; "
          f"{p['share_beating_spy_both']:.0%} still beat SPY on both CAGR and drawdown.",
          f"* Gate 6, universe: {g['6_universe']['share_beating_spy_both']:.0%} of drop-one-ETF universes beat SPY on both.",
          "* Gates 7-8, costs and slippage:"]
    for k, c in g["7_8_costs_and_slippage"].items():
        L.append(f"  * {k}: CAGR {pct(c['cagr'])}, max DD {dd(c['max_dd'])}, costs {c['costs_paid_per_year'] * 1e4:.0f} bp/yr")
    L += ["* Gate 9, regimes (annualised):", "", "| regime | days | Atlas | SPY | exposure |", "|---|---|---|---|---|"]
    for k, v in g["9_regimes"].items():
        L.append(f"| {k} | {v['days']} | {pct(v['atlas_ann'])} | {pct(v['spy_ann'])} | {v['exposure']:.0%} |")

    a = f["autopsy"]
    L += ["", "## Autopsy", "", f"Failure tags: {', '.join(a['tags']) or 'none'}.",
          f"Worst drawdown {dd(a['max_dd']['depth'])} from {a['max_dd']['peak']} to {a['max_dd']['trough']}, "
          f"recovered {a['max_dd']['recovered'] or 'not yet'}; {a['max_dd']['mean_exposure_into_trough']:.0%} invested on the way down.",
          f"Longest run of months behind SPY: {a['longest_losing_months']}; ahead: {a['longest_winning_months']}.", "",
          "Worst 3-month stretches vs SPY: " + "; ".join(f"{x['from']} to {x['to']} {pct(x['excess_vs_spy'])}" for x in a["worst_periods"]),
          "", "Best: " + "; ".join(f"{x['from']} to {x['to']} {pct(x['excess_vs_spy'])}" for x in a["best_periods"]),
          "", "Excess return over SPY by calendar year: " +
          ", ".join(f"{y} {pct(v, 0)}" for y, v in a["excess_by_year"].items()), "",
          "What each layer contributed (switching it off, 2002-2024):", ""]
    for k, v in f["layer_contribution"].items():
        if "cagr_added" in v:
            L.append(f"* {k}: CAGR {pct(v['cagr_added'], 2)}, max DD improved by {v['max_dd_improved'] * 100:+.1f} pts")

    o = f["overfitting"]
    L += ["", "## Overfitting checks (final search)", "",
          f"* White's Reality Check p = {o['reality_check_spa']['white_rc_p']:.2f}, Hansen SPA p = "
          f"{o['reality_check_spa']['hansen_spa_p']:.2f}, over {o['reality_check_spa']['models']} genomes vs SPY on the train days "
          "(small p would mean the best genome beats SPY beyond search luck).",
          f"* Probability of backtest overfitting (CSCV, 16 blocks): {o['pbo']['pbo']:.0%}. In-sample winner's median Sharpe "
          f"{o['pbo']['median_is_sharpe_of_winner']:.2f}, same genome out of sample {o['pbo']['median_oos_sharpe_of_winner']:.2f}.",
          f"* Deflated Sharpe: {o['deflated_sharpe']['deflated_sharpe_prob']:.0%} probability the champion's Sharpe over cash is "
          f"real after {o['deflated_sharpe']['trials']} trials (luck alone would give {o['deflated_sharpe']['expected_max_sharpe_from_luck_ann']:.2f}).",
          "", "## Trial counts", "", "```", json.dumps(r["trial_counts"], indent=1), "```", ""]
    return "\n".join(L)


if __name__ == "__main__":
    r = json.loads((OUT / "atlas_core_report.json").read_text())
    (OUT / "REPORT.md").write_text(render(r))
    print((OUT / "REPORT.md"))
