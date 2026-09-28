"""ASTRAL — volatility rotation across a basket of correlated names, on
fifteen-minute bars, scaled in and out on volume.

One file, ten bots. Each Astral bot runs this same function on one of the
baskets in `BASKETS` below; what separates them is the basket they are handed.
The idea only works on names that move together, so each basket is a group the
market already treats as one trade: semiconductors, money-centre banks, gold,
the bitcoin proxies.

**Ten take-profit iterations** live beside it as `bots/astral_tp_*.py`. Plain
Astral has no take-profit: winners end by rotation, volume or momentum. Each
iteration keeps all of this and adds one far target, with a different idea of
where it belongs: R-multiple, expected daily move, measured move, range
projection, the basket's best run, fixed percent, volume-scaled, Fibonacci
extension, tiered thirds, statistical exhaustion. See `run` for the rules.

**Not in the village yet.** No firm names this file, so nothing runs it. To
wire one up, add a firm to `config/firm_config.yaml` with `bot: bots/astral.py`
and one basket as its `universe`. The crypto basket should also carry Alpaca's
crypto costs (`fee_bps: 25`, `slippage_bps: 5.3`, as `firm_c_crypto` does). A
50+ bps round trip is a lot for a 15m rotation to clear. Needs `TRADE_BAR=15m`.

## The rotation

Inside a correlated basket, the names share the direction and differ in how
hard they are moving. Each bar, every name is scored on:

* **risk-adjusted momentum**: the last `MOMENTUM_BARS` log return divided by
  what that name's own volatility says a move of that length should be. This
  is a z-score, so a 2% move in a quiet name outranks a 2% move in a wild one.
* **volatility expansion**: short realised vol over long realised vol. Capital
  rotates to where volatility is *expanding in the direction of the trend*,
  which is where a correlated group's move is being led from.

Score = z x expansion (expansion capped at 2x). The top `LEADERS` names by
score are the leaders. Capital sits in the leaders and rotates out of any
name that drops out of the top.

Three gates stop it rotating into noise. None of them apply to exits:

1. **The basket has to still be a basket.** Average pairwise correlation of
   fifteen-minute returns, lined up by timestamp, must be at least
   `MIN_CORRELATION`. When the group stops moving together, "the leader" means
   nothing, so no new positions open.
2. **The basket has to be going up.** Long only, so the average z across the
   basket must be positive. Leading a falling group is still falling.
3. **The leader has to be moving and expanding.** z at least `MIN_ENTRY_Z` and
   expansion at least `MIN_VOL_EXPANSION`.

## Volume scales the position

A full position is `TRANCHES` tranches, and volume decides how many to add or
cut each bar:

* **In.** A leader adds one tranche on an up bar with relative volume of at
  least `RVOL_ADD`, and two on a surge (`RVOL_SURGE`). A move nobody is trading
  gets no size.
* **Out on distribution.** A held leader cuts one tranche on a down bar with
  relative volume of at least `RVOL_DISTRIBUTION`: heavy selling into a leader
  is the first sign it is being handed off.
* **Out on rotation.** A held name that is no longer a leader cuts one tranche
  a bar, or two when the bar has heavy volume.
* **Out all at once** when its momentum turns negative, or it is
  `STOP_ATRS` ATRs under water. These two are unconditional and ignore volume.

**Relative volume is measured against the same fifteen-minute slot on
previous days**, lined up by timestamp. Intraday volume is U-shaped, heavy at
the open and close, so comparing the 09:30 bar with a plain rolling average
would call every open a surge and every lunch hour a drought. The feed also
carries extended hours, so "26 bars back" is not yesterday. When there are
fewer than `MIN_SAME_SLOT_DAYS` earlier bars in the same slot, as there
usually are for 24-hour crypto within the 250 bars the village keeps, it falls
back to the median of the last `RVOL_FALLBACK_BARS` bars. The median is used
so one opening print does not set the bar. Adds also need the bar to carry at
least `MIN_PARTICIPATION` of a typical bar's volume, so a thin pre-market
print with a big ratio cannot build a position.

## Sizing

A full position is `LEADER_WEIGHT` of equity, scaled down for names more
volatile than the basket's median (inverse vol, floored at half). Two
leaders at full size is 80% gross. The village's risk manager, conscience and
kill switch still review every order and can cut it; this is the bot's
opinion, and the smaller number wins.

## What it does not know

* No memory between bars. The number of tranches held is read back from the
  position's size against the current tranche. Equity and vol drift, so a
  "tranche" moves a little from bar to bar. That is fine for scaling and would
  be wrong for accounting.
* No session awareness beyond the volume filters. It does not flatten into
  the close.
* Windows are in **bars**, not hours or days. At 15m, `SLOW_VOL_BARS = 78` is
  three regular equity sessions and about 19.5 hours of crypto.
* Nothing here is fitted. The constants are round numbers picked before any
  backtest ran. Tune them against a backtest and the backtest becomes the
  thing you tuned to.
"""

