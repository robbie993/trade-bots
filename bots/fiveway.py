"""FIVEWAY — the five-gate stack. Two of its five gates survive the trip here.

Source of truth: /Users/robbie/trade/fiveway_bot.py (Railway service BOT=fiveway).
Dossier: dossiers/fiveway.md. READ IT BEFORE GIVING THIS A SEAT.

WHAT THE REAL BOT DOES
  Runs once a day at 09:45 ET — post-open, deliberately not premarket, because
  premarket was measured at -0.120%/trade plus 48bp of spread. Over a universe
  frozen before the first run (the pre-registration forbids changing it), a name
  must clear five gates: the wheel's put-selling shape, the scanner's momentum
  screen, a Form 4 whale buy over $50k, a fresh Kronos forecast, and an
  indicator gate of RSI(14) > 50 AND WaveTrend(10,21) wt1 > wt2 on the prior
  close. Four slots. It then verifies a real, sellable option contract
  (bid >= $0.05, open interest >= 100) and records a paper signal. No orders.
  It REFUSES to emit at all if the Kronos file is more than 10 days old.

WHY THIS IS A SCANNER, AND ONLY PART OF ONE
  Three of the five gates cannot exist in a village bot: the whale gate needs
  SEC EDGAR, the Kronos gate needs a torch model and a weekly forecast file,
  and the wheel leg is an options position the village cannot hold. What is
  below is the indicator gate plus a momentum screen — 2 of 5 — publishing a
  reading rather than an order. Do not read a result from this file as a result
  about FIVEWAY. It is a different, much weaker thing wearing the same name.
  WaveTrend normally runs on hlc3; with closes only it runs on the close.

EVIDENCE HEADLINE — THE WHOLE FAMILY FAILED ITS OWN NULL. TWICE.
  1. In-sample placebo: a random gate firing at the same 49% rate gave p=0.087,
     and the gate was best-of-12, so the selection-corrected p is 0.665. FAIL.
  2. Family-wide, 2026-08-06: all 54 viable combinations were searched
     (scanner on/off x whale on/off x Kronos on/off x 13 gate choices). The
     deployed config ranked 15th of 54 at Sharpe +0.954; the best real combo
     reached +1.542. The identical 54-way search run on pure noise, 150 repeats,
     produced a median best-of-54 of +1.675 — HIGHER than the best real one.
     p=0.607. The best of 54 real combinations is worse than the median of what
     searching noise produces.
  Do not resurrect this by picking a different gate or a different subset. That
  search has been run and it is noise all the way down. FIVEWAY runs in
  production to be falsified, not because anyone believes it, and the bot says
  so in its own startup post.
"""

# Only FIVEWAY's indicator gate has genes. Its other four legs — the wheel,
# the scanner, the Form 4 whale watch and the Kronos forecast — are not
# parameters and are simply absent from anything the court can measure.
GENOME = {
    "fast_window": 10,      # REAL — WaveTrend n1
    "slow_window": 21,      # REAL — WaveTrend n2
    "rsi_window": 14,       # REAL — RSI(14) > 50 on the prior close
    "trend_bias": 100,      # REAL in spirit — every surviving leg is momentum
    "value_window": 30,     # DEFAULT — no analogue
    "fair_band_pct": 8,     # DEFAULT — no analogue
    "calm_vol_pct": 35,     # DEFAULT — no analogue
}

UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMD", "AMZN", "META", "TSLA", "PLTR",
            "COIN", "HOOD", "SOFI", "MU", "UBER", "INTC"]

BENCHMARK = "SPY"
SLOTS = 4
RSI_MIN = 50.0
WT_N1 = 10
WT_N2 = 21


def _sma(xs, n):
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


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


def _wavetrend(xs, n1=WT_N1, n2=WT_N2):
    """WaveTrend on closes. The real bot uses hlc3; there are no highs or lows here."""
    esa = _ema_series(xs, n1)
    if len(esa) < n1 + n2:
        return None, None
    src = xs[-len(esa):]
    d_in = [abs(src[i] - esa[i]) for i in range(len(esa))]
    d = _ema_series(d_in, n1)
    if not d:
        return None, None
    ci = []
    off = len(esa) - len(d)
    for i in range(len(d)):
        denom = 0.015 * d[i]
        if denom == 0:
            ci.append(0.0)
        else:
            ci.append((src[off + i] - esa[off + i]) / denom)
    tci = _ema_series(ci, n2)
    if len(tci) < 4:
        return None, None
    wt1 = tci[-1]
    wt2 = sum(tci[-4:]) / 4.0
    return wt1, wt2


def scan(context):
    bench = context.closes(BENCHMARK, 40)
    bench_ret20 = ((bench[-1] / bench[-21] - 1.0) * 100
                   if bench and len(bench) >= 21 else 0.0)

    rows = []
    for symbol in context.universe:
        if symbol == BENCHMARK:
            continue
        closes = context.closes(symbol, 160)
        price = context.price(symbol)
        if not closes or not price or len(closes) < 60:
            continue

        # The real bot evaluates the PRIOR close because it runs 15 minutes into
        # the session. Here the latest close is already a completed bar.
        r = _rsi(closes, 14)
        wt1, wt2 = _wavetrend(closes)
        if r is None or wt1 is None:
            continue
        if not (r > RSI_MIN and wt1 > wt2):
            continue                       # indicator gate: 1 of the 5

        ma50 = _sma(closes, 50)
        if ma50 is None or price <= ma50:
            continue                       # momentum screen: 2 of the 5
        rs = (price / closes[-21] - 1.0) * 100 - bench_ret20
        rows.append((symbol, rs, r))

    rows.sort(key=lambda t: -t[1])
    readings = {}
    for symbol, rs, r in rows[:SLOTS]:
        readings[symbol] = {
            "score": max(-100.0, min(100.0, rs * 3.0)),
            # Low confidence on purpose: three of five gates are missing and the
            # full stack failed its own null at p=0.607.
            "confidence": 20,
            "note": "2 of 5 gates only (RSI %.0f, WT up, above MA50); "
                    "no whale, no Kronos, no option leg" % r,
        }
    return readings
