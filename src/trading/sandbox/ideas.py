"""The idea lab — every call a scanner makes, traded on paper and scored.

A scanner's reading is a vote. It joins one seat of one firm's debate, is
averaged with the technical, fundamental and macro seats, and whatever the firm
then does is the firm's result, not the scanner's. So nothing in the village
could answer the plain question about any scanner: *if you had simply done what
it said, would you have made money?* Its calls were heard and never tested. The
names outside the village's universe — the fleet's Form 4 small caps, its
altcoins — were not even heard; they were dropped with a line in the log.

The lab tests every call on its own, in the sandbox, on paper:

    1. **A call is a direction.** A reading scored above zero is a buy, below
       zero a sell. A score of zero, or a confidence of zero, is silence.
    2. **It is entered where a firm could have entered it.** The price on the
       bar the call was made, in a market that was open, from a bar no more
       than an hour old. A stock called at 2am waits for the scanner to call it
       again once the market opens: buying at last night's close is a trade
       nobody could have made, and the overnight gap it would credit to the
       call is exactly the flattering kind of mistake.
    3. **It pays what crossing really costs.** The session's measured half
       spread plus the fee, on each side. On crypto that is Alpaca's fee and
       the widest spread measured by any desk that trades the coin, or the
       thinnest book's for a coin no desk trades. A call that was right by less
       than its costs lost money.
    4. **It is held for an hour, a day and a week.** Three horizons, each its
       own idea, because a call can be right about the afternoon and wrong
       about the week. A scanner repeating a call every bar is making one call
       per horizon until that idea closes, not ninety-six a day. A scanner that
       turns against its own call closes those ideas at once and opens the
       other side.
    5. **SPY is the yardstick.** Each idea records what holding SPY made over
       the same window, with no costs. That is the stricter comparison, since
       somebody simply holding SPY pays nothing per window, and it is the
       village's own goal: beat SPY net of costs.

**It cannot reach the money.** The lab is built from ``sandbox_handles``: a
writer that refuses every table but the sandbox's own, and whose reads are
checked to be reads. Its book is ``sandbox_ideas``. No row there is a fill, no
firm reads it, and nothing here can place an order.

**It does not flatter a short record.** No verdict before twenty closed ideas,
and the verdict is a t-test on the edge over SPY with the bar raised for every
scanner and horizon being looked at at once: thirty looks at a 5% test turn up
one or two "discoveries" by chance, and a scoreboard that ignored that would
crown a new genius every week. Ideas overlap in time and move with the market
together, so even that bar is kinder than it looks. A verdict here means
"worth a closer look", never "proven".
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import NormalDist
from typing import Callable, Iterable, Optional, Sequence

from ...db.connection import to_datetime, to_iso
from ...money import D, ZERO, money
from .. import session as market_session

TABLE = "sandbox_ideas"

#: How long each idea is held, keyed by the name it is stored and shown under.
HORIZONS = (
    ("1h", timedelta(hours=1)),
    ("1d", timedelta(days=1)),
    ("1w", timedelta(days=7)),
)
HELD_FOR = {"1h": "an hour", "1d": "a day", "1w": "a week"}

#: Paper money behind each idea. Only a unit for reading the results in
#: dollars: every idea gets the same, so no call counts for more than another.
NOTIONAL = D("1000")

#: What every idea is compared with.
BENCHMARK = "SPY"

#: How old a price may be and still be traded on. A thin ETF can go most of an
#: hour without a print in a normal session; anything older is a quote nobody
#: is standing behind, and entering on it would test the call against a price
#: that had already moved.
FRESH = timedelta(hours=1)

#: How long past due an idea may wait for a price before it is written off.
#: Long enough for a holiday weekend, short enough that a delisted small cap
#: does not sit open for ever.
VOID_AFTER = timedelta(days=4)

#: Closed ideas a scanner needs, per horizon, before it gets any verdict.
MIN_CLOSED = 20

#: The test's size across the whole scoreboard, before the Bonferroni split.
ALPHA = 0.05

#: Alpaca's crypto fee, per side, as `config/firm_config.yaml` charges it.
CRYPTO_FEE_BPS = D("25")
#: The widest crypto half-spread measured in this village (DOGE and WIF,
#: 2026-09-11). Used for any coin no desk trades: an altcoin nobody has
#: measured is priced like the thinnest book anybody has.
THIN_CRYPTO_SPREAD_BPS = D("19.7")

NOTE_LIMIT = 200
BPS = D("10000")
FOUR = D("0.0001")

FALSE = ("0", "false", "no", "off")


def on_by_default() -> bool:
    """Whether the lab runs when nobody has flipped its switch.

    On unless ``TRADE_IDEA_LAB`` says otherwise: it makes no request of its own
    for the village's symbols and cannot reach the ledger, so there is nothing
    to opt in to. The wall switch in Mission Control overrides this either way.
    """
    return os.environ.get("TRADE_IDEA_LAB", "on").strip().lower() not in FALSE


def outside_on() -> bool:
    """Whether calls outside the village's universe are priced and tested.

    These are the one part of the lab that asks the internet for anything: a
    batched request to Alpaca's latest-bar endpoints, at most one per asset
    class per bar. ``TRADE_IDEA_LAB_OUTSIDE=off`` stops it.
    """
    return os.environ.get("TRADE_IDEA_LAB_OUTSIDE", "on").strip().lower() not in FALSE


# =========================================================================
# what is being tested
# =========================================================================
@dataclass(frozen=True)
class Call:
    """One scanner saying one thing about one symbol on one bar."""

    publisher: str
    symbol: str
    score: Decimal
    confidence: Decimal
    note: str = ""
    #: A symbol the village's feed does not carry: priced from Alpaca's latest
    #: bars instead, and scored apart from the rest.
    outside: bool = False

    @property
    def side(self) -> str:
        return "buy" if self.score > 0 else "sell"


def calls_from(publisher: str, readings: Iterable, outside: bool = False) -> list:
    """Readings as calls, dropping the ones that are silence."""
    out = []
    for reading in readings:
        try:
            score, confidence = D(reading.score), D(reading.confidence)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if score == 0 or confidence <= 0 or not reading.symbol:
            continue
        out.append(Call(str(publisher), str(reading.symbol).upper(), score, confidence,
                        str(reading.note or "")[:NOTE_LIMIT], outside))
    return out


# =========================================================================
# what crossing costs
# =========================================================================
class CostBook:
    """What crossing once costs, in basis points, for any symbol at any moment.

    Built from the firm specs because costs are a fact about the venue, and the
    desks have measured theirs. A symbol several desks trade is charged the
    widest of their numbers, the same rule `firm_c_crypto` applied to its own
    three coins: a trade should not be told the cheapest book is typical.
    """

    def __init__(self, specs: Iterable = (), fee_bps=D("2"), session_aware: bool = True):
        self.fee_bps = D(fee_bps)
        #: False on daily bars, where a timestamp names a session rather than
        #: a moment and every trade is taken at the close, in regular hours —
        #: the same rule the paper venue applies.
        self.session_aware = session_aware
        self.declared: dict = {}
        for spec in specs:
            if not getattr(spec, "costs_overridden", False):
                continue
            fee = D(spec.fee_bps) if spec.fee_bps is not None else None
            spread = D(spec.slippage_bps) if spec.slippage_bps is not None else None
            for symbol in getattr(spec, "universe", ()) or ():
                old_fee, old_spread = self.declared.get(str(symbol).upper(), (None, None))
                self.declared[str(symbol).upper()] = (_wider(old_fee, fee),
                                                      _wider(old_spread, spread))

    def side_bps(self, symbol: str, when: datetime) -> Decimal:
        symbol = str(symbol).upper()
        fee, spread = self.declared.get(symbol, (None, None))
        if market_session.is_crypto(symbol):
            return ((fee if fee is not None else CRYPTO_FEE_BPS)
                    + (spread if spread is not None else THIN_CRYPTO_SPREAD_BPS))
        # An equity costs what its session costs: 0.82 bps in regular hours,
        # six times that after them. A desk that declared worse is believed.
        which = (market_session.session_of(when, symbol) if self.session_aware
                 else market_session.REGULAR)
        session = market_session.HALF_SPREAD_BPS[which]
        return ((fee if fee is not None else self.fee_bps)
                + (spread if spread is not None and spread > session else session))


def _wider(a: Optional[Decimal], b: Optional[Decimal]) -> Optional[Decimal]:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


# =========================================================================
# what it trades at
# =========================================================================
class BarPrices:
    """The price the lab may trade a symbol at on this bar, or why not.

    One rule for entries and exits alike, so an idea is never entered on a
    price it could not have been exited on or the other way round: the market
    has to be open, and the price has to be fresh.
    """

    def __init__(self, market, now: datetime, outside: Optional[dict] = None,
                 session_aware: bool = True, fresh: timedelta = FRESH):
        self.market = market
        self.now = to_datetime(now)
        self.known = {str(s).upper() for s in getattr(market, "symbols", ())}
        #: symbol -> (price, stamp), for symbols the feed does not carry.
        self.outside = outside or {}
        #: False on daily bars: a daily bar is stamped midnight UTC, which read
        #: as a moment is 8pm in New York with every exchange shut. See
        #: `PaperVenue.session_aware`, which learned this first.
        self.session_aware = session_aware
        self.fresh = fresh

    def __call__(self, symbol: str) -> tuple:
        symbol = str(symbol).upper()
        if self.session_aware and not market_session.is_open(self.now, symbol):
            return None, "its market is shut"
        if symbol in self.known:
            if symbol in (getattr(self.market, "unpriceable", None) or {}):
                return None, "the village's feed cannot price it"
            bar = self.market.bar(symbol)
            if bar is None or D(bar.close) <= 0:
                return None, "no bar yet"
            price, stamp = D(bar.close), bar.as_of
        else:
            got = self.outside.get(symbol)
            if not got:
                return None, "no price for it outside the village's feed"
            price, stamp = got
        stamp = to_datetime(stamp)
        if stamp is not None and self.now - stamp > self.fresh:
            hours = (self.now - stamp).total_seconds() / 3600
            return None, f"its newest price is {hours:.1f}h old"
        return D(price), ""

    def benchmark(self) -> Optional[Decimal]:
        """SPY's mark, fresh or not: a yardstick that did not trade did not move."""
        if BENCHMARK not in self.known:
            return None
        mark = D(self.market.mark(BENCHMARK))
        return mark if mark > 0 else None


