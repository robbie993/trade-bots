"""KEYSTONE — VERITAS dip-buy sleeve + BALLAST 12-1 trend sleeve.

Source of truth: /Users/robbie/trade/keystone_bot.py (Railway service BOT=keystone).
Dossier: dossiers/keystone.md — read it before funding this firm.

WHAT THE REAL BOT DOES
  VERITAS: long only above SMA200, enter when RSI(2) < 10 AND IBS < 0.3,
           exit on close > SMA5, a 10-trading-day time stop, or regime break.
           20 liquid ETFs, $500/position, up to 20 slots.
  BALLAST: 12-1 time-series momentum on SPY/EFA/TLT/GLD, monthly, hold iff the
           12-month return excluding the last month is > 0.

WHAT CHANGED TO FIT THE VILLAGE (do not read results as KEYSTONE's results)
  * IBS = (close - low) / (high - low) CANNOT be computed here — context gives
    closes only, no highs or lows. The IBS < 0.3 leg is DROPPED. It is half the
    entry rule, so this file fires more often than KEYSTONE does.
  * BALLAST rebalances on the village's tick, not monthly on a calendar.
  * No slippage instrumentation. The real bot's whole Experiment A is fill
    measurement (signal_bar_close -> fill_price -> slippage_bps); none of that
    survives the translation.

EVIDENCE HEADLINE: VERITAS passed 5/5 pre-registered criteria (2000-2026,
+0.353%/trade, p<0.002 vs 500 random-entry runs). BALLAST FAILED its own
pre-registration 4/5 (2022 drawdown -6.4% against a >-5% requirement) and was
kept anyway as a portfolio diversifier. Neither has a live out-of-sample verdict
yet — the 20-symbol variant needs 219 trades (~1.9 years) to be significant.
"""

# The court's seven genes, mapped from KEYSTONE's real constants. Two of them
# do not fit and are marked — a clipped gene is not this strategy's parameter.
GENOME = {
    "fast_window": 5,       # REAL — SMA5, the VERITAS exit
    "slow_window": 120,     # CLIPPED — the real regime filter is SMA200; the gene stops at 120
    "rsi_window": 5,        # CLIPPED — VERITAS uses RSI(2); the gene floor is 5. NOT the rule.
    "trend_bias": 10,       # REAL in spirit — buys dips, so reversion-weighted
    "value_window": 150,    # CLIPPED — again standing in for the 200-day regime filter
    "fair_band_pct": 8,     # DEFAULT — KEYSTONE has no such parameter
    "calm_vol_pct": 35,     # DEFAULT — KEYSTONE has no volatility gate at all
}

UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "GLD", "TLT",
            "XLE", "XLF", "XLV", "XLK", "XLP", "XLU", "XLI", "XLY",
            "XLB", "XBI", "EWJ", "VNQ"]

BALLAST = ["SPY", "EFA", "TLT", "GLD"]

NOTIONAL = 500.0      # $ per VERITAS position, as deployed
MAX_POS = 20
RSI_LEN = 2
RSI_ENTRY = 10.0
SMA_TREND = 200
SMA_EXIT = 5
BALLAST_LOOKBACK = 252
BALLAST_SKIP = 21     # 12-1: skip the most recent month


def _sma(xs, n):
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def _rsi(xs, n):
    """Wilder RSI. Returns None when there is not enough history."""
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
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def propose(context):
    orders = []
    for symbol in context.universe:
        closes = context.closes(symbol, 260)
        if not closes or len(closes) < SMA_TREND + 1:
            continue                      # silence, not neutrality
        price = context.price(symbol)
        if not price:
            continue
        held = context.quantity(symbol)
        sma200 = _sma(closes, SMA_TREND)
        sma5 = _sma(closes, SMA_EXIT)
        rsi2 = _rsi(closes, RSI_LEN)
        if sma200 is None or sma5 is None or rsi2 is None:
            continue

        # ---- BALLAST leg: 12-1 time-series momentum, hold iff positive -------
        if symbol in BALLAST and len(closes) >= BALLAST_LOOKBACK:
            past = closes[-BALLAST_LOOKBACK]
            recent = closes[-BALLAST_SKIP]
            mom_12_1 = (recent / past) - 1.0 if past else 0.0
            if mom_12_1 > 0 and held == 0:
                orders.append({"symbol": symbol, "side": "buy",
                               "notional": NOTIONAL,
                               "rationale": "BALLAST 12-1 momentum %.1f%% > 0"
                                            % (mom_12_1 * 100)})
                continue
            if mom_12_1 <= 0 and held > 0:
                orders.append({"symbol": symbol, "side": "sell",
                               "quantity": held,
                               "rationale": "BALLAST 12-1 momentum %.1f%% <= 0"
                                            % (mom_12_1 * 100)})
                continue

        # ---- VERITAS leg: dip inside an uptrend ------------------------------
        if held > 0:
            if price > sma5 or price < sma200:
                orders.append({"symbol": symbol, "side": "sell",
                               "quantity": held,
                               "rationale": "VERITAS exit: close>SMA5 or regime break"})
        else:
            if price > sma200 and rsi2 < RSI_ENTRY:
                orders.append({"symbol": symbol, "side": "buy",
                               "notional": NOTIONAL,
                               "rationale": "VERITAS entry: RSI2 %.1f<10 above SMA200 "
                                            "(IBS leg dropped — no highs/lows here)" % rsi2})
    return orders[:MAX_POS]
