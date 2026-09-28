"""Volatility rotation, scaled in and out on volume.

One engine, shared by the ten bots in ``bots/vol_rotation/``. Each of those
files is a universe of correlated tickers and a handful of numbers; the logic
lives here, once, where it is tested.

**The idea.** Correlated names move together, so the question is never
*whether* to own the group but *which member* is worth owning right now. Each
bar, every name is scored on risk-adjusted momentum — its recent log return
divided by the volatility it took to get there, a t-statistic in all but
name. The best `hold` names are the targets. A name whose volatility has
exploded relative to its own baseline is not eligible, however good its
score: a spike is when correlated names stop being correlated.

**Sizing is volatility's job.** A target's full size is ``max_weight`` of
equity, cut back by ``target_vol / realised_vol`` when the name is running
hotter than the book wants. When volatility rises under a position, its
target shrinks and the excess is trimmed.

**Timing is volume's job.** The full size is split into ``tranches``, and
nothing moves a tranche except a bar that traded on real volume:

* **In** — a target adds a tranche on an up bar, above its volume-weighted
  average price, trading at ``rvol_in`` times its normal volume. Two tranches
  on a surge (``rvol_surge``). A quiet up bar adds nothing: a move nobody
  traded is a move nobody believes.
* **Out** — a name rotated out of the target set is sold a tranche a bar,
  two when the bar is heavy, all of it when a surge prints on a down bar.
  A target that prints a heavy down bar below VWAP gives back a tranche —
  that is distribution, and waiting for the momentum score to notice costs
  exactly the move it is late for.
* **All out** — momentum turned negative, or volatility spiked past
  ``max_vol_ratio``. No tranches; the position goes.

Held names get ``switch_margin`` added to their score before ranking, so a
challenger has to be clearly better, not better by a rounding error, to
cause a rotation. Without it a group of near-identical names churns fees
every bar.

**What this does not do.** It does not short, it does not lever, and it does
not know what time it is — the bar length comes from ``TRADE_BAR_TIMEFRAME``
and the only place it matters is ``bars_per_year``, which annualises the
volatility. A feed with no volume (some CSVs, some FX) disables the volume
gate rather than freezing the bot: every bar then counts as normal volume.

Everything the engine decides still goes through the firm's risk manager,
conscience and approval gate. It proposes; it does not execute.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Optional

from . import take_profit

# Bars in a trading year, for annualising a per-bar volatility.
EQUITY_15M_BARS_PER_YEAR = 26 * 252     # 6.5 hours of 15m bars, 252 sessions
CRYPTO_15M_BARS_PER_YEAR = 96 * 365     # 24 hours, every day


@dataclass(frozen=True)
class Params:
    vol_window: int = 32            # bars of realised vol for scoring (8h of 15m)
    long_vol_window: int = 128      # the baseline a vol spike is measured against
    mom_window: int = 16            # bars of momentum (4h of 15m)
    volume_window: int = 32         # bars of average volume and of VWAP
    hold: int = 1                   # names held at once
    tranches: int = 3               # a full position is this many pieces
    min_score: float = 0.5          # risk-adjusted momentum needed to be a target
    switch_margin: float = 0.5      # a challenger must beat a holding by this
    rvol_in: float = 1.2            # relative volume that lets a tranche in
    rvol_out: float = 1.2           # relative volume that makes an exit heavy
    rvol_surge: float = 2.0         # two tranches in, or everything out
    max_vol_ratio: float = 2.0      # short vol / long vol above this = spike
    max_weight: float = 0.25        # a full position, as a fraction of equity
    target_vol: float = 0.40        # annualised vol a full position is sized to
    bars_per_year: int = EQUITY_15M_BARS_PER_YEAR
    # Take profit — see take_profit.py. Empty means none, which is the
    # original behaviour exactly: every exit is a soft exit.
    take_profit: str = ""
    atr_window: int = 32            # bars of average true range
    tp_atr: float = 8.0             # target distance, in ATRs
    tp_r: float = 6.0               # target, in multiples of the risk
    tp_stop_atr: float = 1.5        # the risk, 1R, in ATRs
    tp_sigma: float = 2.0           # target distance, in standard deviations
    tp_horizon: int = 130           # bars the sigma move is measured over
    tp_lookback: int = 128          # bars for swing highs, ranges and bands
    tp_mult: float = 2.0            # multiple of a range or a leg
    tp_trail_atr: float = 5.0       # trailing distance, in ATRs
    tp_arm_atr: float = 2.0         # gain before a trail arms, in ATRs
    tp_climax_rvol: float = 4.0     # relative volume that counts as a climax

    @classmethod
    def from_mapping(cls, raw: Optional[dict]) -> "Params":
        """Build from a bot's plain dict, refusing names this engine does not have.

        A misspelt key silently falling back to a default is how a bot runs
        for a month on parameters nobody chose.
        """
        raw = dict(raw or {})
        known = {f.name: f.type for f in fields(cls)}
        unknown = sorted(set(raw) - set(known))
        if unknown:
            raise ValueError(f"unknown vol_rotation parameter(s): {', '.join(unknown)}")
        defaults = cls()
        typed = {k: type(getattr(defaults, k))(v) for k, v in raw.items()}
        made = cls(**typed)
        if made.take_profit and made.take_profit not in take_profit.MODES:
            raise ValueError(
                f"unknown take_profit {made.take_profit!r}; expected one of "
                + ", ".join(take_profit.MODES)
            )
        if made.take_profit and made.history > 250:
            raise ValueError("take-profit lookbacks need more than the 250 bars a bot is given")
        return made

    @property
    def warmup(self) -> int:
        """Bars a name needs before it can be scored at all."""
        return max(self.long_vol_window, self.mom_window, self.volume_window) + 1

    @property
    def history(self) -> int:
        """Bars to ask the context for: the warm-up, and the take-profit's reach."""
        if not self.take_profit:
            return self.warmup + 1
        return max(self.warmup, self.tp_lookback + 1, self.atr_window + 1) + 1