class LatestPrices:
    """The newest price of symbols the village's feed does not carry.

    **Batched, once per bar.** Alpaca's latest-bar endpoints take a list, so
    thirty altcoins cost one request rather than thirty, and a symbol asked
    about on a bar is not asked again until the bar turns — a miss is
    remembered as firmly as a hit. The rate limit has been the proximate cause
    of more than one outage in this village; this adds at most one stock and
    one crypto request every fifteen minutes, and only on bars where an outside
    call was made or an outside idea fell due.

    Uses the same credentials as the feed and never logs them.
    """

    STOCKS = "https://data.alpaca.markets/v2/stocks/bars/latest"
    CRYPTO = "https://data.alpaca.markets/v1beta3/crypto/us/latest/bars"
    #: More than this in one request is a runaway list, not a screen.
    MAX_SYMBOLS = 100
    #: What a ticker can look like and still be sent. A scanner is a file
    #: anybody can drop in, and one malformed name fails the whole batch.
    STOCK_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
    CRYPTO_SYMBOL = re.compile(r"^[A-Z0-9]{1,15}-USD$")

    def __init__(self, timeout_s: int = 10, stock_feed: str = "",
                 getter: Optional[Callable] = None):
        self.timeout_s = timeout_s
        self.stock_feed = stock_feed or os.environ.get("TRADE_ALPACA_FEED", "iex")
        self._get = getter or _alpaca_get
        self._bar = ""
        self._cache: dict = {}
        self.last_error = ""

    def fetch(self, symbols: Iterable[str], bar: str) -> dict:
        """symbol -> (price, stamp) for whichever of `symbols` Alpaca priced."""
        if bar != self._bar:
            self._bar, self._cache, self.last_error = bar, {}, ""
        wanted = sorted({str(s).upper() for s in symbols} - set(self._cache))
        crypto = [s for s in wanted
                  if market_session.is_crypto(s) and self.CRYPTO_SYMBOL.match(s)]
        stocks = [s for s in wanted
                  if not market_session.is_crypto(s) and self.STOCK_SYMBOL.match(s)]
        crypto, stocks = crypto[: self.MAX_SYMBOLS], stocks[: self.MAX_SYMBOLS]
        # The rest is a miss for the bar, like a name Alpaca had no price for:
        # sent, a malformed one would sink the batch it rode in, and a list
        # past the cap would be asked for again on every tick of the bar.
        for symbol in set(wanted) - set(crypto) - set(stocks):
            self._cache[symbol] = None
        if crypto:
            self._ask(self.CRYPTO, {"symbols": ",".join(s.replace("-", "/") for s in crypto)},
                      crypto, pairs=True)
        if stocks:
            self._ask(self.STOCKS, {"symbols": ",".join(stocks), "feed": self.stock_feed},
                      stocks, pairs=False)
        return {s: v for s, v in self._cache.items() if v}

    def _ask(self, url: str, params: dict, symbols: list, pairs: bool) -> None:
        for symbol in symbols:
            self._cache[symbol] = None
        try:
            payload = self._get(url, params, self.timeout_s) or {}
        except Exception as exc:  # noqa: BLE001 - a price source, never a precondition
            self.last_error = str(exc)[:160]
            return
        bars = payload.get("bars") or {}
        if not isinstance(bars, dict):
            return
        from ..data.feeds import _parse_stamp

        for symbol in symbols:
            row = bars.get(symbol.replace("-", "/") if pairs else symbol)
            if not isinstance(row, dict) or row.get("c") is None:
                continue
            try:
                price = D(row["c"])
            except (InvalidOperation, TypeError, ValueError):
                continue
            if price > 0:
                self._cache[symbol] = (price, _parse_stamp(row.get("t")))


