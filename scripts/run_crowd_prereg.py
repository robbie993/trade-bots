"""Run PREREG_crowd_calls.md, as amended.

Four arms, five criteria, three horizons, 500 shuffle seeds. Nothing here is
decided at run time: every number that could be chosen to flatter a result is
read from the prereg and written down as a constant below.

Venue is StockTwits per amendment 1, committed before this script existed.
Extraction is `crowd.extract_calls` over message text — *not* StockTwits'
own bull/bear tag, because arm 3 only remains a control if the extractor is
the one under test.

Reads only. Writes a p-value ledger row and nothing else.
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

from src.trading.config import load_trading_config      # noqa: E402
from src.trading.crowd import extract_calls, shuffle_symbols  # noqa: E402
from src.trading.data.feeds import build_feed           # noqa: E402
from src.trading.news import _get, LAST_ERROR           # noqa: E402

# ---- fixed by the prereg, not by this run -----------------------------------
UNIVERSE = ["DOGE-USD", "SHIB-USD", "PEPE-USD", "WIF-USD"]
STREAMS = {"DOGE-USD": "DOGE.X", "SHIB-USD": "SHIB.X",
           "PEPE-USD": "PEPE.X", "WIF-USD": "WIF.X"}
HORIZONS = (1, 4, 24)          # bars
FEE_BPS = 25.0                 # per side
SLIP_BPS = 19.7                # per side, measured 2026-09-11
COST_PER_SIDE = (FEE_BPS + SLIP_BPS) / 10_000.0
ROUND_TRIP = 2 * COST_PER_SIDE
SHUFFLE_SEEDS = 500
MIN_BARS_WITH_CALL = 30
MIN_CALLS = 100
PAGES = int(os.environ.get("CROWD_PAGES", "16"))

BAR_SECONDS = 900


#: StockTwits writes crypto cashtags as `$DOGE.X`; `crowd._aliases` knows
#: `DOGE` and `$DOGE`. Rewriting the *venue's notation* is not changing the
#: instrument — the extractor, its triggers and its window are untouched — so
#: this belongs in the adapter rather than in `crowd.py`, which is what is
#: under test.
_CASHTAG = re.compile(r"\$([A-Za-z]{2,10})\.X\b")


def normalise(text: str) -> str:
    return _CASHTAG.sub(r"$\1", text or "")


def bar_key(stamp: datetime) -> int:
    return int(stamp.timestamp()) // BAR_SECONDS


def collect() -> list:
    """Every StockTwits message across the four streams, de-duplicated by id."""
    seen: dict = {}
    for symbol, stream in STREAMS.items():
        cursor = None
        for page in range(PAGES):
            url = (f"https://api.stocktwits.com/api/2/streams/symbol/"
                   f"{stream}.json?limit=30")
            if cursor:
                url += f"&max={cursor}"
            raw = _get(url, label=f"st:{stream}:{page}")
            if not raw:
                print(f"    {stream} page {page}: {LAST_ERROR.get(f'st:{stream}:{page}')}")
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
            time.sleep(0.7)
        print(f"    {stream:8s} cumulative unique messages: {len(seen)}")
    return list(seen.values())


def main() -> int:
    print("=" * 76)
    print("PREREG_crowd_calls.md — StockTwits (amendment 1)")
    print("=" * 76)
    print(f"  costs: {FEE_BPS} bps fee + {SLIP_BPS} bps slippage per side "
          f"= {ROUND_TRIP*10000:.1f} bps round trip")
    print(f"  horizons: {HORIZONS} bars   shuffle seeds: {SHUFFLE_SEEDS}")
    print("\ncollecting...")
    messages = collect()
    print(f"  {len(messages)} unique messages")

    # ---- prices -------------------------------------------------------------
    feed = build_feed(load_trading_config().data)
    closes: dict = {}
    for symbol in UNIVERSE:
        closes[symbol] = {bar_key(b.as_of): float(b.close) for b in feed.series(symbol)}
    span = {k: (min(v), max(v)) for k, v in closes.items() if v}
    print(f"  price bars per symbol: "
          + ", ".join(f"{s}={len(closes[s])}" for s in UNIVERSE))

    # ---- calls --------------------------------------------------------------
    calls_by_bar: dict = defaultdict(list)
    mentions_by_bar: dict = defaultdict(list)
    total_calls = 0
    for m in messages:
        body = normalise(m.get("body") or "")
        stamp = m.get("created_at")
        if not body or not stamp:
            continue
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        key = bar_key(when)
        found = extract_calls(body, UNIVERSE)
        for call in found:
            calls_by_bar[key].append(call)
            total_calls += 1
        # arm 3: every ticker occurrence, direction ignored
        low = body.lower()
        for symbol in UNIVERSE:
            base = symbol.split("-")[0].lower()
            if base in low:
                mentions_by_bar[key].append(symbol)

    bars_with_call = len(calls_by_bar)
    print(f"\n  explicit calls extracted : {total_calls}")
    print(f"  bars carrying a call     : {bars_with_call}")

    if bars_with_call < MIN_BARS_WITH_CALL or total_calls < MIN_CALLS:
        print(f"\n  INSUFFICIENT DATA — the prereg requires >= {MIN_BARS_WITH_CALL} "
              f"bars and >= {MIN_CALLS} calls.")
        print("  That is the answer, not a number. Criterion 5 fails and the "
              "run stops here.")

        # Why it fell short matters more than that it did: a thin corpus and an
        # instrument the venue does not feed are different problems.
        mentioned = tagged = 0
        for m in messages:
            body = normalise(m.get("body") or "")
            low = body.lower()
            if any(s.split("-")[0].lower() in low for s in UNIVERSE):
                mentioned += 1
            if ((m.get("entities") or {}).get("sentiment") or {}).get("basic"):
                tagged += 1
        print("\n  DIAGNOSIS")
        print(f"    messages collected                     : {len(messages)}")
        print(f"    ... naming a universe symbol           : {mentioned}")
        print(f"    ... carrying an explicit word-form call: {total_calls}")
        print(f"    ... carrying a StockTwits bull/bear tag: {tagged}")
        print("\n    The tag count is reported to size the trade-off amendment 1")
        print("    made on purpose, not to reopen it. Arm 3 only remains a")
        print("    control while the extractor under test is the one running.")
        return 1

    # ---- returns ------------------------------------------------------------
    def forward(symbol: str, key: int, horizon: int):
        book = closes.get(symbol) or {}
        a, b = book.get(key), book.get(key + horizon)
        if a is None or b is None or a <= 0:
            return None
        return (b - a) / a

    def arm_return(bar_calls: dict, horizon: int) -> list:
        """Net per-trade return for one arm at one horizon."""
        out = []
        for key, calls in bar_calls.items():
            for call in calls:
                r = forward(call.symbol, key, horizon)
                if r is None:
                    continue
                out.append(call.direction * r - ROUND_TRIP)
        return out

    def mention_return(horizon: int) -> list:
        out = []
        for key, symbols in mentions_by_bar.items():
            for symbol in symbols:
                r = forward(symbol, key, horizon)
                if r is None:
                    continue
                out.append(r - ROUND_TRIP)      # direction ignored = long
        return out

    results = {}
    for horizon in HORIZONS:
        arm1 = arm_return(calls_by_bar, horizon)
        if not arm1:
            print(f"  horizon {horizon}: no priced calls")
            continue
        arm1_mean = statistics.fmean(arm1)

        # arm 2 — same calls, same timing, reassigned symbols, 500 seeds
        null = []
        for seed in range(SHUFFLE_SEEDS):
            shuffled = {k: shuffle_symbols(v, UNIVERSE, seed=seed)
                        for k, v in calls_by_bar.items()}
            draw = arm_return(shuffled, horizon)
            if draw:
                null.append(statistics.fmean(draw))
        null.sort()
        pct95 = null[int(0.95 * len(null))] if null else float("nan")
        median_null = statistics.median(null) if null else float("nan")
        above = sum(1 for x in null if x >= arm1_mean)

        arm3 = mention_return(horizon)
        arm3_mean = statistics.fmean(arm3) if arm3 else float("nan")

        # arm 4 — equal-weight buy and hold over the same span, per horizon
        holds = []
        for symbol in UNIVERSE:
            book = closes.get(symbol) or {}
            if not book:
                continue
            keys = sorted(book)
            holds.append((book[keys[-1]] - book[keys[0]]) / book[keys[0]])
        arm4 = statistics.fmean(holds) if holds else float("nan")

        results[horizon] = dict(n=len(arm1), arm1=arm1_mean, median_null=median_null,
                                pct95=pct95, above=above, draws=len(null),
                                arm3=arm3_mean, arm3_n=len(arm3), arm4=arm4)

    # ---- report -------------------------------------------------------------
    print("\n" + "=" * 76)
    print("ARMS  (mean net return per call, after 89.4 bps round trip)")
    print("=" * 76)
    print(f"  {'horizon':>8} {'n':>6} {'arm1 calls':>12} {'arm2 median':>12} "
          f"{'arm2 p95':>11} {'arm3 mentions':>14}")
    for h, r in results.items():
        print(f"  {h:>6} bar {r['n']:>6} {r['arm1']*100:>11.4f}% "
              f"{r['median_null']*100:>11.4f}% {r['pct95']*100:>10.4f}% "
              f"{r['arm3']*100:>13.4f}%")
    if results:
        any_h = next(iter(results.values()))
        print(f"\n  arm 4 (equal-weight buy and hold over the window): "
              f"{any_h['arm4']*100:+.2f}%")

    # ---- criteria -----------------------------------------------------------
    print("\n" + "=" * 76)
    print("CRITERIA — all five must hold")
    print("=" * 76)
    primary = results.get(HORIZONS[0])
    c1 = all(r["arm1"] > r["median_null"] for r in results.values())
    c2 = all(r["arm1"] > r["pct95"] for r in results.values())
    c3 = all(r["arm1"] > r["arm3"] for r in results.values())
    signs = {1 if r["arm1"] > 0 else -1 for r in results.values()}
    c4 = len(signs) == 1
    c5 = bars_with_call >= MIN_BARS_WITH_CALL and total_calls >= MIN_CALLS
    for label, ok in (
        ("1. arm1 beats arm2 median, net of costs", c1),
        ("2. arm1 above the 95th percentile of 500 shuffles", c2),
        ("3. arm1 beats arm3 (mentions)", c3),
        ("4. same sign at 1, 4 and 24 bars", c4),
        (f"5. >={MIN_BARS_WITH_CALL} bars with a call and >={MIN_CALLS} calls", c5),
    ):
        print(f"  [{'PASS' if ok else 'FAIL'}]  {label}")
    passed = sum((c1, c2, c3, c4, c5))
    print(f"\n  {passed} of 5.")
    print("  " + ("ALL FIVE HOLD." if passed == 5 else
                  "Fewer than five of five is a NULL — not 'promising', "
                  "not 'directionally encouraging'."))

    # the look gets counted either way
    try:
        from src.trading.research import PValueLedger
        p_emp = (primary["above"] / primary["draws"]) if primary and primary["draws"] else 1.0
        PValueLedger().record(
            subject="crowd:stocktwits_calls",
            test="explicit crowd calls predict memecoin return (PREREG_crowd_calls.md)",
            p=float(p_emp),
            run_date=datetime.now(timezone.utc).date().isoformat(),
            note=(f"{total_calls} calls over {bars_with_call} bars; arms at "
                  f"{HORIZONS} bars; {SHUFFLE_SEEDS} shuffle seeds; "
                  f"{ROUND_TRIP*10000:.1f} bps round trip; {passed}/5 criteria"),
            verdict="PASS" if passed == 5 else "FAIL",
        )
        print("\n  recorded in data/pvalue_ledger.json")
    except Exception as exc:      # noqa: BLE001
        print(f"\n  (ledger not written: {exc})")
    return 0 if passed == 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
