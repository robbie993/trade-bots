"""An example of a bot the village *listens to*, rather than one it runs a firm on.

A lot of what people have written is not a strategy. It is a scanner: it reads
the market, ranks what it sees, and names a symbol. Wired up as a firm's `bot:`
such a file looks broken — it never returns an order, so the firm proposes
nothing. Wire it up here instead.

Register it in `config/firm_config.yaml`:

    scanners:
      example:
        bot: bots/example_scanner.py
        # universe: [SPY, QQQ]     # optional — defaults to the whole village

and give any firm that should listen the `signals` analyst:

    firms:
      my_desk:
        universe: [SPY, QQQ, IWM]
        analysts: [technical, macro, signals]

The village then calls `scan(context)` once per market bar and stores what you
return. Firms with the `signals` seat hear it in their debate alongside their
own analysts.

**What you get.** The same `context` a strategy bot gets, with the money zeroed
out — you have no firm, no cash and no positions, because you are not trading:

    context.universe            the symbols to look at
    context.as_of               the timestamp of the latest bar
    context.price(symbol)       the latest mark, or None
    context.closes(symbol, n)   the last n closes, oldest first

**What you return.** Whichever of these is most natural for what you wrote:

    {"AAPL": 80, "MSFT": -20}                       symbol -> score
    {"AAPL": {"score": 80, "confidence": 60}}       symbol -> reading
    [{"symbol": "AAPL", "score": 80, "note": "…"}]  a list of readings

`score` runs -100 (bearish) to +100 (bullish). `confidence` runs 0 to 100,
where **0 means silence, not neutrality** — say 0 when you have nothing rather
than making something up, and the debate will correctly hear nothing. Leave
confidence out and the strength of your own score is used.

A bare list of tickers is refused. A name with no direction cannot vote, and
the village will not guess a direction on your behalf.

**What you cannot do.** Move money. There is no path from a published reading
to an order: your score joins a debate, the debate reaches the trader, and the
trader's proposal still meets the risk manager, the conscience, the venue and
the approval gate. Publishing +100 on everything wins you one seat in one
argument. If this function raises or hangs, the tick prints why and carries on
with every firm hearing silence from you.
"""

from decimal import Decimal


LOOKBACK = 30


def _mean(values):
    return sum(values) / Decimal(len(values)) if values else None


def scan(context):
    """Rank the universe by how far each name sits above its own 30-bar mean.

    Deliberately simple: the point of the example is the shape of the answer,
    not the cleverness of the screen.
    """
    readings = []
    for symbol in context.universe:
        closes = context.closes(symbol, LOOKBACK)
        if len(closes) < LOOKBACK:
            continue                       # not enough history: say nothing
        average = _mean(closes)
        if not average:
            continue

        stretch = (closes[-1] - average) / average * Decimal(100)
        # Above its mean reads bullish here, and how far above decides how
        # loud. Clamping is the village's job, so overshooting is harmless.
        score = stretch * Decimal(8)
        if abs(score) < 5:
            continue                       # nothing worth interrupting anyone for

        readings.append(
            {
                "symbol": symbol,
                "score": score,
                "confidence": min(Decimal(70), abs(score)),
                "note": f"{stretch:+.1f}% vs its {LOOKBACK}-bar mean",
            }
        )
    return readings