import math
from decimal import Decimal

#: The ten baskets, one per bot. Nothing reads this at run time: the firm's
#: `universe` is what the bot trades. It is here so the baskets live with the
#: strategy until they have firms of their own. No name sits in two baskets.
BASKETS = {
    "semis": ["NVDA", "AMD", "AVGO", "MU", "TSM"],
    "megacap": ["AAPL", "MSFT", "GOOGL", "AMZN", "META"],
    "indices": ["SPY", "QQQ", "IWM", "DIA", "RSP"],
    "banks": ["JPM", "BAC", "C", "WFC", "GS"],
    "energy": ["XOM", "CVX", "COP", "OXY", "SLB"],
    "metals": ["GLD", "SLV", "GDX", "GDXJ", "NEM"],
    "btc_proxies": ["MSTR", "COIN", "MARA", "RIOT", "CLSK"],
    "airlines": ["DAL", "UAL", "AAL", "LUV", "ALK"],
    "homebuilders": ["DHI", "LEN", "PHM", "TOL", "KBH"],
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD"],
}

# -- windows, in fifteen-minute bars --------------------------------------
MOMENTUM_BARS = 16        # 4 hours
FAST_VOL_BARS = 8         # 2 hours
SLOW_VOL_BARS = 78        # 3 regular equity sessions
CORR_BARS = 78
ATR_BARS = 14
RVOL_FALLBACK_BARS = 26   # one regular equity session

# -- rotation --------------------------------------------------------------
LEADERS = 2
MIN_CORRELATION = 0.30
MIN_ENTRY_Z = 0.5
MIN_VOL_EXPANSION = 1.10
EXPANSION_CAP = 2.0

# -- volume ------------------------------------------------------------------
TRANCHES = 3
RVOL_ADD = 1.5
RVOL_SURGE = 2.5
RVOL_DISTRIBUTION = 1.5
MIN_SAME_SLOT_DAYS = 3
MIN_PARTICIPATION = 0.5

# -- risk ----------------------------------------------------------------------
LEADER_WEIGHT = Decimal("0.40")
MIN_SIZE_SCALE = 0.5
STOP_ATRS = 3.0

# -- take profit (the astral_tp_* iterations only) ------------------------------
#: No target closer than this many stop-distances (R) above entry.
MIN_TARGET_R = 2.0
#: The trail the astral_run_* iterations share: 4 ATR off the highest high of
#: the last session. The same for all ten, so they differ only in the target.
RUN_TRAIL = (26, 4.0)
EQUITY_BARS_PER_SESSION = 26
CRYPTO_BARS_PER_SESSION = 96


# =========================================================================
# arithmetic
# =========================================================================
def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _median(xs):
    if not xs:
        return None
    ordered = sorted(xs)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _stdev(xs):
    if len(xs) < 2:
        return None
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def _log_returns(prices):
    out = []
    for before, after in zip(prices, prices[1:]):
        if before <= 0 or after <= 0:
            return []
        out.append(math.log(after / before))
    return out


def _correlation(a, b):
    sa, sb = _stdev(a), _stdev(b)
    if not sa or not sb:
        return None
    ma, mb = _mean(a), _mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) - 1)
    return cov / (sa * sb)


