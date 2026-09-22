"""Cross-sectional momentum: own the strongest names, avoid the weakest.

**Why this one and not something invented.** The sibling repo tested a machine
learning forecaster (Kronos) against a plain 12-1 momentum rule on US equities
and the momentum rule won. That is the only positive finding in a stack of
nulls — the scanner tied a random control, the Form 4 signal was median
negative, short-horizon crypto TA was an anti-edge. So this file implements
the one thing that has actually beaten something in this project's own
measurements, rather than a fresh idea nobody has tested.

**What it does.** Ranks the universe by return over a lookback window, holds
the top third, and exits anything that falls out. Cross-sectional, not
absolute: it does not ask "is this going up", it asks "is this going up more
than its peers". That distinction is the whole point — an absolute rule buys
everything in a rally and a cross-sectional one always holds a relative
opinion.

**Honest limits, stated before any result.**

*The lookback is not 12-1.* Classic momentum skips the most recent month to
avoid short-term reversal, and measures over a year. This village keeps 720
bars of 15-minute data, which is 27 trading days — nowhere near a year. So
this is the same *shape* of rule on a much shorter window, and a short-window
momentum rule is a different claim from the one that beat Kronos. Do not read
a result here as confirming that one.

*27 days is not enough to conclude anything.* The village has 116 closed
trades in total. A rule with three parameters against that sample can be
fitted by accident. This exists to be measured, not to be believed.

*Costs are the thing that kills rules like this.* Three of the five bankruptcy
postmortems in this village blamed fees rather than direction, one at 1144% of
what its winning trades made. A rebalance on every bar would be worse than the
strategies that already died, which is why this only trades when the ranking
actually changes.
"""

from decimal import Decimal

#: Bars used to measure momentum. At 15m this is roughly two trading days —
#: short, and named here rather than buried so the evolver can find it.
LOOKBACK = 52

#: How much of the universe to hold. A third: enough names to be a portfolio
#: rather than a bet, few enough that the ranking means something.
TOP_FRACTION = Decimal("0.34")

#: Never put more than this share of equity in one name, whatever the rank
#: says. The risk manager enforces its own cap too; this is the strategy
#: declining to ask for something it should not want.
MAX_PER_NAME = Decimal("0.20")

# **Literal values, not references.** The court reads this file with `ast` and
# never executes it, which is the whole reason it is safe to accept a
# stranger's strategy at all. That means `float(TOP_FRACTION)` is unreadable to
# it — not because the value is wrong but because finding it would require
# running the file. The first submission of this file was rejected on exactly
# that, 60 to 0, and the court was right: a genome it cannot read is a genome
# it cannot range-check.
GENOME = {
    "lookback": 52,
    "top_fraction": 0.34,
    "max_per_name": 0.20,
}

#: Declared so the court can check it rather than falling back to the firm's.
UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "EFA", "XLK", "XLF", "XLE"]


def _momentum(context, symbol):
    """Return over the lookback, or None when there is not enough history.

    None rather than zero: a symbol with no history is unknown, and ranking an
    unknown alongside a measured one at zero would quietly place it in the
    middle of the field.
    """
    closes = context.closes(symbol, LOOKBACK)
    if not closes or len(closes) < LOOKBACK:
        return None
    first, last = closes[0], closes[-1]
    if not first:
        return None
    return (last - first) / first


def propose(context):
    """Hold the strongest third. Sell what drops out. Trade only on changes."""
    ranked = []
    for symbol in context.universe:
        score = _momentum(context, symbol)
        if score is not None:
            ranked.append((score, symbol))
    if len(ranked) < 3:
        return []                      # too few measurable names to rank

    ranked.sort(reverse=True)
    keep = max(1, int(len(ranked) * float(TOP_FRACTION)))
    wanted = {symbol for _, symbol in ranked[:keep]}

    orders = []

    # Exits first. A name that has fallen out of the top third is sold whatever
    # else happens this bar, because the cash it frees is what funds the entry
    # and because holding a loser to fund nothing is how a book silts up.
    for symbol in context.universe:
        held = context.quantity(symbol)
        if held > 0 and symbol not in wanted:
            orders.append({
                "symbol": symbol,
                "side": "sell",
                "quantity": held,
                "rationale": f"{symbol} left the top {keep} by {LOOKBACK}-bar return",
            })

    # Then entries, sized equally across the names not already held. Equal
    # weight rather than rank weight: with 27 days of history the difference
    # between first and third place is not information.
    missing = [s for s in wanted if context.quantity(s) <= 0]
    if not missing:
        return orders

    budget = context.cash / Decimal(len(missing))
    cap = context.equity * MAX_PER_NAME
    size = min(budget, cap)
    if size <= 0:
        return orders

    for symbol in missing:
        price = context.price(symbol)
        if not price or price <= 0:
            continue                   # no price, no order — never guess one
        rank = next(i for i, (_, s) in enumerate(ranked) if s == symbol) + 1
        orders.append({
            "symbol": symbol,
            "side": "buy",
            "notional": size,
            "rationale": (f"{symbol} ranks {rank} of {len(ranked)} on "
                          f"{LOOKBACK}-bar return"),
        })
    return orders
