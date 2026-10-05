"""The congress desk: stock trades members of Congress disclosed, as calls the
idea lab can test.

Found by the repo scout on Hugging Face: `austin-starks/congressional-stock-trades`,
an audited, point-in-time copy of the House and Senate periodic transaction
reports, refreshed about every 20 hours. Every row carries `availableAt`, the
moment the public could first read it, so a call is made when the filing became
public and never on the trade date the member knew about weeks earlier. The
median filing in 2026 landed 26 days after the trade, which is the reason to be
doubtful about this signal and the reason to measure it rather than argue.

**What it publishes.** For each stock disclosed in the last `WINDOW` (three
days, so a filing that lands on a Friday night is still being called when the
market opens on Monday), one reading: purchases are buys and sales are sells,
netted across every member who filed on the name. Its size and confidence grow
with the dollars reported, from the bottom of the $1,001-$15,000 band to a
million or more. A sale counts half as loudly as a purchase, because members
sell for a hundred reasons that have nothing to do with the company: taxes,
a house, rebalancing a spouse's account.

**Where it goes.** The signal board, like every scanner. The idea lab then
trades each call on paper for an hour, a day and a week and scores it against
SPY after costs; almost every name is outside the village's universe, so the
lab prices it from Alpaca. A firm only hears it on a symbol it trades, and the
board already turns a scanner that trails SPY down to a quarter of its voice.

**The law on this data.** 5 U.S.C. 13107(c) makes it unlawful to use these
reports "for any commercial purpose" other than news. Scoring them on paper in
a sandbox is research. Trading real money on them is a decision for a person,
not for this file.

Off unless `TRADE_CONGRESS_ENABLED` is set: it reaches the open internet.
Reading the dataset's Parquet files needs `pyarrow`.
"""

from __future__ import annotations

import io
import math
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

from .signals import Reading

DATASET = "https://huggingface.co/datasets/austin-starks/congressional-stock-trades"
#: One file per year of `availableAt`, a couple of hundred kilobytes each.
SHARD = DATASET + "/resolve/main/data/political_trade_events/{year}-00000-of-00001.parquet"
HEADERS = {"User-Agent": "village-congress-desk"}

#: How long after a filing became public it is still called.
WINDOW = timedelta(days=3)
#: Seconds between downloads. The dataset refreshes about every 20 hours.
EVERY_S = 6 * 3600

#: Stocks only: the dataset's code for a stock, and no code at all, which is
#: how most Senate rows print a listed share. Options, bonds and funds are not.
STOCK_CODES = (None, "ST")
ACTIONS = {"purchase": 1, "sale": -1}
SALE_WEIGHT = 0.5

#: Dollars at which a call reaches its floor and its ceiling.
FLOOR_USD, CEILING_USD = 1_000.0, 1_000_000.0
FLOOR, CEILING = 20.0, 90.0


def _when(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        at = value
    else:
        try:
            at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)   # the dataset's stamps are UTC
    return at


def loudness(dollars: float) -> float:
    """$1,000 or less is 20, $1,000,000 or more is 90, logarithmic between."""
    if dollars <= FLOOR_USD:
        return FLOOR
    if dollars >= CEILING_USD:
        return CEILING
    share = math.log10(dollars / FLOOR_USD) / math.log10(CEILING_USD / FLOOR_USD)
    return FLOOR + (CEILING - FLOOR) * share


def live_events(rows, now: datetime, window: timedelta = WINDOW) -> list:
    """The stock trades that became public within `window` before `now`.

    Point in time: an event is used only if it was available by `now` and had
    not been replaced by an amendment by then.
    """
    out = []
    for r in rows:
        ticker = str(r.get("ticker") or "").strip().upper()
        action = str(r.get("action") or "").lower()
        if not ticker or action not in ACTIONS:
            continue
        if r.get("assetTypeCode") not in STOCK_CODES:
            continue
        available = _when(r.get("availableAt"))
        if available is None or available > now or now - available > window:
            continue
        superseded = _when(r.get("supersededAt"))
        if superseded is not None and superseded <= now:
            continue
        out.append(r)
    return out


