"""BTCC — the original long/short crypto scorecard bot (paper, 3x leverage).

Source of truth: /Users/robbie/trade/btcc_bot.py (Railway service BOT=btcc).
Dossier: dossiers/btcc.md.

WHAT THE REAL BOT DOES
  Hourly candles on ~10 majors. Ten weighted sub-signals, each in [-1,+1],
  summed into a directional score: trend .20, htf .16, rs .16, momentum .14,
  macd .10, sentiment .10, funding .08, macro .08, volume .08, oi .06.
  Trades when |score| >= 0.42 AND the daily trend agrees by >= 0.25.
  Stop 1.5 ATR, TP1 2.5 ATR (bank 40%), TP2 5.0 ATR (bank 40%), trail 2.0 ATR,
  36h time stop, 4h per-coin cooldown after a stop, circuit breaker at 3 stops
  in 3h, max 4 positions / 2 per side, 1.5% equity risk, 0.05%/side taker fee.

WHAT CHANGED TO FIT THE VILLAGE — THIS IS A DIFFERENT STRATEGY, NOT BTCC
  Six of the ten sub-signals cannot be computed from village context: funding,
  open interest, fear-and-greed sentiment, macro risk-on/off, and volume all
  need feeds that a strategy file has no access to here. What is left is
  trend .20, htf .16, rs .16, momentum .14, macd .10 = 0.76 of the original
  weight, renormalised to 1.0 below. Renormalising is itself a change: it
  silently raises the influence of the five survivors.
  Also: daily bars not hourly; ATR is a close-to-close proxy; no leverage;
  no funding cost. Shorts are emitted only when the village has
  TRADE_ALLOW_SHORT=1 — otherwise the short half of a market-neutral book
  simply never opens, which is a materially different (long-only) strategy.

EVIDENCE HEADLINE — THE SCORECARD WAS TESTED AND HAS NEGATIVE EDGE.
  180 days x 8 coins (~33k samples), 2026-07-07: every signal had a NEGATIVE
  information coefficient; composite score IC -0.16 at 72h, negative in bull,
  bear and chop. Live it ran ~11W/22L and drew down 30% in three days
  (2026-07-04..06) on correlated stop cascades. The risk/exit overhaul of
  2026-07-06 (banking real partial profits, position caps, cooldowns, circuit
  breaker) fixed the accounting and the blow-up shape; it did not create an
  edge, and none has been demonstrated since. Robbie's ruling: BTCC stays
  running as a harmless paper data-collector, and its short-horizon logic is
  not to be tweaked further.
"""

# BTCC scores five signals and trades the weighted sum; the court reads seven
# numbers. Three map, four do not.
GENOME = {
    "fast_window": 20,      # REAL — the 4h EMA20 in its trend leg
    "slow_window": 50,      # REAL — the 4h EMA50
    "rsi_window": 14,       # REAL — RSI(14), its momentum leg
    "trend_bias": 100,      # REAL in spirit — every leg is trend/momentum
    "value_window": 30,     # PARTIAL — the daily EMA30 of its HTF filter, which is
                            #           a trend gate, not a value window
    "fair_band_pct": 8,     # DEFAULT — no analogue
    "calm_vol_pct": 35,     # DEFAULT — its ATR use is stop placement, not a regime gate
}

UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
            "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "LTC-USD"]

BENCHMARK = "BTC-USD"

# Original weights, restricted to what is computable here, then renormalised.
RAW_W = {"trend": 0.20, "htf": 0.16, "rs": 0.16, "momentum": 0.14, "macd": 0.10}
_TOTAL = sum(RAW_W.values())
W = dict((k, v / _TOTAL) for k, v in RAW_W.items())

MIN_SCORE = 0.42
HTF_ALIGN = 0.25
MAX_POSITIONS = 4
MAX_PER_SIDE = 2
STOP_ATR = 1.5
TP2_ATR = 5.0
NOTIONAL = 1000.0
ALLOW_SHORT = False        # the village gates shorts too; this is belt and braces


def _ema(xs, n):
    if len(xs) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(xs[:n]) / n
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e


def _rsi(xs, n=14):
    if len(xs) < n + 1:
        return None
    g = l = 0.0
    for i in range(len(xs) - n, len(xs)):
        d = xs[i] - xs[i - 1]
        if d >= 0:
            g += d
        else:
            l -= d
    if l == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + (g / n) / (l / n))