def _slot(stamp):
    """The time-of-day a bar starts at, or None for a bar with no clock."""
    try:
        return (stamp.hour, stamp.minute)
    except AttributeError:
        return None


def _day(stamp):
    try:
        return stamp.date()
    except AttributeError:
        return None


# =========================================================================
# reading one name
# =========================================================================
def _relative_volume(volumes, times):
    """This bar's volume against the same slot on earlier days, and how much
    of a typical bar it is. (rvol, participation), or (None, None) when the
    feed carries no volume."""
    if len(volumes) < 2 or len(volumes) != len(times):
        return None, None
    now_volume, now_time = volumes[-1], times[-1]
    slot, today = _slot(now_time), _day(now_time)

    same_slot = []
    if slot is not None:
        same_slot = [v for v, t in zip(volumes[:-1], times[:-1])
                     if _slot(t) == slot and _day(t) != today]
    if len(same_slot) >= MIN_SAME_SLOT_DAYS:
        baseline = _mean(same_slot)
    else:
        baseline = _median(volumes[-(RVOL_FALLBACK_BARS + 1):-1])

    typical = _median(volumes[:-1])
    if not baseline or not typical:
        return None, None
    return now_volume / baseline, now_volume / typical


def _atr_pct(highs, lows, closes):
    """Average true range over ATR_BARS, as a fraction of the last close."""
    if len(highs) < ATR_BARS + 1 or len(highs) != len(closes) or len(lows) != len(closes):
        return None
    ranges = []
    for i in range(len(closes) - ATR_BARS, len(closes)):
        prev = closes[i - 1]
        ranges.append(max(highs[i] - lows[i], abs(highs[i] - prev), abs(lows[i] - prev)))
    return _mean(ranges) / closes[-1] if closes[-1] > 0 else None


def _read(context, symbol):
    """Everything the rotation needs to know about one name, or None."""
    need = max(SLOW_VOL_BARS, MOMENTUM_BARS, ATR_BARS) + 1
    closes = [float(c) for c in context.closes(symbol, need)]
    if len(closes) < need:
        return None

    returns = _log_returns(closes)
    if len(returns) < SLOW_VOL_BARS:
        return None
    slow_vol = _stdev(returns[-SLOW_VOL_BARS:])
    fast_vol = _stdev(returns[-FAST_VOL_BARS:])
    if not slow_vol or fast_vol is None:
        return None

    move = math.log(closes[-1] / closes[-1 - MOMENTUM_BARS])
    z = move / (slow_vol * math.sqrt(MOMENTUM_BARS))
    expansion = fast_vol / slow_vol

    opens = context.opens(symbol, 1)
    up_bar = closes[-1] > float(opens[-1]) if opens else closes[-1] > closes[-2]

    highs = [float(h) for h in context.highs(symbol, need)]
    lows = [float(x) for x in context.lows(symbol, need)]
    atr = _atr_pct(highs, lows, closes)
    if atr is None:
        # A close-only feed has no bar range. One bar's close-to-close vol is
        # the stand-in: smaller than a true range, so the stop sits tighter.
        atr = slow_vol

    volumes = [float(v) for v in context.volumes(symbol)]
    times = context.times(symbol)
    rvol, participation = _relative_volume(volumes, times[-len(volumes):] if volumes else [])

    return {
        "z": z,
        "expansion": expansion,
        "score": z * min(expansion, EXPANSION_CAP),
        "slow_vol": slow_vol,
        "up_bar": up_bar,
        "atr_pct": atr,
        "rvol": rvol,
        "participation": participation,
    }


