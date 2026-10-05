"""The too-good-to-be-true alarm.

Every method the AI-trading sellers advertise was run on real prices on
2026-10-05 (`atlas_research/scam_tests`). What they screenshot is real: a
martingale bot on BTC won 1,557 deals out of 1,557, a DCA bot won all 377, and a
"top bot" picked from a thousand random ones made +21%. What they leave out is
also real: the martingale made a quarter of buy-and-hold and dropped to -4% the
moment it had a stop, and the top ten bots lost 7.6% the next month.

So in this village a dazzling number is not celebrated, it is checked. This
module names three patterns and nothing else:

* **A win rate of 90% or more** over enough closed trades. Honest strategies
  rarely get there; a book that never closes a loser usually means losers are
  being held open (martingale, grid, "no stop" bots), which is a drawdown that
  has not been booked yet.
* **A big lead over SPY on few trades.** Ten points ahead on under thirty
  closed trades is one lucky run or hidden leverage until shown otherwise.
* **A scanner whose calls win 90% or more** in the idea lab, for the same
  reason.

An alarm moves no money and blocks nothing. It is a note for a person: look
here before believing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from ..money import D

WIN_RATE_PCT = D("90")
MIN_CLOSED = 10
LEAD_POINTS = D("10")
FEW_TRADES = 30
SCANNER_MIN_CLOSED = 20


@dataclass(frozen=True)
class Alarm:
    who: str
    kind: str          # "firm" | "scanner"
    what: str
    check: str

    def __str__(self) -> str:
        return f"too good to be true? {self.who}: {self.what} — {self.check}"


def _dec(value) -> Optional[Decimal]:
    try:
        return None if value is None else D(value)
    except Exception:  # noqa: BLE001
        return None


def firm_alarms(cards: Iterable) -> list:
    """Alarms for firms, from the brokerage's scorecards."""
    out = []
    for card in cards:
        who = str(getattr(card, "firm_key", "") or getattr(card, "firm_id", "?"))
        closed = int(getattr(card, "closed_trades", 0) or 0)
        win = _dec(getattr(card, "win_rate_pct", None))
        if win is not None and closed >= MIN_CLOSED and win >= WIN_RATE_PCT:
            out.append(Alarm(who, "firm", f"wins {win}% of {closed} closed trades",
                             "check its open positions for losers it has not closed, "
                             "and whether it uses a stop"))
        ret = _dec(getattr(card, "return_pct", None))
        spy = _dec(getattr(card, "spy_pct", None))
        if ret is not None and spy is not None and 0 < closed < FEW_TRADES \
                and ret - spy >= LEAD_POINTS:
            out.append(Alarm(who, "firm",
                             f"{ret - spy:+.2f} points ahead of SPY on only {closed} closed trades",
                             "one lucky run or concentration until more trades say otherwise"))
    return out


def scanner_alarms(records: Iterable) -> list:
    """Alarms for scanners, from the idea lab's scoreboard records."""
    out = []
    for r in records:
        closed = int(getattr(r, "closed", 0) or 0)
        wins = getattr(r, "won", None)
        if wins is None or closed < SCANNER_MIN_CLOSED:
            continue
        rate = D(wins) * 100 / D(closed)
        if rate >= WIN_RATE_PCT:
            out.append(Alarm(f"{r.publisher} ({r.horizon}{', outside' if r.outside else ''})",
                             "scanner",
                             f"calls won {rate:.0f}% of {closed} closed ideas",
                             "check whether the calls are scored after costs and against SPY"))
    return out


__all__ = ["Alarm", "firm_alarms", "scanner_alarms"]
