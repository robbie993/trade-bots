"""Replay Astral and its ten take-profit iterations on real 15m bars.

    python scripts/astral_backtest.py            # fetch (or reuse) and run
    python scripts/astral_backtest.py --refresh  # fetch fresh bars

Bars come from Yahoo's chart endpoint: 15m, regular hours for equities, 24/7
for crypto, about the last 60 days (Yahoo will not serve more at 15m). They
are cached in logs/astral_15m.json so a rerun replays the same tape.

**What this is and is not.** Each strategy runs on each of the ten baskets in
`bots/astral.py` with its own $25,000. At every bar the bot sees what the
village would hand it (the last 250 bars, its cash, its book) and its orders
fill at the **next bar's open** for that symbol, crossing the spread and
paying a fee. It is not the village: there is no risk manager, conscience or
kill switch cutting orders, so real fills would be the same or smaller. Sixty
days is one market regime. A result here is a reason to look closer, not a
reason to fund anything.

A trade is one round trip, flat to flat, in one symbol. Its P&L includes every
tranche in and out and every fee. Win rate is over closed trades only; a
position still open at the end is marked to the last close in the return but
not counted as a win or a loss.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

CACHE = REPO / "logs" / "astral_15m.json"
START_CASH = Decimal("25000")
LOOKBACK = 250                     # what the village hands a bot
# Per side, in basis points. The village's defaults for equities, and the
# measured Alpaca crypto costs `firm_c_crypto` carries.
COSTS = {"equity": (Decimal("2"), Decimal("5")), "crypto": (Decimal("25"), Decimal("5.3"))}
STRATEGIES = ["astral"] + sorted(p.stem for p in (REPO / "bots").glob("astral_tp_*.py"))


# =========================================================================
# bars
# =========================================================================
def fetch(symbols, refresh=False):
    cached = {}
    if CACHE.exists() and not refresh:
        cached = json.loads(CACHE.read_text())
    missing = [s for s in symbols if s not in cached]
    if missing:
        from src.trading.data.feeds import YahooFeed

        feed = YahooFeed(days=10 ** 6, interval="15m")
        for symbol in missing:
            rows = []
            for b in feed.series(symbol):
                # Yahoo's newest bar is the one still forming. Only whole
                # fifteen-minute bars are kept.
                if b.as_of.minute % 15 or b.as_of.second:
                    continue
                rows.append([b.as_of.timestamp(), str(b.open), str(b.high),
                             str(b.low), str(b.close), str(b.volume)])
            cached[symbol] = rows
            print(f"  fetched {symbol}: {len(rows)} bars", flush=True)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cached))
    return {s: cached[s] for s in symbols}


class Series:
    """One symbol's bars as parallel lists, so a context is a slice."""

    def __init__(self, rows):
        self.times = [datetime.fromtimestamp(r[0], tz=timezone.utc) for r in rows]
        self.stamps = [r[0] for r in rows]
        self.opens = [Decimal(r[1]) for r in rows]
        self.highs = [Decimal(r[2]) for r in rows]
        self.lows = [Decimal(r[3]) for r in rows]
        self.closes = [Decimal(r[4]) for r in rows]
        self.volumes = [Decimal(r[5]) for r in rows]


# =========================================================================
# the replay
# =========================================================================
def _memoise(core):
    """Every strategy reads the same market, so the market half is computed
    once per bar and shared. Keyed on each symbol's newest bar, which is what
    a read depends on. This changes speed, not any number."""
    reads, corrs = {}, {}
    read, corr = core._read, core._basket_correlation

    def cached_read(context, symbol):
        times = context.times(symbol, 1)
        key = (symbol, times[-1] if times else None)
        if key not in reads:
            reads[key] = read(context, symbol)
        return reads[key]

    def cached_corr(context, symbols):
        key = tuple((s, context.times(s, 1)[-1]) for s in symbols)
        if key not in corrs:
            corrs[key] = corr(context, symbols)
        return corrs[key]

    core._read, core._basket_correlation = cached_read, cached_corr


