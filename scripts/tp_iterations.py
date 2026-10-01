"""Ten ways to take more profit, tried on the village's best firms.

    railway run -s village-worker -- python scripts/tp_iterations.py firms.json

Until the exit genes (`Firm._take_profits`) existed, a firm left a winner only
when its analysts turned, and the average win stayed small. This replays each
named firm as it is today (the parent) and as ten variants that differ only in
how they take profit, over the same bars, and reports for each:

* the **search window** (the first two thirds after warm-up), where a variant
  is chosen, and
* the **held-out window** (the last third), which no choice was made on. A
  variant that wins the first and loses the second fitted the past; the
  Astral rounds x9 and x10 found exactly that with far take-profits, so a
  win only counts if it holds on bars it was not picked on.

Per window: P&L, closed trades, win rate, average win and average loss.
`firms.json` is a dump of the live firms (key, universe, genome, analysts);
nothing here reads or writes the village's database. Prices come from the
feed the worker uses (TRADE_DATA_SOURCE, TRADE_BAR), which is why it runs under
`railway run`: the Alpaca keys stay in Railway and in this process's memory.
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.money import D  # noqa: E402
from src.trading.backtest import Backtester  # noqa: E402
from src.trading.config import TradingConfig  # noqa: E402
from src.trading.data.market_data import MarketData  # noqa: E402

#: The ten iterations. Each is added on top of the firm's own genome; the
#: parent is the genome alone. Percentages are gains on what the position cost.
#:
#: Scaled to what these firms actually do. On 15-minute bars they hold for
#: hours and win about 1% (an average win of $4-10 on ~$800), and the analysts
#: sell long before +2%: a first set of targets at +8/+15% never fired once and
#: every variant matched its parent to the cent. So each variant first *rides*
#: a winner (ignores the analysts' sell once it is up a little) and then takes
#: profit a different way, at levels these names reach in a day or a week.
VARIANTS: dict = {
    # fixed targets: ride from +0.3%, sell all at +1.5 / +3 / +5
    "ride_tp_1.5": {"ride_pct": 0.3, "tp_pct": 1.5},
    "ride_tp_3": {"ride_pct": 0.3, "tp_pct": 3},
    "ride_tp_5": {"ride_pct": 0.5, "tp_pct": 5},
    # trails: once up `arm`, out if it gives back `trail` from its best
    "ride_trail_1_0.5": {"ride_pct": 0.3, "trail_arm_pct": 1, "trail_pct": 0.5},
    "ride_trail_2_1": {"ride_pct": 0.5, "trail_arm_pct": 2, "trail_pct": 1},
    # scale out: half at +1, the rest trails 0.75 from its best
    "half_1_then_trail": {"ride_pct": 0.3, "tp_scale_pct": 1, "tp_scale_frac": 0.5,
                          "trail_arm_pct": 1, "trail_pct": 0.75},
    # thirds: a third at +1, the rest at +3
    "third_1_rest_3": {"ride_pct": 0.3, "tp_scale_pct": 1, "tp_scale_frac": 0.34,
                       "tp_scale2_pct": 3},
    # targets sized to the name's own 15-minute range: 6 and 12 ranges
    "ride_atr_6": {"ride_pct": 0.3, "tp_atr": 6, "atr_window": 26},
    "ride_atr_12_be": {"ride_pct": 0.3, "tp_atr": 12, "atr_window": 26,
                       "breakeven_arm_pct": 0.8},
    # let everything run: ride from +0.1, never give back +1 as a loss, trail 1.5
    "run_all_trail_1.5": {"ride_pct": 0.1, "breakeven_arm_pct": 1,
                          "trail_arm_pct": 1.5, "trail_pct": 1.5},
}

#: Round two: ten children of round one's survivors, per firm. Round one's
#: held-out window chose these parents, so it is no longer held out; round two
#: is judged on the *fresh* window (bars before 30 June) that neither round
#: looked at. `TP_ROUND=2` selects these.
ATR12_BE = {"ride_pct": 0.3, "tp_atr": 12, "atr_window": 26, "breakeven_arm_pct": 0.8}
RUN_ALL = {"ride_pct": 0.1, "breakeven_arm_pct": 1, "trail_arm_pct": 1.5, "trail_pct": 1.5}
THIRDS = {"ride_pct": 0.3, "tp_scale_pct": 1, "tp_scale_frac": 0.34, "tp_scale2_pct": 3}
ROUND_2: dict = {
    "firm_e_momentum_ii_v": {
        "r1_atr12_be": ATR12_BE,
        "r1_run_all": RUN_ALL,
        "atr9_be": dict(ATR12_BE, tp_atr=9),
        "atr16_be": dict(ATR12_BE, tp_atr=16),
        "atr20_be": dict(ATR12_BE, tp_atr=20),
        "atr12_be0.5": dict(ATR12_BE, breakeven_arm_pct=0.5),
        "atr12_be1.2": dict(ATR12_BE, breakeven_arm_pct=1.2),
        "atr12_be_ride0.6": dict(ATR12_BE, ride_pct=0.6),
        "atr12_be_trail2": dict(ATR12_BE, trail_arm_pct=2, trail_pct=1.5),
        "run_all_trail1": dict(RUN_ALL, trail_pct=1, trail_arm_pct=1),
        "run_all_trail2": dict(RUN_ALL, trail_pct=2, trail_arm_pct=2),
        "run_all_atr16": dict(RUN_ALL, tp_atr=16, atr_window=26),
    },
    "firm_d_value_iii": {
        "r1_thirds_1_3": THIRDS,
        "thirds_0.8_2.5": dict(THIRDS, tp_scale_pct=0.8, tp_scale2_pct=2.5),
        "thirds_1_4": dict(THIRDS, tp_scale2_pct=4),
        "thirds_1.2_3": dict(THIRDS, tp_scale_pct=1.2),
        "half_1_rest_3": dict(THIRDS, tp_scale_frac=0.5),
        "quarter_1_rest_3": dict(THIRDS, tp_scale_frac=0.25),
        "thirds_1_3_be": dict(THIRDS, breakeven_arm_pct=0.8),
        "thirds_1_3_ride0.5": dict(THIRDS, ride_pct=0.5),
        "thirds_1_trail": {k: v for k, v in dict(THIRDS, trail_arm_pct=1.5,
                                                   trail_pct=1).items() if k != "tp_scale2_pct"},
        "thirds_1_3_atr_cap": dict(THIRDS, tp_atr=12, atr_window=26),
        "thirds_1_3_ride0.1": dict(THIRDS, ride_pct=0.1),
    },
}


def stats(result, realized) -> dict:
    wins = [p for p in realized if p > 0]
    losses = [p for p in realized if p < 0]
    return {
        "pnl": float(result.final_equity - result.start_capital),
        "ret": float(result.return_pct),
        "trades": len(realized),
        "win": (len(wins) / len(realized) * 100) if realized else 0.0,
        "avg_win": float(sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": float(sum(losses) / len(losses)) if losses else 0.0,
        "pf": float(sum(wins) / -sum(losses)) if losses and sum(losses) != 0 else float("inf"),
        "dd": float(result.max_drawdown_pct),
    }


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    firms = json.loads(Path(args[0]).read_text())
    wanted = args[1:] or ["firm_e_momentum_ii_v", "firm_d_value_iii", "firm_h_global_ii"]
    # `railway run` brings the worker's TRADE_HISTORY_DAYS; TP_HISTORY_DAYS
    # overrides it for a shorter (faster) replay.
    if os.environ.get("TP_HISTORY_DAYS"):
        os.environ["TRADE_HISTORY_DAYS"] = os.environ["TP_HISTORY_DAYS"]
    config = TradingConfig()
    out = {}
    for firm in [f for f in firms if f["key"] in wanted]:
        if os.environ.get("TP_CSV_ROOT"):
            # Offline: bars saved per firm (regular session, one shared
            # timestamp per bar), so the replay needs no network at all.
            os.environ["TRADE_DATA_SOURCE"] = "csv"
            os.environ["TRADE_DATA_DIR"] = str(Path(os.environ["TP_CSV_ROOT"]) / firm["key"])
            config = TradingConfig()
        genome = dict(firm["genome"] or {})
        seats = genome.get("analysts") or ["technical", "sentiment", "macro"]
        # scanners, news and the scribe need the live board; a replay has none,
        # so those seats abstain for parent and variants alike
        market = MarketData.from_config(config.data, firm["universe"])
        market.register(firm["universe"])
        if getattr(market, "unpriceable", None):
            # On this PC the first connection to Alpaca often stalls past the
            # feed's 20 s timeout and the next one answers in under a second,
            # so a symbol that failed is asked once more before giving up.
            print("  retrying:", dict(market.unpriceable), flush=True)
            market = MarketData.from_config(config.data, firm["universe"])
            market.register(firm["universe"])
        if getattr(market, "unpriceable", None):
            print("  NO DATA:", dict(market.unpriceable))
            continue
        total = market.length()
        tester = Backtester(config)
        first = tester.warmup
        windows = {}
        fresh_before = os.environ.get("TP_FRESH_BEFORE", "")
        if fresh_before:
            # Bars before this date were seen by no earlier round: the window
            # that judges this one. Search and held-out keep their old meaning
            # on the bars after it, so rounds stay comparable.
            bars = market.history(firm["universe"][0])
            cut = next((i for i, b in enumerate(bars)
                        if b.as_of.isoformat() >= fresh_before), total)
            if cut > first + 100:
                windows["fresh"] = (first, cut - first)
                first = cut
        split = first + (total - first) * 2 // 3
        windows["search"] = (first, split - first)
        windows["held_out"] = (split, None)
        # A bankruptcy heir's initial_allocation is stored as 0.00; its
        # allocation is what it actually trades with.
        capital = D(firm["initial"] or 0) or D(firm["allocation"] or 0) or D(20000)
        print(f"\n{firm['key']}  {firm['universe']}  {total} bars, windows "
              + ", ".join(f"{k}={v[0]}+{v[1] if v[1] is not None else total - v[0]}"
                          for k, v in windows.items()), flush=True)
        rows = {}
        variants = (ROUND_2.get(firm["key"], {}) if os.environ.get("TP_ROUND") == "2"
                    else VARIANTS)
        if not variants:
            continue
        for name, extra in [("parent", {})] + list(variants.items()):
            g = dict(genome, **extra)
            row = {}
            t0 = time.time()
            for window, (start, steps) in windows.items():
                res = tester.run(firm["key"], firm["universe"], market, genome=g,
                                 analysts=seats, capital=capital,
                                 risk_limit=D(firm["risk_limit"]), start=start, steps=steps,
                                 strategy=firm.get("strategy") if str(firm.get("strategy", "")).startswith("bot:") else "")
                row[window] = stats(res, tester.last_realized)
            rows[name] = row
            s, h = row["search"], row["held_out"]
            if "fresh" in row:
                f = row["fresh"]
                print(f"  {name:22s} FRESH  {f['pnl']:+9.0f} ({f['trades']:3d} tr, win {f['win']:4.0f}%, "
                      f"avg win {f['avg_win']:+7.0f} / loss {f['avg_loss']:+7.0f})", flush=True)
            print(f"  {name:22s} search {s['pnl']:+9.0f} ({s['trades']:3d} tr, win {s['win']:4.0f}%, "
                  f"avg win {s['avg_win']:+7.0f} / loss {s['avg_loss']:+7.0f})   held-out "
                  f"{h['pnl']:+9.0f} ({h['trades']:3d} tr, avg win {h['avg_win']:+7.0f} / "
                  f"loss {h['avg_loss']:+7.0f})  [{time.time() - t0:.0f}s]", flush=True)
        out[firm["key"]] = rows
    if len(args) and os.environ.get("TP_OUT"):
        Path(os.environ["TP_OUT"]).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
