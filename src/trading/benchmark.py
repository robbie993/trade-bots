"""Did it beat just buying the index?

The village measures return, drawdown, win rate and Sharpe, and not one of them
answers the only question that decides whether any of this was worth doing. A
firm up 8% in a year when SPY did 12% has lost money in the way that matters,
and until now nothing here would have said so — it would have printed a
positive return, a healthy score, and proposed a raise.

So this is the scoreboard the stated goal actually needs.

**The comparison has to be honest, which is most of the work.**

*Same window.* A firm that started trading in March is compared against what
the index did from March, not from January. Comparing a firm's life to the
index's year is how every flattering backtest is built.

*Same money.* Buy-and-hold gets the firm's own starting capital, so the
comparison is "what if you had done nothing with this" rather than an abstract
percentage.

*Buy-and-hold pays to get in.* One entry, charged at the same slippage and fee
the paper venue charges the firm. Not charged to get out, because the
alternative being modelled is *still holding it* — and a benchmark that pays an
exit the firm has not paid is a benchmark tilted in the firm's favour.

*It refuses.* If the benchmark cannot be priced across the window, or the
window is too short to mean anything, the answer is the village's usual one —
insufficient data — and never a number. A comparison against a benchmark we
could not measure is worse than no comparison, because it looks like one.

**What beating the index does not mean.** Two months of outperformance is
noise, and this module says so rather than letting a good fortnight read as
skill. `verdict()` will not call a winner below `MIN_BARS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..money import D, ZERO, money, percent

#: What "the market" means unless told otherwise.
DEFAULT_BENCHMARK = "SPY"

#: Below this many bars, outperformance is a fortnight of luck. The village
#: refuses to call a winner and says which bar count it is waiting for.
MIN_BARS = 60


@dataclass
class Comparison:
    """One firm against buy-and-hold, over the bars the firm actually traded."""

    firm_key: str
    benchmark: str = DEFAULT_BENCHMARK
    bars: int = 0
    capital: Decimal = ZERO
    firm_equity: Decimal = ZERO
    hold_equity: Decimal = ZERO
    why_not: str = ""

    @property
    def measurable(self) -> bool:
        return not self.why_not and self.bars > 0 and self.capital > 0

    @property
    def firm_return_pct(self) -> Optional[Decimal]:
        if not self.measurable:
            return None
        return percent((self.firm_equity - self.capital) / self.capital * D(100))

    @property
    def hold_return_pct(self) -> Optional[Decimal]:
        if not self.measurable:
            return None
        return percent((self.hold_equity - self.capital) / self.capital * D(100))

    @property
    def excess_pct(self) -> Optional[Decimal]:
        """The only number that answers the question. Positive is winning."""
        a, b = self.firm_return_pct, self.hold_return_pct
        return None if a is None or b is None else percent(a - b)

    @property
    def enough_data(self) -> bool:
        return self.measurable and self.bars >= MIN_BARS

    def verdict(self) -> str:
        if not self.measurable:
            return f"{self.firm_key}: cannot be compared — {self.why_not}"
        excess = self.excess_pct
        head = (f"{self.firm_key}: {self.firm_return_pct}% against "
                f"{self.benchmark}'s {self.hold_return_pct}% "
                f"over {self.bars} bar(s)")
        if not self.enough_data:
            return (f"{head} — {excess}%, but {self.bars} bars is not a verdict. "
                    f"Ask again at {MIN_BARS}.")
        if excess > 0:
            return f"{head} — BEATING it by {excess}%"
        if excess < 0:
            return f"{head} — LOSING to it by {abs(excess)}%"
        return f"{head} — dead level"


def buy_and_hold(market, symbol: str, capital, bars: int,
                 slippage_bps=ZERO, fee_bps=ZERO) -> Optional[Decimal]:
    """What `capital` would be worth having bought `symbol` `bars` ago.

    Charged one entry at the venue's own costs, and no exit: the alternative
    being modelled is that you bought it and are still holding it.

    `None` when the benchmark cannot be priced across that window, which is a
    refusal rather than a zero — a benchmark we could not measure must not
    quietly become a benchmark of nothing.
    """
    start = money(capital)
    if start <= 0 or bars <= 0:
        return None
    try:
        closes = market.closes(symbol)
    except Exception:  # noqa: BLE001 - an unpriceable benchmark is a refusal
        return None
    if not closes or len(closes) < bars + 1:
        return None

    entry, now = D(closes[-(bars + 1)]), D(closes[-1])
    if entry <= 0 or now <= 0:
        return None

    # Buying crosses the spread and pays a fee, exactly as a firm's fills do.
    paid = entry * (D(1) + D(slippage_bps) / D(10_000))
    shares = (start * (D(1) - D(fee_bps) / D(10_000))) / paid
    return money(shares * now)


def bars_lived(store, firm_id) -> int:
    """Distinct market bars this firm has been measured across.

    Read from its own performance history rather than from the feed's length,
    because a firm created last week must be compared against what the index
    did last week — comparing a firm's life to the index's year is how every
    flattering backtest is built.
    """
    try:
        rows = store.db.query(
            "SELECT DISTINCT as_of FROM firm_performance WHERE firm_id = ?",
            (firm_id,),
        )
    except Exception:  # noqa: BLE001 - an un-migrated database has no history
        return 0
    return len({str(r["as_of"])[:10] for r in rows if r.get("as_of")})


def compare(firm, card, market, bars: int,
            benchmark: str = DEFAULT_BENCHMARK,
            slippage_bps=ZERO, fee_bps=ZERO) -> Comparison:
    """One firm against the index over the bars it has been alive.

    `card` supplies the firm's equity so this reads the same number the rest of
    the village reads, rather than recomputing it a second way and inviting the
    two to disagree.
    """
    out = Comparison(firm_key=firm.firm_key, benchmark=benchmark, bars=int(bars))
    out.capital = money(firm.initial_allocation or firm.allocation)
    out.firm_equity = money(card.equity) if card is not None else money(firm.cash)

    if card is not None and not card.can_be_valued:
        out.why_not = "this firm's own book cannot be valued"
        return out
    if out.capital <= 0:
        out.why_not = "it was never given any capital"
        return out
    if bars <= 0:
        out.why_not = "it has not traded a single bar yet"
        return out

    held = buy_and_hold(market, benchmark, out.capital, bars,
                        slippage_bps=slippage_bps, fee_bps=fee_bps)
    if held is None:
        out.why_not = (f"{benchmark} could not be priced across those "
                       f"{bars} bar(s)")
        return out
    out.hold_equity = held
    return out


def village(comparisons) -> dict:
    """The whole village against the index, in one line.

    Only firms that could be compared are counted. A village whose total is
    quietly propped up by firms nobody could measure is exactly the kind of
    headline this repository refuses elsewhere, and it is refused here too.
    """
    usable = [c for c in comparisons if c.measurable]
    skipped = [c for c in comparisons if not c.measurable]
    if not usable:
        return {"measurable": False, "firms": 0, "skipped": len(skipped),
                "summary": "nothing in this village can be compared to the index yet"}

    capital = sum((c.capital for c in usable), ZERO)
    equity = sum((c.firm_equity for c in usable), ZERO)
    held = sum((c.hold_equity for c in usable), ZERO)
    ours = percent((equity - capital) / capital * D(100))
    theirs = percent((held - capital) / capital * D(100))
    excess = percent(ours - theirs)
    beat = sum(1 for c in usable if (c.excess_pct or ZERO) > 0)

    verb = "BEATING" if excess > 0 else ("LOSING to" if excess < 0 else "level with")
    return {
        "measurable": True,
        "firms": len(usable),
        "skipped": len(skipped),
        "capital": capital,
        "equity": equity,
        "hold_equity": held,
        "return_pct": ours,
        "benchmark_pct": theirs,
        "excess_pct": excess,
        "beating": beat,
        "summary": (
            f"the village made {ours}% where buy-and-hold made {theirs}% — "
            f"{verb} the index by {abs(excess)}%. "
            f"{beat} of {len(usable)} firm(s) beat it individually"
            + (f"; {len(skipped)} could not be compared" if skipped else "")
        ),
    }


def table(comparisons) -> list:
    rows = []
    for c in comparisons:
        rows.append({
            "firm": c.firm_key,
            "bars": c.bars,
            "its return": "—" if c.firm_return_pct is None else f"{c.firm_return_pct}%",
            f"{c.benchmark} return": "—" if c.hold_return_pct is None
                                     else f"{c.hold_return_pct}%",
            "excess": "—" if c.excess_pct is None else f"{c.excess_pct}%",
            "verdict": ("not yet" if not c.enough_data
                        else ("beats it" if c.excess_pct > 0 else "loses to it")),
        })
    return rows


__all__ = ["Comparison", "DEFAULT_BENCHMARK", "MIN_BARS", "bars_lived",
           "buy_and_hold", "compare", "table", "village"]