def readings_from(events) -> list:
    """One reading per ticker: dollars bought minus half the dollars sold."""
    names: dict = {}
    for e in events:
        ticker = str(e["ticker"]).strip().upper()
        low = float(e.get("amountLow") or 0)
        high = float(e.get("amountHigh") or low)
        dollars = (low + high) / 2 if high else low
        sign = ACTIONS[str(e["action"]).lower()]
        slot = names.setdefault(ticker, {"net": 0.0, "buys": set(), "sells": set()})
        slot["net"] += sign * dollars * (1.0 if sign > 0 else SALE_WEIGHT)
        who = str(e.get("displayName") or e.get("filerLast") or "?")
        (slot["buys"] if sign > 0 else slot["sells"]).add(who)

    readings = []
    for ticker, slot in sorted(names.items()):
        net = slot["net"]
        if net == 0:
            continue
        size = loudness(abs(net))
        score = size if net > 0 else -size
        parts = []
        if slot["buys"]:
            parts.append(f"bought by {', '.join(sorted(slot['buys'])[:3])}")
        if slot["sells"]:
            parts.append(f"sold by {', '.join(sorted(slot['sells'])[:3])}")
        note = f"congress filings: {'; '.join(parts)}; net ${net:+,.0f}"
        readings.append(Reading(ticker, Decimal(f"{score:.2f}"), Decimal(f"{size:.2f}"),
                                note[:200]))
    return readings


def read_shard(raw: bytes) -> list:
    """A year's Parquet file as a list of dicts. Needs pyarrow."""
    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(raw), columns=[
        "ticker", "action", "assetTypeCode", "amountLow", "amountHigh", "availableAt",
        "supersededAt", "displayName", "filerLast", "transactionDate"])
    return table.to_pylist()


class CongressDesk:
    name = "congress"

    def __init__(self, board, get_bytes: Optional[Callable] = None, clock=time.time):
        self.board = board
        self._clock = clock
        self._fetched = 0.0
        self._rows: list = []
        if get_bytes is None:
            import urllib.request

            from .data.feeds import _ssl_context

            def get_bytes(url):
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30,  # noqa: S310 - fixed host
                                            context=_ssl_context()) as r:
                    return r.read()
        self._get = get_bytes

    def _refresh(self, now: datetime) -> list:
        """Download this year's file (and last year's, early in January)."""
        failed = []
        if self._clock() - self._fetched < EVERY_S and self._rows:
            return failed
        years = {now.year, (now - WINDOW).year}
        rows = []
        for year in sorted(years):
            try:
                rows += read_shard(self._get(SHARD.format(year=year)))
            except ImportError:
                return ["congress desk needs pyarrow to read the dataset; pip install pyarrow"]
            except Exception as exc:  # noqa: BLE001 - one source is not the tick
                failed.append(f"congress dataset {year}: {str(exc)[:80]}")
        if rows or not failed:
            self._rows, self._fetched = rows, self._clock()
        return [f"congress desk source FAILING — {f}" for f in failed]

    def run(self, market, as_of=None) -> list:
        as_of = as_of if as_of is not None else market.as_of()
        if as_of is None or self.board.published(self.name, as_of):
            return []
        now = _when(as_of) or datetime.now(timezone.utc)
        notes = self._refresh(now)
        readings = readings_from(live_events(self._rows, now))
        if readings:
            self.board.publish(self.name, readings, as_of)
            notes.append(f"congress desk: {len(readings)} name(s) filed in the last "
                         f"{WINDOW.days} days: " + ", ".join(
                             f"{r.symbol} {r.score:+.0f}" for r in readings[:6])
                         + (f" (+{len(readings) - 6} more)" if len(readings) > 6 else ""))
        elif self._rows:
            self.board.mark_silent(self.name, as_of)
        return notes


__all__ = ["CongressDesk", "live_events", "loudness", "read_shard", "readings_from"]
