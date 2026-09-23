"""SENTINEL — checklist-gated crypto trend bot.

Source of truth: /Users/robbie/trade/sentinel_bot.py (Railway service BOT=sentinel).
Robbie's standing rule: sentinel_bot.py is authored as-is and is not edited. This
file is a translation for the village, not a replacement.
Dossier: dossiers/sentinel.md.

WHAT THE REAL BOT DOES
  2h bars on BTC/ETH/SOL from Coinbase public data (no keys, paper only).
  Base signal: EMA9 > EMA21 and RSI(14) between 50 and 70.
  Then five checklist gates must all pass: daily EMA50 slope up (HTF trend),
  ATR% between its 20th and 90th percentile, ADX >= 18, 2h quote volume >=
  $250k, and net R:R >= 1.8. Stop = entry - 1.5*ATR, target = entry + 2*stop
  distance. Risk 1% of equity per trade, max 3 open.

WHAT CHANGED TO FIT THE VILLAGE
  * Daily bars, not 2h. The village ticks on daily closes.
  * ATR is a close-to-close proxy (mean |close - prev close|) — no highs/lows.
  * The liquidity gate is DROPPED — no volume in context. It vetoed 0 signals
    in 5.6 years of the real backtest, so this is the cheapest omission here.
  * The R:R gate is DROPPED because it is dead code in the original too:
    TP_MULT=2.0 and MIN_RR=1.8 are both constants, so rr is always 2.0 and
    checklist item 5 vetoed exactly zero signals in 5.6 years.
  * Position sizing here is a fixed notional. The real bot's sizing is
    UNCAPPED — median notional 0.31x equity, three positions can exceed equity.

EVIDENCE HEADLINE — THIS STRATEGY WAS MEASURED AND HAS NO EDGE.
  Replayed bar-by-bar on real Coinbase 2h data, 2021-01-01 -> 2026-08-07:
  244 trades, win 32.0%, -0.488%/trade, -70.1% total at 0.55%/side taker fees.
  At ZERO cost it is still -0.080%/trade: 32.0% wins against a 33.3% break-even
  for its own measured 2:1 payoff. Buy-and-hold BTC/ETH/SOL over the same window
  was +123.4%. Fees were not the problem; there was no edge for them to eat.
  Recruit this only to reproduce a known negative, not in hope of a positive.
"""

# Sentinel is the cleanest fit of the fleet — its EMAs and RSI are literally
# the court's first three genes.
GENOME = {
    "fast_window": 9,       # REAL — EMA_FAST
    "slow_window": 21,      # REAL — EMA_SLOW
    "rsi_window": 14,       # REAL — RSI_LEN
    "trend_bias": 100,      # REAL in spirit — trend-following, RSI only confirms
    "value_window": 50,     # REAL-ish — the daily EMA50 whose slope is the HTF filter
    "fair_band_pct": 8,     # DEFAULT — Sentinel has no valuation band
    "calm_vol_pct": 30,     # APPROXIMATE — it gates on the 20th/90th ATR%
                            #               percentile, which is not this gene
}

UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD"]

EMA_FAST = 9
EMA_SLOW = 21
RSI_LEN = 14
RSI_LONG_MIN = 50.0
RSI_LONG_MAX = 70.0
HTF_EMA_LEN = 50
HTF_SLOPE_LOOKBACK = 3
ATR_LEN = 14
ATR_STOP_MULT = 1.5
TP_MULT = 2.0
ADX_BLOCK = 18.0          # kept as a name; see _trendiness() for the substitute
VOL_PCTL_LOW = 0.20
VOL_PCTL_HIGH = 0.90
VOL_PCTL_WINDOW = 500
NOTIONAL = 1000.0
MAX_OPEN_POSITIONS = 3


def _ema(xs, n):
    if len(xs) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(xs[:n]) / n
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e


def _ema_series(xs, n):
    if len(xs) < n:
        return []
    k = 2.0 / (n + 1)
    e = sum(xs[:n]) / n
    out = [e]
    for x in xs[n:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def _rsi(xs, n):
    if len(xs) < n + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(xs) - n, len(xs)):
        d = xs[i] - xs[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + (gains / n) / (losses / n))


def _atr_proxy(xs, n):
    """Close-to-close stand-in for ATR. Not ATR — there are no highs or lows."""
    if len(xs) < n + 1:
        return None
    diffs = [abs(xs[i] - xs[i - 1]) for i in range(len(xs) - n, len(xs))]
    return sum(diffs) / n


