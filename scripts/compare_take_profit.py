"""Compare the Treasuries rotation bot with its ten take-profit iterations.

Runs each over the same seeded synthetic 15-minute bars and prints how big
the wins got, which is the thing the take-profit rules are meant to change:

    python scripts/compare_take_profit.py [--seeds 5] [--bars 1500]

Synthetic bars are a random walk. They show the rules working as rules, and
what they do to the size of wins. They say nothing about real markets: rerun
with TRADE_DATA_SOURCE=alpaca (and --seeds 1) for that.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--bars", type=int, default=1500)
    parser.add_argument("--only", help="run only bots whose name contains this")
    args = parser.parse_args()
    os.environ.setdefault("TRADE_BAR_TIMEFRAME", "15m")

    from src.trading.backtest import Backtester
    from src.trading.config import DataConfig, TradingConfig
    from src.trading.data.feeds import SyntheticFeed, build_feed, timeframe_minutes
    from src.trading.data.market_data import MarketData

    config = TradingConfig()
    backtester = Backtester(config, warmup=260)
    universe = ["TLT", "TLH", "IEF"]
    bots = [("baseline (no TP)", "bots/vol_rotation/07_treasuries.py")] + [
        (p.stem, f"bots/vol_rotation_tp/{p.name}")
        for p in sorted((REPO / "bots" / "vol_rotation_tp").glob("*.py"))
    ]
    if args.only:
        bots = [b for b in bots if args.only in b[0] or b[0].startswith("baseline")]
    synthetic = config.data.source == "synthetic"
    seeds = range(1, args.seeds + 1) if synthetic else [0]
    minutes = timeframe_minutes(config.data.bar_timeframe)

    def feed_for(seed):
        if synthetic:
            return SyntheticFeed(seed=seed, days=args.bars, bar_minutes=minutes)
        return build_feed(DataConfig())

    print(f"{'bot':22s} {'return':>8s} {'avg win':>9s} {'avg loss':>9s} "
          f"{'best win':>9s} {'win rate':>8s} {'exits':>6s}")
    for name, path in bots:
        returns, wins, losses, best, rates, exits = [], [], [], [], [], []
        for seed in seeds:
            r = backtester.run(name, universe, MarketData(feed_for(seed), universe),
                               capital=Decimal("25000"), risk_limit=Decimal("0.2"),
                               strategy=f"bot:{path}")
            returns.append(float(r.return_pct))
            wins += [float(x) for x in r.realized if x > 0]
            losses += [float(x) for x in r.realized if x < 0]
            best.append(float(r.largest_win or 0))
            rates.append(float(r.win_rate_pct or 0))
            exits.append(r.closed_trades)
        mean = statistics.mean
        print(f"{name:22s} {mean(returns):+7.2f}% "
              f"${mean(wins) if wins else 0:8.2f} ${mean(losses) if losses else 0:8.2f} "
              f"${max(best):8.2f} {mean(rates):7.1f}% {mean(exits):6.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
