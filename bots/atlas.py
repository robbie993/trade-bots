"""ATLAS — cross-sectional crypto momentum, market-neutral, weekly rebalance.

Source of truth: /Users/robbie/trade/atlas_xsec_candidate.py (runs inside the
Railway btcc service, posts #btcc-x). Dossier: dossiers/atlas.md.

WHAT THE REAL BOT DOES
  Universe: top ~30 Coinbase USD pairs by liquidity (>= $10M/day, >= 180d
  history), refreshed weekly. Rank each name by 14-day return divided by
  30-day realised volatility. Long the top third, short the bottom third,
  inverse-vol weighted inside each side, dollar-neutral. Rebalance every 7
  days, but SKIP the rebalance if turnover would exceed 1.6 or if the top-vs-
  bottom dispersion is under 0.15. Costs charged at ~15bps/side plus a 10%
  annualised carry drag. Paper only; PRODUCTION is marked LOCKED.

WHAT CHANGED TO FIT THE VILLAGE
  * The village universe is whatever the firm is given — the real bot builds
    its own liquidity-screened universe each week, and that screen is part of
    the strategy.
  * Shorts require TRADE_ALLOW_SHORT=1. Without it this runs long-only, which
    is NOT the strategy: the whole claim is market-neutral cross-sectional
    spread, and half of it is the short leg.
  * The turnover gate is reimplemented but the village rebalances on its own
    tick, so cadence differs.

EVIDENCE HEADLINE — IN-SAMPLE ONLY, FORWARD TEST NOT FINISHED.
  In-sample (3 years, 8 coins): Sharpe 1.09 at 14d lookback vs buy-and-hold
  0.67; Monte Carlo P(profit) 93%; 26/30 config sweep above Sharpe 0.5. That
  is one window and one asset set — promising, not proven, which is why it
  runs as a CANDIDATE and is frozen while it forward-tests.
  KNOWN DEFECT, DELIBERATELY LEFT ALONE: MAX_TURNOVER = 1.6 is mis-scaled.
  Turnover on a market-neutral book runs 0..4.0, not 0..2.0, so the gate is
  about 2x too tight and skipped 29 of 37 weekly rebalances (78%) in a replay
  of real Coinbase history. Robbie's call on 2026-08-01 was "no i dont want
  anything added or changed" — retuning a constant mid-forward-test is exactly
  the drift the project's own success criterion warns about. The gate below
  therefore keeps 1.6 on purpose. Do not "fix" it here either.
"""

# Atlas ranks names against each other. The court's genes describe one symbol's
# own history, so the gene that matters most here — cross-sectional rank — has
# no gene at all. Read the verdict with that missing.
GENOME = {
    "fast_window": 14,      # REAL — LOOKBACK, the momentum window it ranks on
    "slow_window": 30,      # PARTIAL — VOL_N, a realised-vol window, not a trend window
    "rsi_window": 14,       # DEFAULT — Atlas computes no RSI
    "trend_bias": 100,      # REAL — pure momentum, long strongest / short weakest
    "value_window": 30,     # DEFAULT — no analogue
    "fair_band_pct": 8,     # DEFAULT — no analogue
    "calm_vol_pct": 35,     # PARTIAL — MIN_DISPERSION 0.15 gates on cross-sectional
                            #           spread, which is not this gene either
}

UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD",
            "AVAX-USD", "LINK-USD", "DOT-USD", "LTC-USD", "BCH-USD", "UNI-USD"]

LOOKBACK = 14           # days of return
VOL_N = 30              # days of realised vol
FRAC = 1.0 / 3.0        # top/bottom third
MAX_TURNOVER = 1.6      # mis-scaled on purpose — see the docstring
MIN_DISPERSION = 0.15
ALLOW_SHORT = False


def _stdev(xs):
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return var ** 0.5


def _vol_adj_momentum(closes):
    if len(closes) < max(LOOKBACK, VOL_N) + 1:
        return None
    ret = (closes[-1] / closes[-1 - LOOKBACK]) - 1.0
    rets = [(closes[i] / closes[i - 1]) - 1.0
            for i in range(len(closes) - VOL_N, len(closes))]
    sd = _stdev(rets)
    if not sd:
        return None
    return ret / sd


def propose(context):
    scored = []
    for symbol in context.universe:
        closes = context.closes(symbol, 200)
        if not closes or not context.price(symbol):
            continue
        m = _vol_adj_momentum(closes)
        if m is not None:
            scored.append((symbol, m))
    if len(scored) < 6:
        return []                       # too few names to form two sides

    scored.sort(key=lambda t: -t[1])
    k = max(1, int(len(scored) * FRAC))
    top = scored[:k]
    bottom = scored[-k:]

    dispersion = top[0][1] - bottom[-1][1]
    if dispersion < MIN_DISPERSION:
        return []                       # flat cross-section: no spread to trade

    equity = float(context.equity or 0)
    if equity <= 0:
        return []
    side_budget = equity * 0.5

    # inverse-volatility weights inside each side
    def _weights(names):
        inv = []
        for sym, _ in names:
            closes = context.closes(sym, VOL_N + 2)
            rets = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]
            sd = _stdev(rets) or 0.0
            inv.append((sym, 1.0 / sd if sd > 0 else 0.0))
        tot = sum(w for _, w in inv)
        if tot <= 0:
            return []
        return [(sym, w / tot) for sym, w in inv]

    target = {}
    for sym, w in _weights(top):
        target[sym] = w * side_budget
    if ALLOW_SHORT:
        for sym, w in _weights(bottom):
            target[sym] = -w * side_budget

    # turnover gate, on the same scale the real bot uses: sum |dw| in weight terms
    turnover = 0.0
    for sym in context.universe:
        held_val = context.quantity(sym) * (context.price(sym) or 0)
        turnover += abs(target.get(sym, 0.0) - held_val) / equity
    if turnover > MAX_TURNOVER:
        return []                       # this is the gate that skips ~78% of weeks

    orders = []
    for sym in context.universe:
        price = context.price(sym)
        if not price:
            continue
        held_val = context.quantity(sym) * price
        delta = target.get(sym, 0.0) - held_val
        if abs(delta) < equity * 0.01:
            continue
        side = "buy" if delta > 0 else "sell"
        orders.append({"symbol": sym, "side": side, "notional": abs(delta),
                       "rationale": "ATLAS xsec rebalance, turnover %.2f" % turnover})
    return orders