def _alpaca_get(url: str, params: dict, timeout_s: int):
    from ..data.feeds import AlpacaFeed, FeedNotConfigured, _get_json

    key, secret = AlpacaFeed.credentials()
    if not key or not secret:
        raise FeedNotConfigured("no Alpaca credentials, so nothing outside the "
                                "village's feed can be priced")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
               "User-Agent": "ai-village-trading/1.0"}
    return _get_json(url, params, headers, timeout_s)


# =========================================================================
# the lab
# =========================================================================
@dataclass
class LabReport:
    opened: int = 0
    closed: int = 0
    flipped: int = 0
    voided: int = 0
    pnl: Decimal = ZERO
    #: symbol -> why it could not be traded this bar.
    waiting: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def lines(self) -> list:
        out = list(self.notes)
        if self.opened or self.closed or self.voided:
            parts = [f"tested {self.opened} new idea(s)"] if self.opened else []
            if self.closed:
                flipped = f", {self.flipped} because the scanner flipped" if self.flipped else ""
                parts.append(f"closed {self.closed}{flipped} for {_signed_money(self.pnl)} "
                             "on paper")
            if self.voided:
                parts.append(f"wrote off {self.voided} that never got a price")
            out.append("idea lab: " + "; ".join(parts))
        return out


class IdeaLab:
    """Opens an idea for every call, closes it on time, and keeps the score.

    Built on the sandbox writer and nothing else, so everything it records goes
    to `sandbox_ideas` and every read it makes is checked to be a read.
    """

    def __init__(self, writer, costs: Optional[CostBook] = None,
                 outside: Optional[LatestPrices] = None,
                 horizons: Sequence = HORIZONS, notional=NOTIONAL,
                 session_aware: bool = True, fresh: timedelta = FRESH):
        self.writer = writer
        self.costs = costs or CostBook(session_aware=session_aware)
        self.outside = outside
        self.horizons = tuple(horizons)
        self.notional = D(notional)
        self.session_aware = session_aware
        self.fresh = fresh
        #: The bar the outside-price failure was last reported on. The same
        #: calls are re-read every tick of a bar, and saying so once a minute
        #: is the log spam the scanners were just cured of.
        self._said_bar = ""

    # -- reading ------------------------------------------------------------
    def calls_on(self, bar: str) -> list:
        """Every call on the board for this bar, the newest per scanner and symbol."""
        if not bar:
            return []
        try:
            rows = self.writer.query(
                "SELECT publisher, symbol, score, confidence, note FROM signals "
                "WHERE as_of = ? AND symbol <> '' ORDER BY id", (bar,)) or []
        except Exception:  # noqa: BLE001 - an un-migrated database has no board
            return []
        newest: dict = {}
        for row in rows:                       # oldest first: the last one wins
            newest[(str(row["publisher"]), str(row["symbol"]).upper())] = row
        calls = []
        for (publisher, symbol), row in newest.items():
            try:
                score, confidence = D(row["score"]), D(row["confidence"])
            except (InvalidOperation, TypeError, ValueError):
                continue
            if score == 0 or confidence <= 0:
                continue
            calls.append(Call(publisher, symbol, score, confidence,
                              str(row.get("note") or "")[:NOTE_LIMIT]))
        return calls

    def open_ideas(self) -> list:
        try:
            return self.writer.query(
                f"SELECT * FROM {TABLE} WHERE closed_bar IS NULL ORDER BY id") or []
        except Exception:  # noqa: BLE001 - no table yet is not an error
            return []

    # -- the bar ------------------------------------------------------------
    def run(self, market, outside_calls: Sequence = ()) -> LabReport:
        """Test this bar's calls: the board's, plus any the scanners named outside
        the village's universe (`Scanners.take_outside`)."""
        now = market.as_of()
        if now is None:
            return LabReport()
        from ..signals import stamp

        bar = stamp(now)
        known = {str(s).upper() for s in getattr(market, "symbols", ())}
        # A board symbol the feed does not carry is priced like an outside one,
        # never through the feed: asking the feed about a stranger fetches its
        # whole history and reports it unpriceable to every firm in the tick.
        calls = [c if c.symbol in known else replace(c, outside=True)
                 for c in self.calls_on(bar)]
        # Outside *a scanner's* universe is not always outside the village's:
        # a scanner limited to three ETFs can still name a stock a desk trades.
        calls += [replace(c, outside=c.symbol not in known) for c in outside_calls]
        rows = self.open_ideas()

        report = LabReport()
        priced: dict = {}
        if self.outside is not None:
            due = {str(r["symbol"]).upper() for r in rows
                   if int(r.get("outside") or 0) and self._due(r, now)}
            need = ({c.symbol for c in calls if c.outside} | due) - known
            need = {s for s in need
                    if not self.session_aware or market_session.is_open(now, s)}
            if need:
                priced = self.outside.fetch(need, bar)
                if self.outside.last_error and bar != self._said_bar:
                    report.notes.append(
                        f"idea lab: calls outside the village not priced this bar — "
                        f"{self.outside.last_error}")
        prices = BarPrices(market, now, priced, self.session_aware, self.fresh)
        report = self.step(now, calls, prices, rows, report)
        # Calls waiting on a shut market or a stale price are not reported: on
        # any night that is every stock call, every bar, and it is the lab
        # working as intended. `report.waiting` still says which and why.
        if report.notes:
            self._said_bar = bar
        return report

    def step(self, now: datetime, calls: Sequence, prices, rows: Optional[list] = None,
             report: Optional[LabReport] = None) -> LabReport:
        """One bar: close what is due or contradicted, then open what is new.

        `prices(symbol)` returns (price, why not); `prices.benchmark()` is SPY.
        Split from `run` so a test, or a replay, can hand it any clock and any
        prices without a market or a feed behind them.
        """
        from ..signals import stamp

        report = report or LabReport()
        bar = stamp(now)
        now = to_datetime(now)
        spy = prices.benchmark()
        rows = self.open_ideas() if rows is None else rows
        calling: dict = {}
        for call in calls:
            calling[(call.publisher, call.symbol)] = call     # a repeat: last wins

        # 1. Close what is due, or what its own scanner now calls the other way.
        holding: set = set()
        for row in rows:
            key = (str(row["publisher"]), str(row["symbol"]).upper())
            call = calling.get(key)
            flipped = call is not None and call.side != str(row["side"])
            due = self._due(row, now)
            opened = to_datetime(row.get("opened_bar"))
            # The village's clock can step back a bar (see `Ecosystem._high_bar`);
            # an idea is never settled on a bar before the one it was opened on.
            if not (due or flipped) or (opened is not None and now < opened):
                holding.add((key, str(row["horizon"])))
                continue
            if D(row.get("entry_price") or 0) <= 0:
                self.writer.update(TABLE, row["id"], {
                    "closed_bar": bar, "closed_why": "written off: no entry price"})
                report.voided += 1
                continue
            price, why = prices(key[1])
            if price is None:
                due_at = to_datetime(row["due_at"])
                if due and due_at is not None and now - due_at > VOID_AFTER:
                    self.writer.update(TABLE, row["id"], {
                        "closed_bar": bar, "closed_why": f"written off: {why}"[:200]})
                    report.voided += 1
                    continue
                holding.add((key, str(row["horizon"])))
                if due:
                    report.waiting[key[1]] = why
                continue
            why_closed = "held to horizon" if due else "the scanner flipped"
            report.pnl += self._close(row, bar, now, price, spy, why_closed)
            report.closed += 1
            if not due:
                report.flipped += 1

        # 2. Open an idea per horizon for every call that is not already one.
        for key, call in calling.items():
            wanted = [(name, span) for name, span in self.horizons
                      if (key, name) not in holding]
            if not wanted:
                continue
            price, why = prices(call.symbol)
            if price is None:
                report.waiting[call.symbol] = why
                continue
            for name, span in wanted:
                self._open(call, name, span, now, bar, price, spy)
                report.opened += 1
        return report

    @staticmethod
    def _due(row: dict, now: datetime) -> bool:
        due_at = to_datetime(row.get("due_at"))
        return due_at is not None and to_datetime(now) >= due_at

    def _open(self, call: Call, horizon: str, span: timedelta, now: datetime,
              bar: str, price: Decimal, spy: Optional[Decimal]) -> int:
        return self.writer.insert(TABLE, {
            "publisher": call.publisher[:60],
            "symbol": call.symbol,
            "side": call.side,
            "horizon": horizon,
            "score": call.score,
            "confidence": call.confidence,
            "note": call.note[:NOTE_LIMIT],
            "outside": 1 if call.outside else 0,
            "opened_bar": bar,
            "due_at": to_iso(now + span),
            "entry_price": D(price),
            "entry_cost_bps": self.costs.side_bps(call.symbol, now),
            "spy_entry": spy,
        })

    def _close(self, row: dict, bar: str, now: datetime, price: Decimal,
               spy: Optional[Decimal], why: str) -> Decimal:
        """Settle one idea. Returns what it made on its notional."""
        entry, exit_ = D(row["entry_price"]), D(price)
        move = (exit_ - entry) / entry
        if str(row["side"]) == "sell":
            move = -move                   # a sell call is right when it falls
        exit_cost = self.costs.side_bps(str(row["symbol"]), now)
        costs = (D(row.get("entry_cost_bps") or 0) + exit_cost) / BPS
        net_pct = ((move - costs) * 100).quantize(FOUR)
        spy_pct = None
        spy_entry = D(row.get("spy_entry") or 0)
        if spy is not None and spy_entry > 0:
            spy_pct = ((D(spy) / spy_entry - 1) * 100).quantize(FOUR)
        pnl = money(self.notional * net_pct / 100)
        self.writer.update(TABLE, row["id"], {
            "closed_bar": bar,
            "exit_price": exit_,
            "exit_cost_bps": exit_cost,
            "spy_exit": spy,
            "return_pct": net_pct,
            "spy_pct": spy_pct,
            "pnl": pnl,
            "closed_why": why,
        })
        return pnl