class Book:
    def __init__(self, fee_bps, slip_bps):
        self.cash = START_CASH
        self.qty, self.avg = {}, {}
        self.fee, self.slip = fee_bps / Decimal(10000), slip_bps / Decimal(10000)
        self.open_trades = {}          # symbol -> [cost in, proceeds out]
        self.closed = []               # (symbol, pnl, why it closed)
        self.fees = Decimal(0)

    def buy(self, symbol, notional, bar_open):
        price = bar_open * (1 + self.slip)
        notional = min(Decimal(notional), self.cash / (1 + self.fee))
        if notional <= 0:
            return
        qty = notional / price
        fee = notional * self.fee
        held = self.qty.get(symbol, Decimal(0))
        self.avg[symbol] = (self.avg.get(symbol, Decimal(0)) * held + price * qty) / (held + qty)
        self.qty[symbol] = held + qty
        self.cash -= notional + fee
        self.fees += fee
        self.open_trades.setdefault(symbol, [Decimal(0), Decimal(0)])[0] += notional + fee

    def sell(self, symbol, qty, bar_open, why=""):
        held = self.qty.get(symbol, Decimal(0))
        qty = min(Decimal(qty), held)
        if qty <= 0:
            return
        price = bar_open * (1 - self.slip)
        proceeds = qty * price
        fee = proceeds * self.fee
        self.cash += proceeds - fee
        self.fees += fee
        self.qty[symbol] = held - qty
        trade = self.open_trades.setdefault(symbol, [Decimal(0), Decimal(0)])
        trade[1] += proceeds - fee
        if self.qty[symbol] <= held * Decimal("1e-9"):
            self.qty[symbol] = Decimal(0)
            self.avg.pop(symbol, None)
            cost, back = self.open_trades.pop(symbol)
            self.closed.append((symbol, back - cost, why))


def _reason(rationale):
    """The exit rule that closed a trade, from the order's rationale."""
    for name in ("take profit", "stop", "momentum gone", "rotating out", "distribution"):
        if rationale.startswith(name):
            return name
    return "other"


def replay(job):
    """One basket, every strategy, in lockstep over the basket's clock."""
    name, symbols, rows = job
    from src.trading import adapter
    from src.trading.adapter import Context, Holding

    import bots.astral as core

    _memoise(core)
    bots = {"astral": core.propose}
    for stem in STRATEGIES[1:]:
        bots[stem] = adapter.load(REPO / "bots" / f"{stem}.py")
    # Each iteration imports `bots.astral`; loaded after the patch, they all
    # share this one memoised module.

    series = {s: Series(rows[s]) for s in symbols}
    kind = "crypto" if any("-" in s for s in symbols) else "equity"
    books = {k: Book(*COSTS[kind]) for k in bots}
    pending = {k: [] for k in bots}
    clock = sorted({t for s in symbols for t in series[s].stamps})
    curves = {k: [] for k in bots}
    peak = {k: START_CASH for k in bots}
    drawdown = {k: Decimal(0) for k in bots}

    for stamp in clock:
        # Where every symbol stands: the index of its newest bar at or before now.
        at = {s: bisect.bisect_right(series[s].stamps, stamp) - 1 for s in symbols}
        fresh = {s for s in symbols if at[s] >= 0 and series[s].stamps[at[s]] == stamp}

        for key, bot in bots.items():
            book = books[key]
            # Fill what was asked for on the previous bar, at this bar's open.
            waiting = []
            for order in pending[key]:
                s = order["symbol"]
                if s not in fresh:
                    waiting.append(order)
                    continue
                if order["side"] == "buy":
                    book.buy(s, order["notional"], series[s].opens[at[s]])
                else:
                    book.sell(s, order["quantity"], series[s].opens[at[s]],
                              _reason(order.get("rationale", "")))
            pending[key] = waiting

            marks = {s: series[s].closes[at[s]] for s in symbols if at[s] >= 0}
            equity = book.cash + sum(q * marks.get(s, book.avg.get(s, 0))
                                     for s, q in book.qty.items())
            curves[key].append(equity)
            peak[key] = max(peak[key], equity)
            drawdown[key] = max(drawdown[key], (peak[key] - equity) / peak[key])

            if not fresh:
                continue

            def cut(values, s):
                i = at[s] + 1
                return values[max(0, i - LOOKBACK):i] if i > 0 else []

            ctx = Context(
                universe=tuple(symbols), cash=book.cash, equity=equity, as_of=stamp,
                _closes={s: cut(series[s].closes, s) for s in symbols},
                _marks={s: marks.get(s) for s in symbols},
                _positions={s: Holding(s, q, book.avg[s])
                            for s, q in book.qty.items() if q > 0},
                _highs={s: cut(series[s].highs, s) for s in symbols},
                _lows={s: cut(series[s].lows, s) for s in symbols},
                _opens={s: cut(series[s].opens, s) for s in symbols},
                _volumes={s: cut(series[s].volumes, s) for s in symbols},
                _times={s: cut(series[s].times, s) for s in symbols},
            )
            busy = {o["symbol"] for o in pending[key]}
            for order in bot(ctx) or []:
                if order["symbol"] not in busy:
                    pending[key].append(order)

    out = {}
    last = {s: series[s].closes[-1] for s in symbols if series[s].closes}
    for key, book in books.items():
        final = book.cash + sum(q * last[s] for s, q in book.qty.items())
        pnls = [float(p) for _, p, _ in book.closed]
        exits = {}
        for _, p, why in book.closed:
            exits.setdefault(why, []).append(float(p))
        out[key] = {
            "pnls": pnls,
            "final": float(final),
            "open": sum(1 for q in book.qty.values() if q > 0),
            "fees": float(book.fees),
            "max_dd": float(drawdown[key]),
            "exits": exits,
        }
    return name, kind, len(clock), out


