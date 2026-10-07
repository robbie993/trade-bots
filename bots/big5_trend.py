"""BIG-5 TREND: the five most-traded US stocks, only while QQQ is in an uptrend.

Built from the Pelosi research (research/pelosi on branch claude/pelosi-backtest,
report village-health/pelosi-backtest-2026-10-07.md, section 12). Her filed
trades beat SPY mostly by holding the biggest, most-traded tech names, and a
public rule that does that on its own kept most of the result:

* each month-end, rank stocks by 63-day average dollar volume;
* hold the top five at equal weight for the month;
* but only while QQQ closes above its 200-day average; otherwise hold cash.

Backtest Dec 2014 to Oct 2026, 10 bps costs: 26.0%/yr, Sharpe 1.03, worst drop
-30.2%, against SPY 13.7%/yr and -33.7%. It also made money in 2021-22, where
every copy-Pelosi rule failed. **The QQQ filter was chosen after seeing 2022**,
so the backtest cannot vouch for it. This firm exists to be the honest test: the
rule is frozen here, run forward on paper, and judged on what it does from now.

Five names rather than ten because the village caps a firm at eight positions
and 25% of equity in one name; five at 20% fits both.

Every constant below is the backtest's. Changing one to make the forward record
look better throws away the only thing this firm is for.

**Two honest differences from the backtest.**
1. The backtest ranked every US stock each month. A bot sees only its universe,
   so the pool is fixed: the most-traded US stocks as of October 2026. Picking
   the pool today means it holds names that are big *now*; forward, that is no
   look-ahead, but it is not the same experiment as the history.
2. The backtest re-equalised all five weights each month. This bot only sells
   names that drop out and buys names that come in at 20%; winners it keeps are
   left to drift, which saves fees and is closer to how the edge was earned.
"""

from decimal import Decimal

TOP_N = 5
WEIGHT = Decimal("0.20")
RANK_BARS = 63            # 63 trading days, about three months
TREND_BARS = 200          # QQQ 200-day average
TREND = "QQQ"

#: Most-traded US common stocks by dollar volume, October 2026. One share class
#: each (GOOGL, not GOOG). QQQ is in the universe only as the trend gauge; it
#: is never bought.
POOL = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AVGO", "AMD",
    "PLTR", "NFLX", "MU", "LLY", "JPM", "COIN", "ORCL", "UNH", "COST", "V",
    "MA", "WMT", "XOM", "BAC", "CRM", "INTC", "MSTR", "SMCI", "ADBE", "QCOM",
    "HOOD",
]


def _cutoff(context):
    """Bars that close before the current month began, measured on QQQ.

    The decision is made on the last close of the previous month and held all
    month, as in the backtest. Freezing it this way also makes the bot
    idempotent: every tick in a month computes the same targets, so a tick that
    finds the book already matching proposes nothing.
    Returns the number of QQQ bars to keep, or None if it cannot tell.
    """
    times = context.times(TREND)
    if not times:
        return None
    now = context.as_of or times[-1]
    month = (now.year, now.month)
    keep = len(times)
    while keep > 0 and (times[keep - 1].year, times[keep - 1].month) == month:
        keep -= 1
    return keep or None


def _before(context, series_fn, symbol, cutoff_time):
    """A series trimmed to bars at or before ``cutoff_time``."""
    times = context.times(symbol)
    values = series_fn(symbol)
    if not times or len(times) != len(values):
        return []
    n = len(times)
    while n > 0 and times[n - 1] > cutoff_time:
        n -= 1
    return values[:n]


def targets(context):
    """(trend_on, [symbols]) as of the last close of the previous month.

    trend_on is None when there is not enough history to know, and the bot then
    does nothing at all rather than guess.
    """
    keep = _cutoff(context)
    if keep is None:
        return None, []
    qqq = context.closes(TREND)[:keep]
    if len(qqq) < TREND_BARS:
        return None, []
    sma = sum(qqq[-TREND_BARS:]) / Decimal(TREND_BARS)
    on = qqq[-1] > sma
    if not on:
        return False, []
    cutoff_time = context.times(TREND)[keep - 1]
    ranked = []
    for symbol in context.universe:
        if symbol == TREND:
            continue
        closes = _before(context, context.closes, symbol, cutoff_time)
        volumes = _before(context, context.volumes, symbol, cutoff_time)
        if len(closes) < RANK_BARS or len(volumes) != len(closes):
            continue
        dollar = sum(c * v for c, v in zip(closes[-RANK_BARS:], volumes[-RANK_BARS:]))
        ranked.append((dollar / Decimal(RANK_BARS), symbol))
    ranked.sort(reverse=True)
    return True, [s for _, s in ranked[:TOP_N]]


def propose(context):
    on, picks = targets(context)
    if on is None:
        return []
    orders = []
    held = [p.symbol for p in context.positions if p.quantity > 0]
    for symbol in held:
        if symbol not in picks:
            orders.append({
                "symbol": symbol,
                "side": "sell",
                "quantity": context.quantity(symbol),
                "rationale": ("QQQ below its 200-day average at month end: to cash"
                              if not on else "dropped out of the five most traded"),
            })
    for symbol in picks:
        if context.quantity(symbol) > 0:
            continue
        orders.append({
            "symbol": symbol,
            "side": "buy",
            "notional": context.equity * WEIGHT,
            "rationale": "top five by 3-month dollar volume, QQQ above its 200-day average",
        })
    return orders
