"""The shadow desk — options on paper, on the side the evidence actually favours.

**It sells premium.** The first version of this file bought it, and three
independent measurements say that was the wrong side of the same trade:

* 210,509 bar-days of 2024 option history: buy a contract and hold five days
  and the mean return is **-12.98%**, the median **-45.28%**, winning 23.4% of
  the time — before any spread;
* the Alpaca account's own 21 written contracts, imported into this table:
  **+$2,931 realised** across 13 closed, every one of them short;
* the sibling repo's wheel backtest at Sharpe 0.84 and a 94.4% win rate, and
  `option_bot.py`'s evidence base — the Cboe PUT index over 32 years at
  +1.61%/yr alpha, Sharpe 0.65 against the S&P's 0.49.

Buying premium pays theta every day. Selling it collects theta every day. The
village's live account has been on the collecting side and is up; this desk was
built on the paying side and measured how reliably it loses.

**Why it still cannot do this for real.** `risk_manager` blocks a written
option because no fraction-of-capital limit can size an unbounded loss, and
that rule is right. A shadow desk risks nothing — its worst outcome is a row in
a table — so it can explore the side the ledger must not, and produce the
evidence that would justify ever changing that rule.

**What keeps it honest.**

*A seller receives the bid and pays the ask to close.* The mirror of a buyer,
and stated explicitly, because getting this backwards would manufacture the
exact edge the desk exists to test.

*Real and imagined never mix.* Imported Alpaca fills carry `source='real'`;
this desk writes `source='shadow'`. A score blending them would be part
evidence and part invention with no way to separate them afterwards.

*Its knobs are genes.* The first version hardcoded every constant, so the
evolver had nothing to change and the desk could have run for a year without
learning anything. They are in `GENES` now, and mutants face the same held-out
test that rejected three firm genomes on 31 August.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from ..money import D, ZERO, money
from .data.options_feed import AlpacaOptionFeed

DESK = "shadow_options"

#: Genes this desk is tuned on. Registered in `brain/evolver.GENES` so the
#: evolver can actually move them — the whole reason the first version could
#: not learn is that its equivalents were module constants.
GENE_DEFAULTS = {
    "shadow_dte_min": 21,
    "shadow_dte_max": 45,
    "shadow_strike_sd": 1.0,      # how far below spot to write, in realised SD
    "shadow_spread_cap": 15.0,    # round-trip % beyond which a contract is refused
    "shadow_confidence": 20.0,
}


@dataclass
class ShadowReport:
    opened: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _gene(genome, name):
    try:
        return D(str((genome or {}).get(name, GENE_DEFAULTS[name])))
    except Exception:  # noqa: BLE001 - a malformed gene is the default gene
        return D(str(GENE_DEFAULTS[name]))


def _strike_of(occ: str) -> Optional[Decimal]:
    try:
        return D(int(occ[-8:])) / D(1000)
    except (ValueError, TypeError):
        return None


def _realised_sd(closes) -> Optional[Decimal]:
    """Standard deviation of recent returns. None when it cannot be measured.

    Used to place the strike rather than to forecast anything: writing "one SD
    below spot" is the rule `option_bot.py` settled on, and its own header notes
    that every entry filter tried on top of it made results worse.
    """
    if not closes or len(closes) < 20:
        return None
    rets = [(b - a) / a for a, b in zip(closes, closes[1:]) if a]
    if len(rets) < 10:
        return None
    mean = sum(rets, ZERO) / D(len(rets))
    var = sum(((r - mean) ** 2 for r in rets), ZERO) / D(len(rets))
    return var.sqrt() if var > 0 else None


class ShadowDesk:
    """Writes puts on paper, records what it would have collected."""

    name = DESK

    def __init__(self, store, board, universe=None, feed=None, genome=None):
        self.store = store
        self.board = board
        self.feed = feed or AlpacaOptionFeed()
        self.universe = list(universe or ("SPY", "QQQ", "IWM"))
        self.genome = dict(genome or {})

    # -- its own book ------------------------------------------------------
    def open_trades(self) -> list:
        try:
            return self.store.db.query(
                "SELECT * FROM shadow_trades WHERE desk = ? AND source = 'shadow' "
                "AND closed_at IS NULL", (DESK,)) or []
        except Exception:  # noqa: BLE001
            return []

    def _holds(self, underlying: str) -> bool:
        return any(str(r["underlying"]) == underlying for r in self.open_trades())

    # -- the bar -----------------------------------------------------------
    def run(self, market, as_of=None) -> ShadowReport:
        report = ShadowReport()
        as_of = as_of if as_of is not None else market.as_of()
        if as_of is None:
            return report
        bar = str(as_of)

        self._settle(bar, report)

        dte_min = int(_gene(self.genome, "shadow_dte_min"))
        dte_max = int(_gene(self.genome, "shadow_dte_max"))
        sd_mult = _gene(self.genome, "shadow_strike_sd")
        cap = _gene(self.genome, "shadow_spread_cap")
        min_conf = _gene(self.genome, "shadow_confidence")

        for underlying in self.universe:
            if self._holds(underlying):
                continue
            reading = self.board.reading(underlying, as_of)
            if reading is None or D(reading.confidence) < min_conf:
                continue
            # A put seller wants the underlying to stay up. A bearish reading is
            # a reason not to write, not a reason to write a call — the account's
            # own record is 21 short contracts and this desk does not invent a
            # strategy the evidence has not seen.
            if D(reading.score) < 0:
                continue

            spot = D(market.mark(underlying))
            sd = _realised_sd(market.closes(underlying, 60))
            if spot <= 0 or sd is None:
                continue
            target = spot * (D(1) - sd * sd_mult)

            today = date.today()
            quotes = self.feed.chain(
                underlying,
                expiry_gte=(today + timedelta(days=dte_min)).isoformat(),
                expiry_lte=(today + timedelta(days=dte_max)).isoformat())
            if not quotes:
                report.notes.append(
                    f"{underlying}: no chain — "
                    f"{self.feed.last_error.get(underlying, 'nothing quoted')}")
                continue

            best, gap = None, None
            for quote in quotes:
                if quote.symbol[-9] != "P":
                    continue                      # puts only
                if quote.round_trip_pct > cap or quote.bid <= 0:
                    continue
                strike = _strike_of(quote.symbol)
                if strike is None or strike >= spot:
                    continue                      # out of the money only
                distance = abs(strike - target)
                if gap is None or distance < gap:
                    best, gap = quote, distance

            if best is None:
                report.refused.append(
                    f"{underlying}: no OTM put inside a {cap}% spread near "
                    f"{target:.2f} ({sd_mult}sd below {spot:.2f})")
                continue

            self.store.db.insert("shadow_trades", {
                "desk": DESK,
                "source": "shadow",
                "contract": best.symbol,
                "underlying": underlying,
                "side": "sell",
                "quantity": 1,
                # A seller is filled at the bid. The mirror of a buyer, and the
                # single place where getting the sign wrong would invent an edge.
                "entry_price": str(best.sell_at),
                "entry_mid": str(best.mid),
                "spread_pct": str(best.round_trip_pct),
                "reason": (f"wrote {sd_mult}sd below {spot:.2f}; "
                           f"{reading.note[:60]} (conf {reading.confidence})"),
                "opened_bar": bar,
            })
            report.opened.append(
                f"SOLD {best.symbol} at {best.sell_at} "
                f"(mid {best.mid}, spread {best.round_trip_pct:.1f}%)")
        return report

    def _settle(self, bar: str, report: ShadowReport) -> None:
        """Buy the written put back, at the ask, when it can be quoted."""
        for row in self.open_trades():
            quotes = {q.symbol: q for q in self.feed.chain(str(row["underlying"]))}
            quote = quotes.get(str(row["contract"]))
            if quote is None or quote.ask <= 0:
                continue
            received = D(str(row["entry_price"]))
            # Sold at the bid, bought back at the ask: premium kept minus cost.
            realised = money((received - quote.buy_at) * D(str(row["quantity"])) * D(100))
            self.store.db.execute(
                "UPDATE shadow_trades SET exit_price = ?, realized = ?, "
                "closed_bar = ?, closed_at = ? WHERE id = ?",
                (str(quote.buy_at), str(realised), bar,
                 datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 row["id"]))
            report.closed.append(
                f"BOUGHT BACK {row['contract']} at {quote.buy_at} ({realised:+})")

    # -- the scoreboard ----------------------------------------------------
    def record(self, source: str = "shadow") -> dict:
        """What this desk made, and what the real account made, side by side."""
        try:
            rows = self.store.db.query(
                "SELECT realized FROM shadow_trades WHERE desk = ? AND source = ? "
                "AND closed_at IS NOT NULL", (DESK, source)) or []
        except Exception:  # noqa: BLE001
            return {"closed": 0}
        if not rows:
            return {"closed": 0, "net": money(ZERO), "win_rate": 0.0}
        values = [D(str(r["realized"] or 0)) for r in rows]
        wins = sum(1 for v in values if v > 0)
        return {
            "closed": len(values),
            "net": money(sum(values, ZERO)),
            "win_rate": round(100.0 * wins / len(values), 1),
        }


__all__ = ["ShadowDesk", "ShadowReport", "DESK", "GENE_DEFAULTS"]