def _trendiness(xs, n=14):
    """Stand-in for ADX: |net move| / sum of absolute moves, 0..1.

    ADX needs +DM/-DM from highs and lows. This measures the same idea — is the
    move directional or is it chop — on a different scale. ADX >= 18 out of 100
    is roughly 'not dead flat'; the 0.20 threshold below is the analogous call
    and is NOT a calibrated equivalent.
    """
    if len(xs) < n + 1:
        return None
    seg = xs[-(n + 1):]
    gross = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    if gross == 0:
        return 0.0
    return abs(seg[-1] - seg[0]) / gross


def _pctl_rank(series, value):
    if not series:
        return None
    below = sum(1 for s in series if s <= value)
    return below / len(series)


def propose(context):
    orders = []
    open_positions = sum(1 for s in context.universe if context.quantity(s) > 0)

    for symbol in context.universe:
        # **Floats at the boundary, and only here.** The context serves Decimal
        # because it also serves cash; the indicator maths below is the real
        # bot's, written in float, with float constants (`k = 2.0/(n+1)`,
        # `gains = 0.0`). Mixing the two raises `TypeError: unsupported operand
        # type(s) for *: 'decimal.Decimal' and 'float'` on the first EMA, which
        # is why this file had never once run inside the village.
        #
        # Converting here rather than rewriting the maths in Decimal is
        # deliberate: the shipped example states the rule — "Use Decimal for
        # anything that becomes money. Floats are fine for indicators" — and a
        # rewrite would change the arithmetic of a port whose whole value is
        # being the same arithmetic as the bot on Railway.
        closes = [float(c) for c in context.closes(symbol, VOL_PCTL_WINDOW + 60)]
        price = context.price(symbol)
        price = float(price) if price is not None else None
        if not closes or not price or len(closes) < HTF_EMA_LEN + HTF_SLOPE_LOOKBACK + 5:
            continue
        held = context.quantity(symbol)

        ema_f = _ema(closes, EMA_FAST)
        ema_s = _ema(closes, EMA_SLOW)
        rsi = _rsi(closes, RSI_LEN)
        atr = _atr_proxy(closes, ATR_LEN)
        if None in (ema_f, ema_s, rsi, atr) or atr <= 0:
            continue

        # ---- exits: stop and target, checked on the close --------------------
        if held > 0:
            stop_dist = ATR_STOP_MULT * atr
            if price <= closes[-2] - stop_dist:
                orders.append({"symbol": symbol, "side": "sell", "quantity": held,
                               "rationale": "SENTINEL stop: -1.5 ATR-proxy"})
            elif price >= closes[-2] + TP_MULT * stop_dist:
                orders.append({"symbol": symbol, "side": "sell", "quantity": held,
                               "rationale": "SENTINEL target: +2R"})
            continue

        if open_positions >= MAX_OPEN_POSITIONS:
            continue

        # ---- base signal -----------------------------------------------------
        if not (ema_f > ema_s and RSI_LONG_MIN <= rsi <= RSI_LONG_MAX):
            continue

        # ---- gate 1: higher-timeframe trend ----------------------------------
        htf = _ema_series(closes, HTF_EMA_LEN)
        if len(htf) <= HTF_SLOPE_LOOKBACK:
            continue
        if htf[-1] <= htf[-1 - HTF_SLOPE_LOOKBACK]:
            continue

        # ---- gate 2: volatility regime ---------------------------------------
        atr_pcts = []
        for i in range(ATR_LEN + 1, len(closes)):
            window = closes[:i + 1]
            a = _atr_proxy(window, ATR_LEN)
            if a and window[-1]:
                atr_pcts.append(a / window[-1])
        rank = _pctl_rank(atr_pcts[-VOL_PCTL_WINDOW:], atr / price)
        if rank is None or rank < VOL_PCTL_LOW or rank > VOL_PCTL_HIGH:
            continue

        # ---- gate 3: not chop -------------------------------------------------
        t = _trendiness(closes)
        if t is None or t < 0.20:
            continue

        orders.append({"symbol": symbol, "side": "buy", "notional": NOTIONAL,
                       "rationale": "SENTINEL: EMA9>21, RSI %.0f, HTF up, vol pctl %.2f"
                                    % (rsi, rank)})
        open_positions += 1

    return orders