# =========================================================================
# the scoreboard
# =========================================================================
@dataclass
class Record:
    """How one scanner's calls did at one horizon."""

    publisher: str
    horizon: str
    outside: bool
    closed: int
    won: int
    pnl: Decimal
    mean_pct: Decimal
    spy_pct: Decimal
    edge_pct: Decimal
    t: Optional[float]
    verdict: str = ""
    #: What holding SPY for the same windows made on the same paper.
    spy_pnl: Decimal = ZERO

    @property
    def won_pct(self) -> float:
        return round(100.0 * self.won / self.closed, 1) if self.closed else 0.0

    @property
    def label(self) -> str:
        return self.publisher + (" (outside the village)" if self.outside else "")


def scoreboard(db) -> list:
    """Every scanner's record at every horizon, graded. Newest data, no cache.

    Aggregated in SQL rather than read row by row: the hour horizon alone adds
    a row per scanner per symbol per hour, and a page that summed them in
    Python would get slower every day it was left running.
    """
    try:
        rows = db.query(
            "SELECT publisher, horizon, outside, COUNT(*) AS n, "
            "SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END) AS won, "
            "SUM(pnl) AS pnl, SUM(return_pct) AS sum_ret, "
            "SUM(COALESCE(spy_pct, 0)) AS sum_spy, "
            "SUM((return_pct - COALESCE(spy_pct, 0)) * "
            "(return_pct - COALESCE(spy_pct, 0))) AS sum_sq "
            f"FROM {TABLE} WHERE closed_bar IS NOT NULL AND return_pct IS NOT NULL "
            "GROUP BY publisher, horizon, outside") or []
    except Exception:  # noqa: BLE001 - no table yet is not an error
        return []
    records = []
    for row in rows:
        n = int(row["n"] or 0)
        if n <= 0:
            continue
        sum_ret, sum_spy = _dec(row["sum_ret"]), _dec(row["sum_spy"])
        edge = (sum_ret - sum_spy) / D(n)
        records.append(Record(
            publisher=str(row["publisher"]),
            horizon=str(row["horizon"]),
            outside=bool(int(row["outside"] or 0)),
            closed=n,
            won=int(row["won"] or 0),
            pnl=money(_dec(row["pnl"])),
            mean_pct=(sum_ret / D(n)).quantize(FOUR),
            spy_pct=(sum_spy / D(n)).quantize(FOUR),
            edge_pct=edge.quantize(FOUR),
            t=_t_stat(n, float(edge), float(_dec(row["sum_sq"]))),
            spy_pnl=money(NOTIONAL * sum_spy / 100),
        ))
    return grade(records)