def _ladder(context, symbol, read, basket, take_profit):
    """The take-profit ladder for one held name, cleaned up, or [].

    Every level is held at least ``MIN_TARGET_R`` stop-distances above the
    entry. Some ideas anchor to the chart rather than the entry, such as a
    range projection or a Fibonacci extension, and on a trade entered high in
    its range those can land at or under the entry. That is a target that
    fires on the next bar and banks nothing, so the floor lifts it.
    """
    if take_profit is None:
        return []
    entry = context.entry(symbol)
    if entry is None or entry <= 0 or not read["atr_pct"]:
        return []
    entry = float(entry)
    try:
        raw = take_profit(context, symbol, read, entry, basket) or []
    except (ValueError, ZeroDivisionError, TypeError):
        return []                             # an idea with no answer this bar
    floor = entry * (1 + MIN_TARGET_R * STOP_ATRS * read["atr_pct"])
    ladder = []
    for level, keep in raw:
        if level is None or not math.isfinite(level):
            continue
        ladder.append((max(float(level), floor), min(1.0, max(0.0, float(keep)))))
    # Rising price; where two levels were lifted to the same floor, the one
    # that keeps less comes last so it is the one that counts.
    return sorted(ladder, key=lambda step: (step[0], -step[1]))


def _basket_correlation(context, symbols):
    """Average pairwise correlation of returns, bar for bar by timestamp.

    Lined up by time, not by index: a thin name that skipped a bar would
    otherwise be compared with its neighbour's next bar, which reads as
    decorrelation that is not there.
    """
    series = {}
    for s in symbols:
        series[s] = dict(zip(context.times(s), (float(c) for c in context.closes(s))))

    pairs = []
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            shared = [t for t in context.times(a) if t in series[b]]
            shared = shared[-(CORR_BARS + 1):]
            if len(shared) < CORR_BARS // 2:
                continue
            ra = _log_returns([series[a][t] for t in shared])
            rb = _log_returns([series[b][t] for t in shared])
            if len(ra) != len(rb) or len(ra) < 2:
                continue
            rho = _correlation(ra, rb)
            if rho is not None:
                pairs.append(rho)
    return _mean(pairs)


# =========================================================================
# the strategy
# =========================================================================
def bars_per_session(symbol):
    """Fifteen-minute bars in one regular session: 26 for a US equity, 96 for
    crypto, which never closes. Crypto is spelled with a quote currency."""
    return CRYPTO_BARS_PER_SESSION if "-" in str(symbol) else EQUITY_BARS_PER_SESSION


def propose(context):
    return run(context)


