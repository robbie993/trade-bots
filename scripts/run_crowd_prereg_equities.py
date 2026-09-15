"""Run PREREG_crowd_calls_equities.md.

A separate script from `run_crowd_prereg.py` on purpose: that one is the record
of what was executed against the memecoin document, and editing it would
rewrite a result that has already been reported.

Two criteria differ from the memecoin version, both repaired in the prereg
before any number was seen:

  * criterion 4 requires the shared sign to be **positive** — the memecoin run
    passed it with all three horizons negative
  * criterion 5 counts **priced** calls, not extracted ones — the memecoin run
    certified 222 when 188 of them had no forward return

Reads only. Writes one p-value ledger row.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading.config import load_trading_config                # noqa: E402
from src.trading.crowd import extract_calls, shuffle_symbols      # noqa: E402
from src.trading.data.feeds import build_feed                     # noqa: E402
from src.trading.news import _get, LAST_ERROR                     # noqa: E402

# ---- fixed by the prereg ----------------------------------------------------
UNIVERSE = ["AAPL", "AMD", "AMZN", "DBA", "DIA", "EEM", "EFA", "EWJ", "FXI",
            "GLD", "IEF", "IWM", "JNJ", "KO", "LQD", "META", "MSFT", "NVDA",
            "PG", "QQQ", "SHY", "SLV", "SPY", "TLT", "TSLA", "USO", "VGK",
            "VZ", "XOM"]
HORIZONS = (1, 4, 24)
SLIP_BPS = 0.82          # measured, regular hours
FEE_BPS = 0.0            # Alpaca equities are commission-free
COST_PER_SIDE = (FEE_BPS + SLIP_BPS) / 10_000.0
ROUND_TRIP = 2 * COST_PER_SIDE
SHUFFLE_SEEDS = 500
MIN_BARS_WITH_CALL = 30
MIN_PRICED_CALLS = 100
PAGES = int(os.environ.get("CROWD_PAGES", "12"))
BAR_SECONDS = 900

_CASHTAG = re.compile(r"\$([A-Za-z]{1,6})\b")


def bar_key(stamp: datetime) -> int:
    return int(stamp.timestamp()) // BAR_SECONDS


def collect() -> list:
    seen: dict = {}
    for symbol in UNIVERSE:
        cursor = None
        for page in range(PAGES):
            url = (f"https://api.stocktwits.com/api/2/streams/symbol/"
                   f"{symbol}.json?limit=30")
            if cursor:
                url += f"&max={cursor}"
            raw = _get(url, label=f"st:{symbol}:{page}")
            if not raw:
                break
            payload = json.loads(raw)
            messages = payload.get("messages", [])
            if not messages:
                break
            for m in messages:
                seen[m["id"]] = m
            cursor = (payload.get("cursor") or {}).get("max")
            if not cursor:
                break
            time.sleep(0.6)
        print(f"    {symbol:6s} cumulative unique messages: {len(seen)}")
    return list(seen.values())


def main() -> int:
    print("=" * 76)
    print("PREREG_crowd_calls_equities.md")
    print("=" * 76)
    print(f"  universe: {len(UNIVERSE)} village-traded equities")
    print(f"  costs   : {SLIP_BPS} bps slippage + {FEE_BPS} bps fee per side "
          f"= {ROUND_TRIP*10000:.2f} bps round trip")
    print("\ncollecting...")
    messages = collect()
    print(f"  {len(messages)} unique messages")

    feed = build_feed(load_trading_config().data)
    closes: dict = {}
    for symbol in UNIVERSE:
        try:
            closes[symbol] = {bar_key(b.as_of): float(b.close)
                              for b in feed.series(symbol)}
        except Exception:                                   # noqa: BLE001
            closes[symbol] = {}
    print(f"  price bars: {sum(len(v) for v in closes.values())} across "
          f"{sum(1 for v in closes.values() if v)} symbols")

    calls_by_bar: dict = defaultdict(list)
    mentions_by_bar: dict = defaultdict(list)
    extracted = 0
    for m in messages:
        body = m.get("body") or ""
        stamp = m.get("created_at")
        if not body or not stamp:
            continue
        key = bar_key(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
        for call in extract_calls(body, UNIVERSE):
            calls_by_bar[key].append(call)
            extracted += 1
        tickers = {t.upper() for t in _CASHTAG.findall(body)}
        for symbol in UNIVERSE:
            if symbol in tickers:
                mentions_by_bar[key].append(symbol)

    def forward(symbol, key, horizon):
        book = closes.get(symbol) or {}
        a, b = book.get(key), book.get(key + horizon)
        if a is None or b is None or a <= 0:
            return None
        return (b - a) / a

    priced = sum(1 for k, cs in calls_by_bar.items() for c in cs
                 if all(forward(c.symbol, k, h) is not None for h in HORIZONS))
    bars_with_call = len(calls_by_bar)
    print(f"\n  explicit calls extracted : {extracted}")
    print(f"  ... priced at all horizons: {priced}")
    print(f"  bars carrying a call      : {bars_with_call}")

    if bars_with_call < MIN_BARS_WITH_CALL or priced < MIN_PRICED_CALLS:
        print(f"\n  INSUFFICIENT DATA — prereg requires >= {MIN_BARS_WITH_CALL} "
              f"bars and >= {MIN_PRICED_CALLS} PRICED calls.")
        print("  Criterion 5 fails. That is the answer, not a number.")
        return 1

    def arm_return(bar_calls, horizon):
        out = []
        for key, calls in bar_calls.items():
            for call in calls:
                r = forward(call.symbol, key, horizon)
                if r is not None:
                    out.append(call.direction * r - ROUND_TRIP)
        return out

    def mention_return(horizon):
        out = []
        for key, symbols in mentions_by_bar.items():
            for symbol in symbols:
                r = forward(symbol, key, horizon)
                if r is not None:
                    out.append(r - ROUND_TRIP)
        return out

    results = {}
    for horizon in HORIZONS:
        arm1 = arm_return(calls_by_bar, horizon)
        if not arm1:
            continue
        m1 = statistics.fmean(arm1)
        null = []
        for seed in range(SHUFFLE_SEEDS):
            shuffled = {k: shuffle_symbols(v, UNIVERSE, seed=seed)
                        for k, v in calls_by_bar.items()}
            draw = arm_return(shuffled, horizon)
            if draw:
                null.append(statistics.fmean(draw))
        null.sort()
        arm3 = mention_return(horizon)
        holds = []
        for symbol in UNIVERSE:
            book = closes.get(symbol) or {}
            if book:
                ks = sorted(book)
                holds.append((book[ks[-1]] - book[ks[0]]) / book[ks[0]])
        results[horizon] = dict(
            n=len(arm1), arm1=m1,
            median_null=statistics.median(null) if null else float("nan"),
            pct95=null[int(0.95 * len(null))] if null else float("nan"),
            above=sum(1 for x in null if x >= m1), draws=len(null),
            arm3=statistics.fmean(arm3) if arm3 else float("nan"),
            arm4=statistics.fmean(holds) if holds else float("nan"))

    print("\n" + "=" * 76)
    print(f"ARMS  (mean net return per call, after {ROUND_TRIP*10000:.2f} bps round trip)")
    print("=" * 76)
    print(f"  {'horizon':>8} {'n':>6} {'arm1 calls':>12} {'arm2 median':>12} "
          f"{'arm2 p95':>11} {'arm3 mentions':>14}")
    for h, r in results.items():
        print(f"  {h:>6} bar {r['n']:>6} {r['arm1']*100:>11.4f}% "
              f"{r['median_null']*100:>11.4f}% {r['pct95']*100:>10.4f}% "
              f"{r['arm3']*100:>13.4f}%")
    if results:
        print(f"\n  arm 4 (equal-weight buy and hold over the window): "
              f"{next(iter(results.values()))['arm4']*100:+.2f}%")

    c1 = all(r["arm1"] > r["median_null"] for r in results.values())
    c2 = all(r["arm1"] > r["pct95"] for r in results.values())
    c3 = all(r["arm1"] > r["arm3"] for r in results.values())
    signs = {1 if r["arm1"] > 0 else -1 for r in results.values()}
    c4 = len(signs) == 1 and signs == {1}          # positive, per the prereg
    c5 = bars_with_call >= MIN_BARS_WITH_CALL and priced >= MIN_PRICED_CALLS
    print("\n" + "=" * 76)
    print("CRITERIA — all five must hold")
    print("=" * 76)
    for label, ok in (
        ("1. arm1 beats arm2 median, net of costs", c1),
        ("2. arm1 above the 95th percentile of 500 shuffles", c2),
        ("3. arm1 beats arm3 (mentions)", c3),
        ("4. same sign at 1, 4, 24 bars AND that sign is positive", c4),
        (f"5. >={MIN_BARS_WITH_CALL} bars and >={MIN_PRICED_CALLS} priced calls", c5),
    ):
        print(f"  [{'PASS' if ok else 'FAIL'}]  {label}")
    passed = sum((c1, c2, c3, c4, c5))
    print(f"\n  {passed} of 5.")
    print("  " + ("ALL FIVE HOLD." if passed == 5
                  else "Fewer than five of five is a NULL."))

    try:
        from src.trading.research import PValueLedger
        primary = results.get(HORIZONS[0])
        p = (primary["above"] / primary["draws"]) if primary and primary["draws"] else 1.0
        PValueLedger().record(
            subject="crowd:stocktwits_equities",
            test="explicit crowd calls predict equity return (PREREG_crowd_calls_equities.md)",
            p=float(p),
            run_date=datetime.now(timezone.utc).date().isoformat(),
            note=(f"{passed}/5 criteria. p is the 1-bar horizon's permutation p "
                  f"ONLY and is not the result. {extracted} calls extracted, "
                  f"{priced} priced at all horizons, {bars_with_call} bars; "
                  f"{ROUND_TRIP*10000:.2f} bps round trip; {SHUFFLE_SEEDS} seeds."),
            verdict="PASS" if passed == 5 else "FAIL",
        )
        print("  recorded in data/pvalue_ledger.json")
    except Exception as exc:                                # noqa: BLE001
        print(f"  (ledger not written: {exc})")
    return 0 if passed == 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
