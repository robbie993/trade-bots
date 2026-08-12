"""An example of a bot the village *runs*, rather than one it translates.

The other two examples in this folder declare `GENOME` and `UNIVERSE`, and the
village reads them without ever executing the file. That works when your
strategy is seven numbers. When the logic lives in a function — which is most
real strategies — declare a function instead and let the village call it.

Point a firm at this file in `config/firm_config.yaml`:

    firms:
      my_desk:
        name: "My Desk"
        asset_class: Equities
        capital_allocation: 50000
        universe: [SPY, QQQ]
        bot: bots/example_adapter.py

The village then calls `propose(context)` once per tick. What you return goes
through exactly the same review as anything the built-in analysts produce: the
risk manager can cut your size, the conscience can block the trade, the kill
switch still watches the firm, and capital still stops at the approval gate.
You decide what you want; you do not decide what happens.

**What you get.** One argument, `context`, holding only what a strategy needs:

    context.universe            the symbols this firm may trade
    context.cash                spendable cash, as a Decimal
    context.equity              cash plus the marked value of what you hold
    context.as_of               the timestamp of the latest bar
    context.price(symbol)       the latest mark, or None
    context.closes(symbol, n)   the last n closes, oldest first
    context.position(symbol)    what you hold, or None
    context.quantity(symbol)    how much, as a Decimal — 0 if nothing

**What you return.** A list of dicts. `symbol` and `side` are required, plus
either `quantity` or `notional`. Anything else is ignored.

    {"symbol": "SPY", "side": "buy", "notional": 5000, "rationale": "..."}

**Rules worth knowing.** A symbol outside the firm's universe is dropped. A
non-positive quantity is dropped. Returning nothing is fine and means "no
opinion today". If this function raises, the firm has a quiet tick and the
reason is printed in the tick summary — it cannot take the village down.

Use `Decimal` for anything that becomes money. Floats are fine for indicators.
"""

from decimal import Decimal

FAST = 10
SLOW = 30

# Never put more than this fraction of equity into one position. The risk
# manager enforces its own limit regardless — this is your opinion, not the
# village's, and the smaller of the two wins.
MAX_WEIGHT = Decimal("0.20")


def _average(values):
    return sum(values) / Decimal(len(values)) if values else None


def propose(context):
    """A plain moving-average crossover, written the way you would write it."""
    orders = []

    for symbol in context.universe:
        closes = context.closes(symbol, SLOW)
        if len(closes) < SLOW:
            continue                      # not enough history to have a view

        fast = _average(closes[-FAST:])
        slow = _average(closes)
        held = context.quantity(symbol)
        price = context.price(symbol)
        if price is None or price <= 0:
            continue

        if fast > slow and held == 0:
            budget = min(context.cash, context.equity * MAX_WEIGHT)
            if budget > price:
                orders.append({
                    "symbol": symbol,
                    "side": "buy",
                    "notional": budget,
                    "rationale": f"{FAST}-bar mean {fast:.2f} above {SLOW}-bar {slow:.2f}",
                })

        elif fast < slow and held > 0:
            orders.append({
                "symbol": symbol,
                "side": "sell",
                "quantity": held,
                "rationale": f"{FAST}-bar mean {fast:.2f} fell below {SLOW}-bar {slow:.2f}",
            })

    return orders
