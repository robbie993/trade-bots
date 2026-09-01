"""The shadow desk — a firm that trades options on paper and never on the book.

Three separate things stop this village trading options for real, and all three
are correct:

* it **cannot write** them. `risk_manager` blocks a sale with no holding,
  because no fraction-of-capital limit can size an unbounded loss;
* it **cannot afford** to. One cash-secured SPY put needs about $76,600 of
  collateral at a $766 underlying; the firms here hold $20,000;
* what remains — buying premium — is the thing the sibling repo's 2026-07-31
  study found dies on cost. It measured a real signal of roughly +0.5% per 21
  days against round-trip spreads near 52% of premium.

So this desk does not trade. It records what it *would* have bought, at the
price it would actually have paid, and marks it every bar. The point is not to
pretend it made money — it is that **117 closed trades is the binding
constraint on everything in this village**: the evolver, the strike system, the
scanner scorecard and the court are all starved of evidence, and a desk that
generates measurable decisions at no risk is worth more than one that generates
none.

**What makes it honest.**

*Entries fill at the ask and exits at the bid.* Never the mid. The mid is what
turns a 52%-spread study into a headline, and `options_feed.Quote` makes the
cost a property so that no path here can avoid it.

*It refuses its own trades.* A contract past the spread gate is not entered,
and it is recorded as refused rather than silently skipped, so the log shows
what the cost rejected as well as what it liked.

*It cannot reach the ledger.* Its table is `shadow_trades`. It never writes
`fills`, `positions` or `cash`. The reconciliation identity broke twice on 31
August and the one thing that must never break it is a research toy.

*It reads the same board as everyone else.* The scanner, the news desk and the
scribe publish readings; this desk hears them exactly as a firm's `signals`
seat does, at the same confidence, with no privileged access. If those sources
are nulls — and the scanner measurably is — this desk will faithfully lose
imaginary money, which is the correct outcome and the reason to run it on paper
first.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from ..money import D, ZERO, money
from .data.options_feed import AlpacaOptionFeed, MAX_ROUND_TRIP_PCT

DESK = "shadow_options"

#: How far out to look for contracts. Short enough that a directional view has
#: to be right soon, long enough that theta is not the entire trade.
MIN_DAYS, MAX_DAYS = 7, 45

#: A reading has to be at least this confident before the desk acts on it.
#:
#: **Set just above the median reading, not high.** The first version used 40,
#: reasoning that an option starts a quarter of the premium behind so a
#: marginal opinion cannot justify one. That sounds right and conflates two
#: different things: `confidence` is a publisher's certainty, not an expected
#: move, and nothing has ever established a relationship between them — the
#: scorecard measured both publishers as nulls the same afternoon.
#:
#: So 40 was an arbitrary number that happened to exclude 93% of all readings
#: ever published, which would starve this desk of the only thing it exists to
#: collect. The desk risks nothing; the cost of a bad shadow trade is a row in
#: a table. The cost of too few is that nothing is ever learned.
#:
#: 20 sits just above the median of 16.2, so the desk acts on the better half
#: of what it hears and the confidence of every entry is recorded with it —
#: which is what lets the question "did more confident readings do better?" be
#: asked from data rather than assumed in a constant.
MIN_CONFIDENCE = D(os.environ.get("TRADE_SHADOW_MIN_CONFIDENCE", "20"))

#: Contracts per entry. One, because sizing is not what this desk is measuring
#: and a number here would only add a parameter nobody can justify from 117
#: closed trades.
CONTRACTS = 1


@dataclass
class ShadowReport:
    opened: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _atm(quotes, underlying_price: Decimal, want_call: bool):
    """The tradable contract closest to the money, or None.

    Closest to the money because that is where a directional view lives. Deep
    in-the-money contracts quote at 0.2% round trip and are stock with extra
    steps; far out-of-the-money ones quote at 200% and are lottery tickets.
    """
    best, best_gap = None, None
    for quote in quotes:
        if not quote.tradable:
            continue
        # OCC: root, YYMMDD, C/P, then the strike in thousandths.
        tail = quote.symbol[-9:]
        right, strike_raw = tail[0], tail[1:]
        if (right == "C") != want_call:
            continue
        try:
            strike = D(int(strike_raw)) / D(1000)
        except (ValueError, TypeError):
            continue
        gap = abs(strike - underlying_price)
        if best_gap is None or gap < best_gap:
            best, best_gap = quote, gap
    return best


class ShadowDesk:
    """Reads the board, writes to `shadow_trades`, touches nothing else."""

    name = DESK

    def __init__(self, store, board, universe=None, feed=None):
        self.store = store
        self.board = board
        self.feed = feed or AlpacaOptionFeed()
        self.universe = list(universe or ("SPY", "QQQ", "IWM"))

    # -- reading its own book ---------------------------------------------
    def open_trades(self) -> list:
        try:
            return self.store.db.query(
                "SELECT * FROM shadow_trades WHERE desk = ? AND closed_at IS NULL",
                (DESK,),
            ) or []
        except Exception:  # noqa: BLE001 - no table yet is not a crash
            return []

    def _already_open(self, underlying: str) -> bool:
        return any(str(r["underlying"]) == underlying for r in self.open_trades())

    # -- the tick ----------------------------------------------------------
    def run(self, market, as_of=None) -> ShadowReport:
        report = ShadowReport()
        as_of = as_of if as_of is not None else market.as_of()
        if as_of is None:
            return report
        bar = str(as_of)

        # Mark and exit first, so a closing trade frees the underlying for a
        # new opinion on the same bar rather than a bar later.
        self._settle(bar, report)

        for underlying in self.universe:
            if self._already_open(underlying):
                continue
            reading = self.board.reading(underlying, as_of)
            if reading is None or abs(D(reading.confidence)) < MIN_CONFIDENCE:
                continue
            score = D(reading.score)
            if score == 0:
                continue

            price = D(market.mark(underlying))
            if price <= 0:
                continue

            today = date.today()
            quotes = self.feed.chain(
                underlying,
                expiry_gte=(today + timedelta(days=MIN_DAYS)).isoformat(),
                expiry_lte=(today + timedelta(days=MAX_DAYS)).isoformat(),
            )
            if not quotes:
                report.notes.append(
                    f"{underlying}: no chain — "
                    f"{self.feed.last_error.get(underlying, 'nothing quoted')}")
                continue

            pick = _atm(quotes, price, want_call=score > 0)
            if pick is None:
                worst = min((q.round_trip_pct for q in quotes), default=D(0))
                report.refused.append(
                    f"{underlying}: nothing inside the {MAX_ROUND_TRIP_PCT}% "
                    f"spread gate (best {worst:.1f}%)")
                continue

            self.store.db.insert("shadow_trades", {
                "desk": DESK,
                "contract": pick.symbol,
                "underlying": underlying,
                "side": "buy",
                "quantity": CONTRACTS,
                "entry_price": str(pick.buy_at),      # the ask, always
                "entry_mid": str(pick.mid),
                "spread_pct": str(pick.round_trip_pct),
                "reason": (f"{reading.note[:80]} (score {score}, "
                           f"confidence {reading.confidence})"),
                "opened_bar": bar,
            })
            report.opened.append(
                f"{pick.symbol} at {pick.buy_at} "
                f"(mid {pick.mid}, spread {pick.round_trip_pct:.1f}%)")
        return report

    def _settle(self, bar: str, report: ShadowReport) -> None:
        """Close anything whose contract can still be quoted, at the bid."""
        for row in self.open_trades():
            underlying = str(row["underlying"])
            quotes = {q.symbol: q for q in self.feed.chain(underlying)}
            quote = quotes.get(str(row["contract"]))
            if quote is None or quote.bid <= 0:
                continue                     # still open, or unquotable
            entry = D(str(row["entry_price"]))
            realized = money((quote.sell_at - entry) * D(CONTRACTS) * D(100))
            self.store.db.execute(
                "UPDATE shadow_trades SET exit_price = ?, realized = ?, "
                "closed_bar = ?, closed_at = ? WHERE id = ?",
                (str(quote.sell_at), str(realized), bar,
                 __import__("datetime").datetime.utcnow()
                 .strftime("%Y-%m-%dT%H:%M:%SZ"), row["id"]),
            )
            report.closed.append(f"{row['contract']} at {quote.sell_at} "
                                 f"({realized:+})")

    # -- the scoreboard ----------------------------------------------------
    def record(self) -> dict:
        """What the desk would have made, and what the spread took."""
        try:
            rows = self.store.db.query(
                "SELECT realized, entry_price, entry_mid, spread_pct FROM "
                "shadow_trades WHERE desk = ? AND closed_at IS NOT NULL", (DESK,)
            ) or []
        except Exception:  # noqa: BLE001
            return {}
        if not rows:
            return {"closed": 0}
        net = sum((D(str(r["realized"] or 0)) for r in rows), ZERO)
        # What the same trades would have shown filling at the mid — the
        # flattering number, kept beside the real one so the gap is visible.
        paid_over_mid = sum(
            ((D(str(r["entry_price"] or 0)) - D(str(r["entry_mid"] or 0)))
             * D(100) for r in rows), ZERO)
        wins = sum(1 for r in rows if D(str(r["realized"] or 0)) > 0)
        return {
            "closed": len(rows),
            "net": money(net),
            "win_rate": round(100.0 * wins / len(rows), 1),
            "spread_paid": money(paid_over_mid),
            "net_at_mid": money(net + paid_over_mid),
        }


__all__ = ["ShadowDesk", "ShadowReport", "DESK"]