# =========================================================================
# the report
# =========================================================================
def summarise(pnls, final, start):
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        "trades": len(pnls),
        "win_rate": len(wins) / len(pnls) if pnls else None,
        "pnl": final - start,
        "ret": (final - start) / start,
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "pf": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--json", type=Path, help="also write the results here")
    args = parser.parse_args()

    from bots.astral import BASKETS

    rows = fetch(sorted({s for names in BASKETS.values() for s in names}), args.refresh)
    jobs = [(name, names, {s: rows[s] for s in names}) for name, names in BASKETS.items()]
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(replay, jobs))

    per = {}
    for name, kind, bars, out in results:
        for key, r in out.items():
            per.setdefault(key, {})[name] = r

    fmt = lambda x, f: "-" if x is None else format(x, f)  # noqa: E731
    print(f"\n{'strategy':<30}{'trades':>7}{'win%':>7}{'P&L $':>11}{'return':>8}"
          f"{'avg win':>9}{'avg loss':>9}{'PF':>6}{'worst DD':>9}")
    report = {}
    for key in STRATEGIES:
        baskets = per[key]
        pnls = [p for r in baskets.values() for p in r["pnls"]]
        start = float(START_CASH) * len(baskets)
        s = summarise(pnls, sum(r["final"] for r in baskets.values()), start)
        s["worst_dd"] = max(r["max_dd"] for r in baskets.values())
        s["baskets"] = {n: summarise(r["pnls"], r["final"], float(START_CASH)) | {
            "max_dd": r["max_dd"], "open": r["open"], "fees": r["fees"]} for n, r in baskets.items()}
        report[key] = s
        print(f"{key:<30}{s['trades']:>7}{fmt(s['win_rate'] and s['win_rate'] * 100, '.0f'):>7}"
              f"{s['pnl']:>11,.0f}{s['ret'] * 100:>7.2f}%{fmt(s['avg_win'], ',.0f'):>9}"
              f"{fmt(s['avg_loss'], ',.0f'):>9}{fmt(s['pf'], '.2f'):>6}"
              f"{s['worst_dd'] * 100:>8.1f}%")

    reasons = ("take profit", "stop", "momentum gone", "rotating out", "distribution")
    print("\nHow trades ended (count / win% / P&L $):")
    print(f"{'strategy':<30}" + "".join(f"{r:>22}" for r in reasons))
    for key in STRATEGIES:
        exits = {}
        for r in per[key].values():
            for why, ps in r["exits"].items():
                exits.setdefault(why, []).extend(ps)
        report[key]["exits"] = {w: {"n": len(ps), "pnl": sum(ps)} for w, ps in exits.items()}
        cells = []
        for why in reasons:
            ps = exits.get(why, [])
            cells.append("-" if not ps else
                         f"{len(ps)} / {sum(p > 0 for p in ps) / len(ps) * 100:.0f}% / {sum(ps):,.0f}")
        print(f"{key:<30}" + "".join(f"{c:>22}" for c in cells))

    print("\nReturn by basket:")
    names = list(BASKETS)
    print(f"{'strategy':<30}" + "".join(f"{n[:9]:>10}" for n in names))
    for key in STRATEGIES:
        b = report[key]["baskets"]
        print(f"{key:<30}" + "".join(f"{b[n]['ret'] * 100:>9.1f}%" for n in names))

    first = min(datetime.fromtimestamp(rows[s][0][0], tz=timezone.utc) for s in rows)
    last = max(datetime.fromtimestamp(rows[s][-1][0], tz=timezone.utc) for s in rows)
    print(f"\n{len(BASKETS)} baskets x $25,000 each, {first:%Y-%m-%d} to {last:%Y-%m-%d}, "
          "fills at next bar's open with fees and slippage.")
    if args.json:
        args.json.write_text(json.dumps({"window": [first.isoformat(), last.isoformat()],
                                         "strategies": report}, indent=1))


if __name__ == "__main__":
    main()
