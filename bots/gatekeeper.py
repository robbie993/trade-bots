"""GATEKEEPER — the fleet's advisory decision filter. Publishes a verdict, not a trade.

Source of truth: /Users/robbie/trade/gatekeeper_bot.py (Railway service
BOT=gatekeeper, service sublime-celebration). Dossier: dossiers/gatekeeper.md.

WHAT THE REAL BOT DOES
  Two jobs. LIVE: synthesise a fresh long setup for each fleet symbol from
  Coinbase data and return PASS / WARN / BLOCK from five checks — ADX regime
  (>25 trending, <20 chop), ATR% volatility (>2% = size down), net reward:risk
  after 0.05% slippage (floor 1.5), correlation to ETH/BTC (>0.75 = crowded),
  and proximity to a key level. HISTORY: replay the same filter over the
  fleet's committed trade logs and report the honest counterfactual — does the
  BLOCK bucket actually hold the losers? It places no orders anywhere.

WHY THIS IS A SCANNER AND NOT A FIRM
  A filter has no direction of its own. PASS means "this long setup is
  acceptable", BLOCK means "do not take it" — BLOCK is NOT a signal to sell,
  and publishing it as a negative score would turn a veto into a short. So:
  PASS publishes a positive score, WARN a weaker one, and BLOCK publishes
  confidence 0, which the village treats as silence rather than neutrality.
  That is the correct translation of a veto into a vote.

WHAT CHANGED TO FIT THE VILLAGE
  * ADX and ATR come from highs and lows; context has closes only. Both are
    close-to-close proxies here and the thresholds are not calibrated to them.
  * The 5m fill timeframe and the 1h regime timeframe collapse to daily bars.
  * The key-level check is dropped.
  * The history-grading half — the part that produced the finding below — has
    no analogue in the village at all. This file is only the live half.

EVIDENCE HEADLINE — AND THE BUG THAT MADE IT SAY THE OPPOSITE.
  Until 2026-08-01 the runner judged the filter by comparing PASS and BLOCK
  WIN RATES and printed "the filter is NOT separating winners from losers."
  That was wrong. On the 30 graded fleet trades:
      PASS   n=8   win 25.0%   avg win +8.9% / avg loss -5.1%  -> expectancy -1.60%
      BLOCK  n=22  win 36.4%   avg win +4.7% / avg loss -6.0%  -> expectancy -2.14%
  On expectancy PASS beats BLOCK by +0.54%/trade — the filter WAS separating.
  Win rate without the win/loss size ratio is not a measurement.
  Caveat that survives the fix: both buckets are negative expectancy (the
  graded trades lost money) and PASS is only n=8.
"""

# NO GENOME, DELIBERATELY. The Gatekeeper is a filter over other bots' trades,
# not a strategy: it has no entry rule of its own to backtest. Genes would be
# fiction. It belongs in the `scanners:` block, where its PASS/WARN/BLOCK
# becomes a reading one firm can hear — expect the court to refuse it.

UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD"]

ADX_TREND = 25.0
ADX_RANGE = 20.0
ATR_PCT_HOT = 2.0
MIN_RR = 1.5
SLIPPAGE_RATE = 0.0005
CORR_HIGH = 0.75
CORR_ASSET = "ETH-USD"

PASS_SCORE = 70
WARN_SCORE = 35


def _atr_proxy(xs, n=14):
    if len(xs) < n + 1:
        return None
    return sum(abs(xs[i] - xs[i - 1]) for i in range(len(xs) - n, len(xs))) / n


def _trendiness(xs, n=14):
    """Directionality 0..1, standing in for ADX. Not on the ADX scale."""
    if len(xs) < n + 1:
        return None
    seg = xs[-(n + 1):]
    gross = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    if gross == 0:
        return 0.0
    return abs(seg[-1] - seg[0]) / gross


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return None
    ra = [(a[i] / a[i - 1]) - 1.0 for i in range(len(a) - n + 1, len(a))]
    rb = [(b[i] / b[i - 1]) - 1.0 for i in range(len(b) - n + 1, len(b))]
    m = min(len(ra), len(rb))
    ra, rb = ra[-m:], rb[-m:]
    ma, mb = sum(ra) / m, sum(rb) / m
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(m))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((x - mb) ** 2 for x in rb) ** 0.5
    if va == 0 or vb == 0:
        return None
    return cov / (va * vb)


def scan(context):
    ref = context.closes(CORR_ASSET, 60)
    readings = {}

    for symbol in context.universe:
        closes = context.closes(symbol, 120)
        price = context.price(symbol)
        if not closes or not price or len(closes) < 40:
            continue

        atr = _atr_proxy(closes)
        t = _trendiness(closes)
        if atr is None or t is None or atr <= 0:
            continue
        atr_pct = atr / price * 100

        # Scaled to the ADX thresholds the real bot uses. The scaling is an
        # assumption, and it is the weakest joint in this file.
        adx_like = t * 100

        reasons = []
        verdict = "PASS"

        if adx_like < ADX_RANGE:
            verdict = "BLOCK"
            reasons.append("chop")
        elif adx_like < ADX_TREND:
            verdict = "WARN"
            reasons.append("weak trend")

        if atr_pct > ATR_PCT_HOT:
            reasons.append("hot vol %.1f%%" % atr_pct)
            verdict = "WARN" if verdict == "PASS" else verdict

        # net R:R after slippage, on a 1.5-ATR stop and a 2x target
        stop_dist = 1.5 * atr
        target_dist = 2.0 * stop_dist
        net_rr = ((target_dist - price * SLIPPAGE_RATE) /
                  (stop_dist + price * SLIPPAGE_RATE)) if stop_dist > 0 else 0.0
        if net_rr < MIN_RR:
            verdict = "BLOCK"
            reasons.append("net R:R %.2f" % net_rr)

        if symbol != CORR_ASSET and ref:
            c = _corr(closes, ref)
            if c is not None and c > CORR_HIGH:
                reasons.append("corr %.2f to %s" % (c, CORR_ASSET))
                verdict = "WARN" if verdict == "PASS" else verdict

        if verdict == "BLOCK":
            # A veto is silence, not a short.
            readings[symbol] = {"score": 0, "confidence": 0,
                                "note": "BLOCK: " + ", ".join(reasons)}
        elif verdict == "WARN":
            readings[symbol] = {"score": WARN_SCORE, "confidence": 40,
                                "note": "WARN: " + ", ".join(reasons)}
        else:
            readings[symbol] = {"score": PASS_SCORE, "confidence": 70,
                                "note": "PASS: trend %.0f, vol %.1f%%, netRR %.2f"
                                        % (adx_like, atr_pct, net_rr)}
    return readings