# =========================================================================
# reading one name
# =========================================================================
@dataclass(frozen=True)
class Reading:
    symbol: str
    price: float
    score: float            # risk-adjusted momentum
    annual_vol: float       # short-window realised vol, annualised
    vol_ratio: float        # short vol / long vol
    rvol: float             # this bar's volume against its recent average
    vwap: float
    up_bar: bool
    down_bar: bool

    def spiking(self, p: Params) -> bool:
        return self.vol_ratio > p.max_vol_ratio


def _stdev(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def read(symbol: str, bars: list, p: Params) -> Optional[Reading]:
    """Score one name from its bars, or None when there is not enough history."""
    if len(bars) < p.warmup:
        return None
    closes = [float(b.close) for b in bars]
    if any(c <= 0 for c in closes[-p.warmup:]):
        return None
    logret = [math.log(b / a) for a, b in zip(closes, closes[1:])]

    vol = _stdev(logret[-p.vol_window:])
    long_vol = _stdev(logret[-p.long_vol_window:])
    if vol <= 0 or long_vol <= 0:
        return None
    momentum = math.log(closes[-1] / closes[-1 - p.mom_window])
    score = momentum / (vol * math.sqrt(p.mom_window))

    window = bars[-p.volume_window:]
    volumes = [float(b.volume) for b in window]
    previous = volumes[:-1]
    average = sum(previous) / len(previous) if previous else 0.0
    # No volume on this feed: the gate opens rather than jamming shut.
    rvol = volumes[-1] / average if average > 0 else p.rvol_in
    traded = sum(volumes)
    if traded > 0:
        vwap = sum(float(b.close) * float(b.volume) for b in window) / traded
    else:
        vwap = sum(float(b.close) for b in window) / len(window)

    last = bars[-1]
    return Reading(
        symbol=symbol,
        price=closes[-1],
        score=score,
        annual_vol=vol * math.sqrt(p.bars_per_year),
        vol_ratio=vol / long_vol,
        rvol=rvol,
        vwap=vwap,
        up_bar=float(last.close) > float(last.open),
        down_bar=float(last.close) < float(last.open),
    )


# =========================================================================
# choosing and sizing
# =========================================================================
def targets(readings: dict, held: set, p: Params) -> list:
    """The names worth owning this bar, best first.

    Eligible means scored above `min_score` and not in a vol spike. Held names
    rank with `switch_margin` added, so a rotation needs a clear winner.
    """
    eligible = [r for r in readings.values() if r.score > p.min_score and not r.spiking(p)]
    ranked = sorted(
        eligible,
        key=lambda r: r.score + (p.switch_margin if r.symbol in held else 0.0),
        reverse=True,
    )
    return [r.symbol for r in ranked[: p.hold]]


def full_size(reading: Reading, equity: float, p: Params) -> float:
    """What a full position in this name is worth, after the vol haircut."""
    scale = min(1.0, p.target_vol / reading.annual_vol) if reading.annual_vol > 0 else 1.0
    return equity * p.max_weight * scale


# =========================================================================
# the bot
# =========================================================================
def propose(context, params=None) -> list:
    """Orders for one bar. Sells first, so their cash can fund the buys."""
    p = params if isinstance(params, Params) else Params.from_mapping(params)
    equity = float(context.equity)
    cash = float(context.cash)

    readings, history = {}, {}
    for symbol in context.universe:
        history[symbol] = context.bars(symbol, p.history)
        reading = read(symbol, history[symbol], p)
        if reading is not None:
            readings[symbol] = reading

    held = {s for s in context.universe if context.quantity(s) > 0}
    ranked = targets(readings, held, p)
    wanted = set(ranked)
    sells, buys = [], []
    no_adds = set()

    # -- out: everything held that is no longer a target, and trims --------
    for symbol in sorted(held):
        quantity = float(context.quantity(symbol))
        r = readings.get(symbol)
        if r is None:
            continue                    # no read on it this bar; do not guess
        size = full_size(r, equity, p)
        tranche = size / p.tranches if size > 0 else 0.0
        value = quantity * r.price

        if p.take_profit:
            position = context.position(symbol)
            entry = float(position.average_price) if position else 0.0
            decision = take_profit.decide(history[symbol], entry, r, p, value, size)
            if not decision.allow_adds:
                no_adds.add(symbol)
            if decision.sell_fraction > 0:
                sells.append(_sell(
                    symbol, min(quantity, quantity * decision.sell_fraction),
                    f"{decision.why} (entry {entry:.2f}, now {r.price:.2f})",
                ))
                continue
            if r.price > entry > 0:
                # A winner. Only the take-profit closes it, so none of the
                # soft exits below get a say. See take_profit.py.
                continue

        if r.score < 0 or r.spiking(p):
            why = ("momentum turned negative" if r.score < 0
                   else f"vol spiked to {r.vol_ratio:.1f}x its baseline")
            sells.append(_sell(symbol, quantity, f"all out: {why} (score {r.score:.2f})"))
            continue

        if symbol not in wanted:
            pieces = 1
            if r.rvol >= p.rvol_out:
                pieces = 2
            if r.rvol >= p.rvol_surge and r.down_bar:
                pieces = p.tranches
            sells.append(_sell_tranches(
                symbol, quantity, value, tranche, pieces, r,
                f"rotated out (score {r.score:.2f}); {pieces} tranche(s) on "
                f"{r.rvol:.1f}x volume",
            ))
            continue

        # Still a target. Distribution: heavy selling below VWAP.
        if r.down_bar and r.rvol >= p.rvol_out and r.price < r.vwap:
            sells.append(_sell_tranches(
                symbol, quantity, value, tranche, 1, r,
                f"distribution: down bar on {r.rvol:.1f}x volume under VWAP "
                f"{r.vwap:.2f}",
            ))
            continue

        # Vol rose under the position, so its full size shrank.
        if size > 0 and value > size * 1.10:
            sells.append(_sell(
                symbol, (value - size) / r.price,
                f"trim to vol-sized target: {r.annual_vol:.0%} annualised vs "
                f"{p.target_vol:.0%} target",
            ))

    # -- in: add a tranche to a target on a bar that traded ------------------
    spendable = cash + sum(float(o["quantity"]) * readings[o["symbol"]].price for o in sells)
    # A winner held past its rotation still fills a slot, so a new leader
    # waits for it rather than doubling the book.
    selling = {o["symbol"] for o in sells}
    stale = [s for s in held if s not in wanted and s not in selling]
    slots = p.hold - len(stale) if p.take_profit else p.hold
    for symbol in ranked:
        r = readings[symbol]
        if symbol in no_adds:
            continue
        if symbol not in held:
            if slots <= 0:
                continue
            slots -= 1
        size = full_size(r, equity, p)
        if size <= 0:
            continue
        tranche = size / p.tranches
        value = float(context.quantity(symbol)) * r.price
        have = round(value / tranche) if tranche > 0 else p.tranches
        if have >= p.tranches:
            continue
        if not (r.up_bar and r.price > r.vwap and r.rvol >= p.rvol_in):
            continue
        pieces = 2 if r.rvol >= p.rvol_surge else 1
        pieces = min(pieces, p.tranches - have)
        notional = min(pieces * tranche, max(0.0, size - value), spendable * 0.95)
        if notional <= 0 or notional < tranche * 0.25:
            continue
        spendable -= notional
        buys.append({
            "symbol": symbol,
            "side": "buy",
            "notional": Decimal(str(round(notional, 2))),
            "confidence": min(100, int(r.score * 25)),
            "rationale": (
                f"scale in {pieces} tranche(s) ({have}->{have + pieces} of {p.tranches}): "
                f"score {r.score:.2f}, {r.rvol:.1f}x volume, above VWAP "
                f"{r.vwap:.2f}, vol {r.annual_vol:.0%}"
            ),
        })

    return sells + buys


def _sell(symbol: str, quantity: float, why: str) -> dict:
    return {
        "symbol": symbol,
        "side": "sell",
        "quantity": Decimal(str(quantity)),
        "rationale": why,
    }


def _sell_tranches(symbol, quantity, value, tranche, pieces, r, why) -> dict:
    """Sell `pieces` tranches, or everything when what would be left is dust."""
    if tranche <= 0 or value - pieces * tranche < tranche * 0.25:
        return _sell(symbol, quantity, why)
    return _sell(symbol, min(quantity, pieces * tranche / r.price), why)


__all__ = [
    "CRYPTO_15M_BARS_PER_YEAR", "EQUITY_15M_BARS_PER_YEAR", "Params", "Reading",
    "full_size", "propose", "read", "targets",
]