def _macd_hist(xs):
    if len(xs) < 35:
        return None
    e12, e26 = _ema(xs, 12), _ema(xs, 26)
    if e12 is None or e26 is None:
        return None
    line = []
    for i in range(26, len(xs) + 1):
        a, b = _ema(xs[:i], 12), _ema(xs[:i], 26)
        if a is not None and b is not None:
            line.append(a - b)
    if len(line) < 9:
        return None
    sig = _ema(line, 9)
    return line[-1] - sig if sig is not None else None


def _atr_proxy(xs, n=14):
    if len(xs) < n + 1:
        return None
    return sum(abs(xs[i] - xs[i - 1]) for i in range(len(xs) - n, len(xs))) / n


def _clamp(x):
    return max(-1.0, min(1.0, x))


def _score(closes, bench_ret):
    price = closes[-1]
    e20, e50 = _ema(closes, 20), _ema(closes, 50)
    r = _rsi(closes)
    mh = _macd_hist(closes)
    a = _atr_proxy(closes)
    if None in (e20, e50, r, mh, a) or a <= 0:
        return None, None

    if e20 > e50 and price > e50:
        trend = 1.0
    elif e20 < e50 and price < e50:
        trend = -1.0
    else:
        trend = _clamp((price - e50) / e50 * 20)

    momentum = _clamp((r - 50) / 25)
    if r > 80:
        momentum = _clamp(momentum - 0.4)
    if r < 20:
        momentum = _clamp(momentum + 0.4)

    macd_s = _clamp(mh / (a * 0.5 + 1e-9))

    # HTF here is the same series over a longer window — the real bot reads a
    # separate daily feed. Same intent, coarser instrument.
    htf = 0.0
    if len(closes) >= 100:
        e100 = _ema(closes, 100)
        if e100:
            htf = _clamp((price - e100) / e100 * 10)

    coin_ret = (closes[-1] - closes[-25]) / closes[-25] if len(closes) >= 25 else 0.0
    rs = _clamp((coin_ret - bench_ret) * 8)

    s = (trend * W["trend"] + momentum * W["momentum"] + macd_s * W["macd"] +
         htf * W["htf"] + rs * W["rs"])
    return s, htf


def propose(context):
    bench = context.closes(BENCHMARK, 30)
    bench_ret = ((bench[-1] - bench[-25]) / bench[-25]
                 if bench and len(bench) >= 25 else 0.0)

    held = dict((s, context.quantity(s)) for s in context.universe)
    open_n = sum(1 for q in held.values() if q != 0)
    longs = sum(1 for q in held.values() if q > 0)
    shorts = sum(1 for q in held.values() if q < 0)

    orders = []
    for symbol in context.universe:
        closes = context.closes(symbol, 200)
        price = context.price(symbol)
        if not closes or not price or len(closes) < 60:
            continue
        s, htf = _score(closes, bench_ret)
        if s is None:
            continue
        atr = _atr_proxy(closes)
        q = held.get(symbol, 0)

        # exits first: signal flip, or stop/target distance from the prior close
        if q > 0:
            if s < 0 or price <= closes[-2] - STOP_ATR * atr or \
               price >= closes[-2] + TP2_ATR * atr:
                orders.append({"symbol": symbol, "side": "sell", "quantity": q,
                               "rationale": "BTCC exit: score %.2f / ATR band" % s})
            continue
        if q < 0:
            if s > 0:
                orders.append({"symbol": symbol, "side": "buy", "quantity": abs(q),
                               "rationale": "BTCC cover: score flipped to %.2f" % s})
            continue

        if open_n >= MAX_POSITIONS:
            continue
        if abs(s) < MIN_SCORE:
            continue
        if s > 0 and htf < HTF_ALIGN:
            continue          # daily trend must agree — sitting out chop
        if s < 0 and htf > -HTF_ALIGN:
            continue

        if s > 0 and longs < MAX_PER_SIDE:
            orders.append({"symbol": symbol, "side": "buy", "notional": NOTIONAL,
                           "rationale": "BTCC long: score %.2f, htf %.2f" % (s, htf)})
            longs += 1
            open_n += 1
        elif s < 0 and ALLOW_SHORT and shorts < MAX_PER_SIDE:
            orders.append({"symbol": symbol, "side": "sell", "notional": NOTIONAL,
                           "rationale": "BTCC short: score %.2f, htf %.2f" % (s, htf)})
            shorts += 1
            open_n += 1
    return orders
