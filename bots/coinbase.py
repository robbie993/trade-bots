"""COINBASE — long-only crypto momentum scorer. REAL MONEY in production.

Source of truth: /Users/robbie/trade/coinbase_bot.py (Railway service BOT=coinbase,
service honest-hope). Dossier: dossiers/coinbase.md.

⚠️ The production instance of this strategy trades REAL money (~$30 tradable plus
a hold-forever SPX position). This village translation is paper by construction —
the village has no path to a real venue — but do not confuse the two accounts.

⚠️ IT IS ALSO UNDER A PRE-REGISTERED FREEZE. D-0003 froze the strategy until 40
more closed trades or 2026-10-14, judged on net expectancy. Tweaking the rules is
what the freeze exists to prevent. Recruit this to observe it, not to improve it.

WHAT THE REAL BOT DOES
  Scores each candidate on five weighted signals — ema .25, rsi .25, volume .20,
  24h gain .15, breakout .15 — and buys when the score clears 0.45, subject to
  entry floors: >= $5M 24h volume, not already up more than 20% today.
  Exits: hard stop -4%; TP1 +8% sells 40% and arms an 8% trail from the high;
  TP2 +18% sells another 30%; break-even stop arms once the high touches +4% and
  never lets the position fall back below +0.5% (which covers the 0.6%/side taker
  fee); 24h time stop if the position is under +2%. Max 5 positions, learning
  gated to >= 25 closed trades. USDC/USDT/DAI and SPX are protected from selling.

WHAT CHANGED TO FIT THE VILLAGE
  * The volume signal (.20) and the $5M liquidity floor are DROPPED — no volume
    in context. The remaining four weights are renormalised, which raises their
    influence. That is a change to the strategy, not a neutral port.
  * Daily bars, not the 10s/300s live cadence, so the 24h time stop becomes a
    1-bar time stop and intrabar stops cannot trigger — exits are checked on the
    close only. This bot's edge claim lives in its exit geometry, so a
    close-only replay is a weaker test than the real thing.
  * Fees: the real bot pays 0.6%/side taker. Set the village's cost model
    accordingly or the result will flatter this strategy by ~1.2%/round trip.

EVIDENCE HEADLINE — NEGATIVE BASELINE, EXPERIMENT STILL OPEN.
  Frozen baseline (d0003_baseline.md, hash 3519db2): 40 trades, 55% win rate,
  net -$4.57, expectancy -$0.114/trade. Kill criteria pre-registered: if net
  expectancy is still below zero after 40 more closed trades or by 2026-10-14,
  the bot stops. Note the shape — 55% wins with negative expectancy is the exact
  win-rate-without-expectancy trap this project keeps finding.
"""

# NOTE: this bot is FROZEN under pre-registered experiment D-0003 until 40 more
# closed trades or 2026-10-14. A court verdict is evidence, not a licence to
# retune it — the freeze is the point.
GENOME = {
    "fast_window": 9,       # REAL — EMA9
    "slow_window": 21,      # REAL — EMA21
    "rsi_window": 14,       # REAL — RSI(14)
    "trend_bias": 100,      # REAL in spirit — momentum long-only
    "value_window": 30,     # DEFAULT — it has no long-run level anywhere
    "fair_band_pct": 8,     # DEFAULT — no analogue
    "calm_vol_pct": 35,     # DEFAULT — its filters are 24h volume and 24h gain
}

UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
            "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "DOT-USD"]

RAW_W = {"ema": 0.25, "rsi": 0.25, "gain": 0.15, "breakout": 0.15}   # "vol" dropped
_TOTAL = sum(RAW_W.values())
W = dict((k, v / _TOTAL) for k, v in RAW_W.items())

MIN_SCORE = 0.45
MAX_GAIN_24H = 20.0     # never buy something already up more than this today
MAX_POSITIONS = 5
STOP_PCT = -4.0
TP1_PCT = 8.0
TP2_PCT = 18.0
TIME_STOP_BARS = 1
TIME_STOP_GAIN = 2.0
NOTIONAL = 500.0
PROTECTED = ("USDC", "USDT", "DAI", "SPX")


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


def _score(closes):
    price = closes[-1]
    e9, e21 = _ema(closes, 9), _ema(closes, 21)
    r = _rsi(closes)
    if None in (e9, e21, r):
        return None, None

    ema_s = 1.0 if e9 > e21 else 0.0
    rsi_s = 1.0 if 45 <= r <= 70 else (0.5 if r < 45 else 0.0)
    gain24 = (price / closes[-2] - 1.0) * 100 if len(closes) >= 2 else 0.0
    gain_s = 1.0 if 0 < gain24 <= 8 else (0.4 if gain24 > 8 else 0.0)
    hi20 = max(closes[-20:]) if len(closes) >= 20 else price
    breakout_s = 1.0 if price >= hi20 else max(0.0, (price / hi20 - 0.95) / 0.05)

    s = (ema_s * W["ema"] + rsi_s * W["rsi"] +
         gain_s * W["gain"] + breakout_s * W["breakout"])
    return s, gain24


def propose(context):
    orders = []
    open_n = sum(1 for s in context.universe if context.quantity(s) > 0)

    for symbol in context.universe:
        if any(symbol.startswith(p) for p in PROTECTED):
            continue                    # never sold in production; skipped here
        closes = context.closes(symbol, 120)
        price = context.price(symbol)
        if not closes or not price or len(closes) < 30:
            continue
        held = context.quantity(symbol)

        # ---- exits. The real bot manages these on 10s polling with partial
        # ---- fills at TP1/TP2; here they are all-or-nothing on the close.
        if held > 0:
            prev = closes[-2]
            move = (price / prev - 1.0) * 100
            if move <= STOP_PCT:
                orders.append({"symbol": symbol, "side": "sell", "quantity": held,
                               "rationale": "COINBASE hard stop %.1f%%" % move})
            elif move >= TP2_PCT:
                orders.append({"symbol": symbol, "side": "sell", "quantity": held,
                               "rationale": "COINBASE TP2 %.1f%%" % move})
            elif move >= TP1_PCT:
                orders.append({"symbol": symbol, "side": "sell",
                               "quantity": held * 0.4,
                               "rationale": "COINBASE TP1 partial %.1f%%" % move})
            elif move < TIME_STOP_GAIN:
                orders.append({"symbol": symbol, "side": "sell", "quantity": held,
                               "rationale": "COINBASE time stop: under +2%%"})
            continue

        if open_n >= MAX_POSITIONS:
            continue
        s, gain24 = _score(closes)
        if s is None or s < MIN_SCORE:
            continue
        if gain24 > MAX_GAIN_24H:
            continue                    # never chase something already extended
        orders.append({"symbol": symbol, "side": "buy", "notional": NOTIONAL,
                       "rationale": "COINBASE score %.2f (volume leg unavailable)" % s})
        open_n += 1
    return orders
