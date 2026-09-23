"""PICKS_TRADER — buys only the picks the market confirms the next morning.

Source of truth: /Users/robbie/trade/picks_trader.py (co-hosted in the Railway
wheel service). Dossier: dossiers/picks_trader.md.

WHAT THE REAL BOT DOES
  It does not pick anything. It reads the Form 4 whale watchlist and the
  scanner's list, then buys a name only if the market confirms it the next
  morning: green versus the previous close AND at least $300k of dollar volume.
  Conviction floor score >= 50. $1,000 per position, 3 whale picks per list.
  Tries the premarket window 07:00-09:25 ET with extended-hours limit orders
  capped 1% over last trade, retries no more than every 30 minutes, then tops
  up in regular hours 09:45-11:30. Exits: -7% stop, +10% target, 7-day time
  stop. Fills are verified against the broker rather than assumed.

WHAT CHANGED TO FIT THE VILLAGE — THE PICK SOURCE IS GONE
  This is the important one. The real bot's input is an external list produced
  by form4_bot and scanner_bot from SEC EDGAR and Alpaca. A village bot has no
  network, so THERE IS NO PICK LIST HERE. What survives is the confirmation
  rule and the exit geometry, applied to whatever universe the firm is given.
  That means this file is testing the *filter*, not the strategy: the real
  question — are whale picks worth buying — cannot be asked in the village.
  Also gone: the premarket window (the village ticks on daily bars), the
  dollar-volume floor (no volume in context), and the pick score.

EVIDENCE HEADLINE
  The whale signal it consumes was measured and does not survive contact with
  costs — see dossiers/form4.md: the insider edge is 83% priced in the
  overnight gap before retail can act, and a placebo on random dates is within
  noise of the real signal. The confirmation filter implemented below has never
  been tested on its own.
  LEDGER DEFECT STILL OPEN: the 2026-07-14 ledger row says "sold 21 YPF" and
  the broker sold 4. Recorded realised P&L for picks_trader is overstated until
  that is corrected — do not treat its ledger as evidence in the meantime.
"""

# NO GENOME, DELIBERATELY. picks_trader computes no moving average, no RSI and
# no valuation band — it buys names another bot named, if the market confirms
# them, and manages -7% / +10% / 7 days. Writing seven genes here would be
# inventing parameters it does not have, so the court's "no readable genome"
# refusal is the correct verdict, not a packaging mistake. It still runs as a
# firm's bot: — that path reads the function below, not the genome.

UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMD", "AMZN", "GOOGL", "META", "TSLA",
            "PLTR", "COIN", "HOOD", "SOFI", "F", "INTC", "MU", "UBER"]

POS_USD = 1000.0
MAX_POSITIONS = 3
STOP_PCT = -7.0
TARGET_PCT = 10.0
MAX_HOLD_BARS = 7          # calendar days in the real bot; bars here


def propose(context):
    orders = []
    open_n = sum(1 for s in context.universe if context.quantity(s) > 0)

    for symbol in context.universe:
        # Floats at the boundary, and only here — see sentinel.py. The
        # context serves Decimal because it also serves cash; the maths
        # below is the real bot's, in float. Mixing them raises TypeError
        # on the first arithmetic, which is why this port never ran.
        closes = [float(c) for c in context.closes(symbol, 30)]
        price = context.price(symbol)
        price = float(price) if price is not None else None
        if not closes or not price or len(closes) < 10:
            continue
        held = context.quantity(symbol)

        if held > 0:
            # Entry price is not exposed by context, so the stop and target are
            # measured against the 7-bar-ago close. That is a proxy for the real
            # bot's entry-anchored exits, and a loose one.
            anchor = closes[-min(MAX_HOLD_BARS, len(closes))]
            move = (price / anchor - 1.0) * 100
            if move <= STOP_PCT:
                orders.append({"symbol": symbol, "side": "sell", "quantity": held,
                               "rationale": "PICKS stop %.1f%%" % move})
            elif move >= TARGET_PCT:
                orders.append({"symbol": symbol, "side": "sell", "quantity": held,
                               "rationale": "PICKS target %.1f%%" % move})
            else:
                orders.append({"symbol": symbol, "side": "sell", "quantity": held,
                               "rationale": "PICKS 7-bar time stop"})
            continue

        if open_n >= MAX_POSITIONS:
            continue

        # The confirmation rule, minus the pick list it is supposed to confirm.
        confirmed = price > closes[-2]
        if not confirmed:
            continue
        orders.append({"symbol": symbol, "side": "buy", "notional": POS_USD,
                       "rationale": "PICKS confirmation: green vs prior close "
                                    "(NO whale/scanner pick list in the village)"})
        open_n += 1
    return orders