def grade(records: list, min_closed: int = MIN_CLOSED, alpha: float = ALPHA) -> list:
    """Give each record its verdict, allowing for how many are being looked at.

    The critical value is Bonferroni over every record with enough ideas to be
    judged: with one scanner and one horizon it is the familiar 1.96, with
    thirty it is 3.19. Blunt, and deliberately so.
    """
    judged = [r for r in records if r.closed >= min_closed]
    z = NormalDist().inv_cdf(1 - alpha / (2 * max(1, len(judged))))
    for r in records:
        if r.closed < min_closed:
            r.verdict = "too few to tell"
        elif r.t is None:
            # Every idea beat or trailed SPY by the same amount: a scanner
            # calling SPY itself, whose edge is exactly its own costs. There is
            # no luck in that to test for, only a sign.
            r.verdict = ("beats SPY" if r.edge_pct > 0 else
                         "trails SPY" if r.edge_pct < 0 else "no edge yet")
        elif r.t >= z:
            r.verdict = "beats SPY"
        elif r.t <= -z:
            r.verdict = "trails SPY"
        else:
            r.verdict = "no edge yet"
    order = {name: i for i, (name, _) in enumerate(HORIZONS)}
    return sorted(records, key=lambda r: (order.get(r.horizon, 99), -r.pnl, r.publisher))


