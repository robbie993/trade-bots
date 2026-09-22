"""WHEEL — cash-secured puts on 12 single names, then covered calls if assigned.

Source of truth: /Users/robbie/trade/wheel_bot.py (Railway service BOT=wheel,
Alpaca PAPER). Dossier: dossiers/wheel.md — read it before funding anything.

WHAT THE REAL BOT DOES
  Sells a ~21 DTE cash-secured put ~8% below spot on a name from its watchlist,
  but only if the premium annualises to >= 15%. Buys to close at 50% of max
  profit. If assigned, sells covered calls ~6% above its cost basis. Max 4
  concurrent wheels, never more than 85% of options buying power committed.

WHY THIS FILE IS A SCANNER AND NOT A FIRM
  The village holds shares. It cannot hold, sell or be assigned an option, and
  the entire strategy is the option. There is no `propose` here that would be
  the wheel — a firm that bought these names outright would be a different
  strategy wearing the wheel's name, which is the one thing this file must not
  produce. So it publishes a reading instead, and the reading is deliberately
  weak.

WHAT THE READING MEANS, EXACTLY
  Selling a put 8% below spot expresses one modest opinion: *willing to own this
  8% lower*. That is the only directional content in the strategy, and it is the
  same for every name that clears the yield bar. So the score is small and
  nearly flat across the watchlist, and it is scaled only by whether realised
  volatility is high enough that a 21-day, 8%-OTM put could plausibly have paid
  15% annualised.
  Realised volatility is a PROXY FOR IMPLIED. They are not the same number, and
  the gap between them is the entire premium the wheel earns. This file cannot
  see that gap. A firm listening to this scanner is hearing the wheel's universe
  selection, not the wheel's edge.

EVIDENCE HEADLINE: 30 years of CBOE PutWrite vs SPY total return — Sharpe 0.83
vs 0.72, CAGR +8.49% vs +10.32%. A real risk-adjusted edge and a genuine return
loss. It won 1996-2008 and LOST 2009-19 and 2020-26; rolling 5-year windows
where it beat SPY on Sharpe fell from 57% since 1996 to 23% since 2020. Measured
separately: on volatility-normalised strikes, single names return the same as
the index at ~4.5x worse risk-adjusted (mean/sd 0.160 vs 0.712) — and this bot
trades the single names, because one SPY put ties up $69.7k against $57.6k of
options buying power.
"""

# Only ONE of the seven genes is a real number from wheel_bot, and even that one
# is a strike offset rather than a valuation band. The court's own note will say
# defaults are used for the rest; that note is accurate and should be believed.
GENOME = {
    "fair_band_pct": 8,     # NEAREST HONEST MAPPING — PUT_OTM_PCT = 0.08, the strike
                            # offset. The village reads this gene as a fair-value band,
                            # which is related but NOT the same quantity.
}

UNIVERSE = ["TSLA", "NVDA", "AMD", "AAPL", "AMZN", "PLTR",
            "COIN", "SOFI", "HOOD", "F", "INTC", "MSFT"]

# Real wheel_bot constants, kept here so the file states its own terms.
TARGET_DTE = 21
PUT_OTM_PCT = 0.08
CALL_OTM_PCT = 0.06
PROFIT_CLOSE = 0.50
MIN_ANN_YIELD = 0.15
MAX_CONCURRENT = 4

VOL_WINDOW = 21          # trading days, matched to TARGET_DTE
BASE_SCORE = 25.0        # "willing to own it 8% lower" — modest by construction
MAX_SCORE = 40.0


def _returns(closes):
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev:
            out.append(closes[i] / prev - 1.0)
    return out


def _stdev(xs):
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return var ** 0.5


def _plausible_annual_yield(sd_daily):
    """Roughly what a 21-day, 8%-OTM put could annualise at this volatility.

    A deliberately crude Bachelier-flavoured stand-in: premium scales with
    sigma * sqrt(t), discounted by how far out of the money the strike sits.
    It is NOT a pricing model and must not be read as one — it exists only to
    separate names where a 15% annualised yield is reachable from names where
    it plainly is not.
    """
    sigma_t = sd_daily * (TARGET_DTE ** 0.5)
    if sigma_t <= 0:
        return 0.0
    moneyness = PUT_OTM_PCT / sigma_t          # strike distance in sigmas
    if moneyness > 3.0:
        return 0.0
    premium_frac = sigma_t * 0.4 * max(0.0, 1.0 - moneyness / 3.0)
    return premium_frac * (365.0 / TARGET_DTE)


def scan(context):
    """Publish the wheel's willingness to own each watchlist name 8% lower."""
    readings = []
    for symbol in context.universe:
        if symbol not in UNIVERSE:
            continue                      # the watchlist is hand-picked; respect it
        closes = context.closes(symbol, VOL_WINDOW + 1)
        if not closes or len(closes) < VOL_WINDOW + 1:
            readings.append({"symbol": symbol, "score": 0, "confidence": 0,
                             "note": "not enough history to estimate volatility"})
            continue
        sd = _stdev(_returns(closes))
        if sd is None or sd <= 0:
            readings.append({"symbol": symbol, "score": 0, "confidence": 0,
                             "note": "no measurable volatility"})
            continue

        ann = _plausible_annual_yield(sd)
        if ann < MIN_ANN_YIELD:
            # wheel_bot would not sell this put at all. Silence, not a negative:
            # declining to sell a put is not a bearish opinion.
            readings.append({"symbol": symbol, "score": 0, "confidence": 0,
                             "note": f"realised vol implies ~{ann:.0%} annualised, "
                                     f"under the {MIN_ANN_YIELD:.0%} floor"})
            continue

        clearance = min(2.0, ann / MIN_ANN_YIELD)
        score = min(MAX_SCORE, BASE_SCORE * clearance)
        readings.append({
            "symbol": symbol,
            "score": round(score, 1),
            "confidence": 30,        # low on purpose: realised vol is not implied vol
            "note": (f"would sell a {TARGET_DTE}d put {PUT_OTM_PCT:.0%} OTM; "
                     f"realised vol implies ~{ann:.0%} annualised vs the "
                     f"{MIN_ANN_YIELD:.0%} floor. Premium leg invisible here."),
        })
    return readings
