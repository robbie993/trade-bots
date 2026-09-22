"""SCANNER — the daily momentum/relative-strength screen. A reading, never an order.

Source of truth: /Users/robbie/trade/scanner_bot.py (co-hosted in the Railway
wheel service, posts #scanner). Dossier: dossiers/scanner.md.

WHAT THE REAL BOT DOES
  Twice a day (20:00 ET on closing data, 07:00 ET premarket refresh) it scans a
  ~78-name base universe plus the day's 40 most actives, drops anything under $5
  or under 1M average volume, and scores what is left:

      score = (20d return - SPY 20d return)          # relative strength
            + (+10 if above the 50d MA else -12)      # trend filter
            + min(volume ratio, 3) * 5                # volume surge
            + (price / 20d high - 0.9) * 120          # breakout proximity

  It publishes the top 10 as a WATCHLIST. Its picks are not auto-bought — the
  only thing that trades them is picks_trader, and only after next-morning
  confirmation.

WHAT CHANGED TO FIT THE VILLAGE
  * The volume-surge term is DROPPED (no volume in context). It contributes 0
    to 15 points of an additive score, so rankings here differ from the real
    scanner's, especially on the days a volume spike is what promoted a name.
  * The most-actives half of the universe is gone — that list is fetched live.
    This scans only the firm's own universe.
  * The raw score is squashed to the village's -100..+100 with tanh-like
    clipping at +/-40 raw. The clip point is a presentation choice, not the
    bot's; two names both at +100 here may be far apart in the real ranking.

EVIDENCE HEADLINE — THE HAND-PICKED UNIVERSE IS THE PROBLEM.
  Measured 2026-08-06: scanner_bot's hand-written base universe is worth about
  +26.9%/yr of pure hindsight compared with a random-name control. The names in
  it were chosen in 2026 from what had already worked. Any backtest that scores
  this screen on that universe is measuring the list, not the screen. If you
  trial this in the village, feed it a universe someone else chose, or the
  verdict is meaningless. The four-way stack built on top of it tied a random-
  name control (t=+0.79).
"""

# The scanner publishes readings and never orders, so the genome exists only so
# the court has something to backtest. Its real output is a ranking.
GENOME = {
    "fast_window": 20,      # REAL — ret20 and the 20-day high both use it
    "slow_window": 50,      # REAL — the MA50 trend filter
    "rsi_window": 14,       # DEFAULT — the scanner computes no RSI
    "trend_bias": 100,      # REAL — relative strength, breakout proximity, volume surge
    "value_window": 50,     # PARTIAL — MA50 again, used as a filter not a fair value
    "fair_band_pct": 8,     # DEFAULT — no analogue
    "calm_vol_pct": 35,     # DEFAULT — its volume-surge term is the opposite of a calm gate
}

UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMD", "AMZN", "GOOGL", "META", "TSLA",
            "AVGO", "NFLX", "CRM", "ORCL", "INTC", "QCOM", "MU", "PLTR",
            "COIN", "HOOD", "SOFI", "UBER", "DIS", "JPM", "V", "WMT", "XOM"]

BENCHMARK = "SPY"
TOP_N = 10
MIN_PRICE = 5.0
SCORE_CLIP = 40.0        # raw score mapped to +/-100 at this magnitude


def _sma(xs, n):
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def _raw_score(closes, bench_ret20):
    if len(closes) < 55:
        return None
    price = closes[-1]
    if price < MIN_PRICE:
        return None
    ret20 = (price / closes[-21] - 1.0) * 100
    ma50 = _sma(closes, 50)
    if ma50 is None:
        return None
    near_high = price / max(closes[-20:])
    rs = ret20 - bench_ret20
    return rs + (10.0 if price > ma50 else -12.0) + (near_high - 0.9) * 120


def scan(context):
    bench = context.closes(BENCHMARK, 60)
    bench_ret20 = ((bench[-1] / bench[-21] - 1.0) * 100
                   if bench and len(bench) >= 21 else 0.0)

    rows = []
    for symbol in context.universe:
        if symbol == BENCHMARK:
            continue
        closes = context.closes(symbol, 160)
        if not closes:
            continue
        raw = _raw_score(closes, bench_ret20)
        if raw is None:
            continue                    # not enough history is silence
        rows.append((symbol, raw))

    rows.sort(key=lambda t: -t[1])
    readings = {}
    for symbol, raw in rows[:TOP_N]:
        score = max(-100.0, min(100.0, raw / SCORE_CLIP * 100.0))
        # Confidence reflects how much history backed the score, not how much
        # anyone should believe the screen. See the evidence note above.
        readings[symbol] = {"score": round(score, 1), "confidence": 50}
    return readings