def _t_stat(n: int, mean: float, sum_sq: float) -> Optional[float]:
    """t of the mean edge, from n, the mean and the sum of squared edges."""
    if n < 2:
        return None
    var = (sum_sq - n * mean * mean) / (n - 1)
    if var <= 1e-12:
        return None
    return mean / math.sqrt(var / n)


def open_count(db) -> int:
    try:
        row = db.query_one(f"SELECT COUNT(*) AS n FROM {TABLE} WHERE closed_bar IS NULL")
    except Exception:  # noqa: BLE001
        return 0
    return int((row or {}).get("n") or 0)


def recent(db, limit: int = 12) -> list:
    """The latest ideas to close with a result, newest first."""
    try:
        return db.query(
            f"SELECT * FROM {TABLE} WHERE closed_bar IS NOT NULL AND return_pct IS NOT NULL "
            "ORDER BY id DESC LIMIT ?", (limit,)) or []
    except Exception:  # noqa: BLE001
        return []


def render(db) -> str:
    """The scoreboard as text, for the CLI and the tick log."""
    records = scoreboard(db)
    lines = [f"idea lab: {open_count(db)} idea(s) open; ${NOTIONAL:,.0f} of paper each"]
    if not records:
        lines.append("(no idea has closed yet: the first hour-long ones close an hour "
                     "after the lab starts)")
        return "\n".join(lines)
    for name, _ in HORIZONS:
        mine = [r for r in records if r.horizon == name]
        if not mine:
            continue
        lines.append(f"\nheld for {HELD_FOR.get(name, name)}:")
        width = max(len(r.label) for r in mine)
        for r in mine:
            lines.append(
                f"  {r.label.ljust(width)}  {r.closed:>5} closed  {r.won_pct:>5.1f}% won  "
                f"{_signed_money(r.pnl):>10}  avg {r.mean_pct:+.3f}% vs SPY "
                f"{r.spy_pct:+.3f}%  {r.verdict}")
    return "\n".join(lines)


def _dec(value) -> Decimal:
    try:
        return D(str(value)) if value is not None else ZERO
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def _signed_money(value) -> str:
    v = money(value)
    return f"-${abs(v):,.2f}" if v < 0 else f"+${v:,.2f}"


__all__ = [
    "BENCHMARK", "BarPrices", "Call", "CostBook", "FRESH", "HELD_FOR", "HORIZONS",
    "IdeaLab", "LabReport", "LatestPrices", "MIN_CLOSED", "NOTIONAL", "Record",
    "TABLE", "calls_from", "grade", "on_by_default", "open_count", "outside_on",
    "recent", "render", "scoreboard",
]