def run(context, take_profit=None, trail=None):
    """The strategy. ``take_profit`` is how the astral_tp_* iterations differ.

    ``trail`` is ``(bars, atrs)`` or None, and only matters with a target. With
    it, a winner short of its target is no longer closed when momentum turns;
    it stays until the target, or until price falls ``atrs`` ATR below its
    highest high of the last ``bars`` bars. That is what lets a far target
    actually be reached: in the replay, the momentum exit ended almost every
    winner long before one. Losers keep every exit. The astral_run_*
    iterations use it; the astral_tp_* and astral_vs_* ones do not.

    It is called for each held name as ``take_profit(context, symbol, read,
    entry, basket)`` and returns a ladder: ``[(price, keep), ...]`` in rising
    price order, where ``keep`` is the fraction of a full position to still
    hold once price reaches that level. ``[(target, 0)]`` is one target that
    closes the whole trade. Plain Astral passes nothing and has no target.

    With a target in place, the target is how a winner ends: a position in
    profit and short of its target is not trimmed for rotation or volume. The
    stop and the momentum exit still apply, so a winner that rolls over is
    still sold.

    The bot has no memory, so the ladder is recomputed every bar from the
    current average entry and the current volatility. Adding a tranche moves
    the entry, and the target moves with it.
    """
    reads = {s: _read(context, s) for s in context.universe}
    live = {s: r for s, r in reads.items() if r is not None}
    if not live:
        return []

    correlation = _basket_correlation(context, list(live)) if len(live) > 1 else None
    basket_z = _mean([r["z"] for r in live.values()])
    median_vol = _median([r["slow_vol"] for r in live.values()])
    ranked = sorted(live, key=lambda s: live[s]["score"], reverse=True)
    leaders = {s for s in ranked[:LEADERS] if live[s]["score"] > 0}

    basket_ok = (correlation is not None and correlation >= MIN_CORRELATION
                 and basket_z is not None and basket_z > 0)
    basket_note = (f"basket corr {correlation:.2f}" if correlation is not None
                   else "basket corr unknown") + f", basket z {basket_z:+.2f}"

    sells, buys = [], []
    cash = context.cash

    for symbol in ranked:
        read = live[symbol]
        price = context.price(symbol)
        if price is None or price <= 0:
            continue
        held = context.quantity(symbol)
        if held < 0:
            continue                          # long only; a short is not ours

        scale = min(1.0, max(MIN_SIZE_SCALE, median_vol / read["slow_vol"]))
        full = context.equity * LEADER_WEIGHT * Decimal(str(round(scale, 4)))
        tranche = full / TRANCHES
        if tranche <= 0:
            continue
        tranche_qty = tranche / price
        held_value = held * price
        rvol = read["rvol"]
        heavy = rvol is not None and rvol >= RVOL_DISTRIBUTION
        tag = (f"z {read['z']:+.2f}, vol x{read['expansion']:.2f}, "
               + (f"rvol {rvol:.1f}" if rvol is not None else "no volume"))

        def sell(n, why, quantity=None):
            if quantity is None:
                quantity = held if n is None else min(held, tranche_qty * n)
            quantity = min(held, quantity)
            if held - quantity < tranche_qty / 2:
                quantity = held               # do not leave a crumb behind
            sells.append({"symbol": symbol, "side": "sell", "quantity": quantity,
                          "rationale": f"{why} ({tag})"})

        # -- exits, first and without conditions -----------------------------
        if held > 0:
            pnl = context.unrealized_pct(symbol)
            if (pnl is not None and read["atr_pct"]
                    and float(pnl) <= -STOP_ATRS * read["atr_pct"] * 100):
                sell(None, f"stop: {float(pnl):.2f}% is past {STOP_ATRS:g} ATR")
                continue

            ladder = _ladder(context, symbol, read, live, take_profit)
            if ladder:
                reached = [(level, keep) for level, keep in ladder if float(price) >= level]
                if reached:
                    level, keep = reached[-1]
                    excess = held_value - full * Decimal(str(keep))
                    if keep <= 0 or excess >= tranche / 4:
                        sell(None,
                             f"take profit: {float(price):.2f} reached target "
                             f"{level:.2f}, keeping {keep:.0%}",
                             quantity=None if keep <= 0 else excess / price)
                    continue                  # past a target: no adding back

            # A winner short of its target is left to reach it.
            running = bool(ladder) and pnl is not None and pnl > 0
            if running and trail:
                # ...and with a trail, momentum no longer ends it either. Only
                # the target or a fall of `atrs` ATR off the recent high does.
                bars, atrs = trail
                recent = context.highs(symbol, bars) or context.closes(symbol, bars)
                peak = float(max(recent)) if recent else float(price)
                floor = peak * (1 - atrs * read["atr_pct"])
                if float(price) < floor:
                    sell(None, f"trailing stop: {float(price):.2f} fell {atrs:g} ATR "
                               f"off the {bars}-bar high {peak:.2f}")
                    continue
            elif read["z"] <= 0:
                sell(None, "momentum gone")
                continue
            if symbol not in leaders and not running:
                sell(2 if heavy else 1, f"rotating out: ranked {ranked.index(symbol) + 1}")
                continue
            if heavy and not read["up_bar"] and not running:
                sell(1, "distribution: heavy volume on a down bar")
                continue

        # -- scaling in --------------------------------------------------------
        if symbol not in leaders or rvol is None or rvol < RVOL_ADD:
            continue
        if not read["up_bar"] or (read["participation"] or 0) < MIN_PARTICIPATION:
            continue
        if held == 0 and not (basket_ok and read["z"] >= MIN_ENTRY_Z
                              and read["expansion"] >= MIN_VOL_EXPANSION):
            continue

        n = 2 if rvol >= RVOL_SURGE else 1
        notional = min(tranche * n, full - held_value, cash)
        if notional < tranche / 4:
            continue                          # full, or nothing left to spend
        cash -= notional
        verb = "adding" if held > 0 else "opening"
        buys.append({"symbol": symbol, "side": "buy", "notional": notional,
                     "rationale": f"{verb} {n} tranche{'s' if n > 1 else ''}: leader "
                                  f"#{ranked.index(symbol) + 1} ({tag}; {basket_note})"})

    return sells + buys
