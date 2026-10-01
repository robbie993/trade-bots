"""The Pump.fun lab — what buying every trending meme launch would have done.

Robbie wants a Pump.fun bot. Before anybody writes one, the village can answer
the question such a bot lives or dies by, from data it already collects: *if
you had bought every one of these tokens the moment it showed up, what would
you have now?*

The meme radar (`meme_radar.py`) logs three lists to `intel` on every bar:
Pump.fun's twenty biggest coins (`pumpfun_top`), the twenty streaming live
(`pumpfun_live`), and DexScreener's thirty most-boosted tokens
(`dexscreener_boosts`, which is paid promotion). Alpaca lists none of them, so
they were kept and never asked anything. The lab asks them that one question:

    1. **A token is bought the first time it shows up.** On the bar the radar
       first logs it, at DexScreener's price for its deepest pool. Each list
       is scored on its own, because "buy what just went live" and "buy what
       just paid for a boost" are different bets.
    2. **Only where somebody could have got in and out.** A pool holding less
       than $5,000 is not bought: its price is not one anybody could trade a
       meaningful amount at. Neither is a token DexScreener has no pool for.
    3. **One look per token.** A token skipped at first sight is skipped for
       good, and the skip is recorded. Buying it later, once it had grown a
       deep pool, would credit "buy every launch" with the launches that had
       already proven themselves, which is exactly the flattering mistake.
       Only a request that failed is retried, and only for two hours after
       the token first showed up, so a restart or a first deploy never buys a
       token hours late at today's price.
    4. **Held an hour, a day and a week.** Three ideas per token, each closed
       at DexScreener's price when it falls due.
    5. **A pool that disappears is a total loss.** A due token with no pool is
       retried for a day, then marked vanished at -100%: a holder of a rugged
       token has nothing left to sell. A token the lab could not even ask
       about for a day, because its own requests failed, is void instead and
       left out: an outage says nothing about the token.

**The costs are an assumption, not a measurement.** Pump.fun's bonding curve
charges about 1% a side and the DEX pools a token graduates to about 0.25%,
before slippage, which on pools this thin is often the larger cost. Every
return is kept gross, and the scoreboard shows the mean once more after an
assumed 2% round trip (`ASSUMED_ROUND_TRIP_PCT`). Nobody has measured what a
fill on these pools really costs; a real bot would have to, before trusting
any of this.

**No verdict, on purpose.** This is a reality check for one idea, not a test
of a scanner. The mean of a meme coin basket is one hundredfold token away
from looking brilliant, so the scoreboard leads with the median, the share
that went up and the share that lost more than half.

**It cannot reach the money.** Built from the sandbox's handles like the idea
lab: it reads `intel` through a writer whose reads are checked to be reads,
and writes only `meme_lab`. No firm can buy any of these tokens, and nothing
here can place an order.

Off unless `TRADE_MEME_RADAR_ENABLED` is set, because it reaches the open
internet and has nothing to read without the radar. `TRADE_MEME_LAB=off`
stops it on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Optional
from urllib.parse import quote

from ...db.connection import to_datetime, to_iso, utcnow
from ...money import D, ZERO

TABLE = "meme_lab"

#: The radar's lists, as `MemeRadar._log_launches` names them, and how the
#: page names them.
SOURCES = ("pumpfun_top", "pumpfun_live", "dexscreener_boosts")
LISTED_AS = {
    "pumpfun_top": "Pump.fun top 20",
    "pumpfun_live": "Pump.fun live now",
    "dexscreener_boosts": "DexScreener boosted",
}

#: How long each idea is held, keyed by the name it is stored and shown under.
HORIZONS = (
    ("1h", timedelta(hours=1)),
    ("1d", timedelta(days=1)),
    ("1w", timedelta(days=7)),
)
HELD_FOR = {"1h": "an hour", "1d": "a day", "1w": "a week"}

#: The thinnest pool a token is bought in, in dollars of liquidity.
MIN_LIQUIDITY_USD = D("5000")

#: How long after it first showed up a token may still be looked at, when the
#: request that should have priced it failed.
WINDOW = timedelta(hours=2)

#: How long past due an idea waits for a price before it is settled without one.
GRACE = timedelta(days=1)

#: The longest the lab goes without running, on a village whose bars are
#: longer than this: an hour-long idea on a daily village is still closed
#: within the hour. On fifteen-minute bars the bar always moves first.
IDLE = timedelta(minutes=20)

#: The round trip the "after costs" figure assumes, in percent: about 1% a
#: side on Pump.fun's curve. An assumption, not a measurement (see above).
ASSUMED_ROUND_TRIP_PCT = D("2")

#: What a holder of a token whose pool is gone has left.
VANISHED_PCT = D("-100")
#: "Lost more than half": below this.
HALVED_PCT = -50

DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/{chain}/{addresses}"
#: Addresses per request. DexScreener takes thirty, but it answers with a list
#: of pools, and a list cut short at its own limit would make a token with a
#: pool look like one without. Ten tokens rarely have thirty pools between them.
PER_CALL = 10
#: Requests per bar at most: 120 tokens, several times what a bar brings.
MAX_CALLS = 12
#: Seconds of asking per bar at most. Each request may take eight, and twelve
#: slow answers inside the tick would hold up every firm for a minute and a
#: half; whatever is left over is asked on the next bar.
BUDGET_S = 20.0
#: A response this long may have been cut short; a token missing from it is
#: then asked again rather than recorded as having no pool.
PAIRS_CAP = 30

FALSE = ("0", "false", "no", "off")
FOUR = D("0.0001")


def enabled() -> bool:
    """On when the meme radar is, unless `TRADE_MEME_LAB` turns it off."""
    if not os.environ.get("TRADE_MEME_RADAR_ENABLED", "").strip():
        return False
    return os.environ.get("TRADE_MEME_LAB", "on").strip().lower() not in FALSE


# =========================================================================
# what is being bought, and at what price
# =========================================================================
@dataclass(frozen=True)
class Token:
    """One token on one of the radar's lists, as `intel` holds it."""

    source: str
    chain: str
    address: str
    name: str = ""
    seen_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.address}"


def token_from(row: dict) -> Optional[Token]:
    """The token an `intel` row is about, or None if it names none.

    Pump.fun's rows are keyed by the Solana mint; DexScreener's by
    `chain:address`, which is split on the first colon only, because a Sui
    token's address has colons of its own.
    """
    source = str(row.get("source") or "")
    item = str(row.get("item_key") or "").strip()
    if source == "dexscreener_boosts":
        chain, _, address = item.partition(":")
    else:
        chain, address = "solana", item
    chain, address = chain.strip().lower(), address.strip()
    if chain in ("", "none") or address in ("", "None"):
        return None
    return Token(source, chain, address, str(row.get("title") or "")[:120],
                 str(row.get("first_seen") or ""))


@dataclass(frozen=True)
class Quote:
    """A token's price in its deepest pool, and how deep that pool is."""

    price: Decimal
    liquidity: Decimal
    mcap: Optional[Decimal] = None


def deepest(pairs: list, address: str) -> Optional[Quote]:
    """The quote from the deepest pool that trades `address` as its base token.

    Only as the base: in a pool where the token is the quote side, `priceUsd`
    is the price of the *other* token. Matched without regard to case, which
    an EVM address needs and a Solana mint never notices.
    """
    want = address.lower()
    best = None
    for pair in pairs or []:
        if not isinstance(pair, dict):
            continue
        base = str((pair.get("baseToken") or {}).get("address") or "").lower()
        if base != want:
            continue
        price = _dec(pair.get("priceUsd"))
        if price is None or price <= 0:
            continue
        depth = _dec((pair.get("liquidity") or {}).get("usd")) or ZERO
        if best is None or depth > best.liquidity:
            best = Quote(price, depth, _dec(pair.get("marketCap")) or _dec(pair.get("fdv")))
    return best


class DexPrices:
    """DexScreener's price for each token's deepest pool, a batch at a time.

    `fetch` returns `{token_key: Quote or None}`. None means DexScreener was
    asked and has no pool for the token. A token missing from the result was
    not answered, because a request failed or the bar's budget ran out, and is
    asked again later. That difference is what lets a vanished pool count as a
    loss while an outage counts as nothing.
    """

    def __init__(self, get_json: Optional[Callable] = None, per_call: int = PER_CALL,
                 max_calls: int = MAX_CALLS, budget_s: float = BUDGET_S):
        if get_json is None:
            from ..data.feeds import _get_json

            # Inside the tick, like the radar: eight seconds a request, so a
            # slow site costs the tick a little rather than a minute.
            def get_json(url, params, headers=None):
                return _get_json(url, params, headers=headers, timeout=8)
        self._get = get_json
        self.per_call = max(1, int(per_call))
        self.max_calls = max(0, int(max_calls))
        self.budget_s = budget_s
        self.last_error = ""

    def fetch(self, tokens: Iterable) -> dict:
        """Price `(chain, address)` pairs, grouped by chain, in as few requests as fit."""
        import time

        from ..meme_radar import HEADERS

        self.last_error = ""
        started = time.monotonic()
        by_chain: dict = {}
        for chain, address in tokens:
            wanted = by_chain.setdefault(chain, [])
            if address not in wanted:
                wanted.append(address)
        out: dict = {}
        calls = 0
        for chain, addresses in by_chain.items():
            for start in range(0, len(addresses), self.per_call):
                if calls >= self.max_calls or time.monotonic() - started > self.budget_s:
                    return out
                batch = addresses[start:start + self.per_call]
                url = DEX_TOKENS.format(chain=quote(chain, safe=""),
                                        addresses=",".join(quote(a, safe="") for a in batch))
                calls += 1
                try:
                    pairs = self._get(url, {}, headers=HEADERS)
                except Exception as exc:  # noqa: BLE001 - a failing site costs one timeout
                    self.last_error = f"{chain}: {str(exc)[:120]}"
                    return out
                if isinstance(pairs, dict):
                    pairs = pairs.get("pairs")
                if not isinstance(pairs, list):
                    # Not an answer about these tokens, so not a reason to
                    # record any of them as having no pool.
                    self.last_error = f"{chain}: answered {type(pairs).__name__}, not a list"
                    return out
                pairs = [p for p in pairs if isinstance(p, dict)]
                cut_short = len(pairs) >= PAIRS_CAP
                for address in batch:
                    found = deepest(pairs, address)
                    if found is not None or not cut_short:
                        out[f"{chain}:{address}"] = found
        return out


# =========================================================================
# the lab
# =========================================================================
@dataclass
class MemeReport:
    bought: int = 0
    thin: int = 0
    unlisted: int = 0
    closed: int = 0
    vanished: int = 0
    void: int = 0
    notes: list = field(default_factory=list)

    def lines(self) -> list:
        out = list(self.notes)
        parts = []
        if self.bought:
            parts.append(f"bought {self.bought} new token(s) on paper")
        skipped = [f"{n} {why}" for n, why in ((self.thin, "too thin to trade"),
                                               (self.unlisted, "with no pool")) if n]
        if skipped:
            parts.append("skipped " + " and ".join(skipped))
        if self.closed or self.vanished:
            gone = f", {self.vanished} of them vanished" if self.vanished else ""
            parts.append(f"closed {self.closed + self.vanished} idea(s){gone}")
        if self.void:
            parts.append(f"voided {self.void} it could not price for a day")
        if parts:
            out.append("Pump.fun lab: " + "; ".join(parts))
        return out


class MemeLab:
    """Buys each new token on the radar's lists on paper, and closes it on time.

    Built on the sandbox writer and nothing else, so everything it records goes
    to `meme_lab` and every read it makes is checked to be a read.
    """

    def __init__(self, writer, prices: Optional[DexPrices] = None, horizons=HORIZONS,
                 min_liquidity=MIN_LIQUIDITY_USD, window: timedelta = WINDOW,
                 grace: timedelta = GRACE):
        self.writer = writer
        self.prices = prices or DexPrices()
        self.horizons = tuple(horizons)
        self.min_liquidity = D(min_liquidity)
        self.window = window
        self.grace = grace
        #: The bar it last ran on, and when. In memory on purpose: a restart
        #: runs it once more, and nothing here is ever bought twice.
        self._bar = ""
        self._ran_at: Optional[datetime] = None

    def tick(self, bar: str, now: Optional[datetime] = None) -> MemeReport:
        """Run when the village's bar moves forward, or after `IDLE` without a run.

        Forward, not merely different: a bar key can step back between ticks
        (see `Ecosystem._bar_now`), and a guard on equality would read every
        flip as a new bar. Run in the same tick as the radar, which logs a new
        bar's tokens just before, so a token is bought within a minute of
        showing up.
        """
        now = to_datetime(now) or utcnow()
        if not bar:
            return MemeReport()
        quiet = self._ran_at is not None and now - self._ran_at < IDLE
        if bar <= self._bar and quiet:
            return MemeReport()
        self._bar, self._ran_at = max(bar, self._bar), now
        return self.run(now)

    def run(self, now: Optional[datetime] = None) -> MemeReport:
        """One bar: price what is due and what is new in one batch, then settle both.

        The wall clock, not the bar: the radar stamps `intel` with the time it
        saw a token, and DexScreener's price is the price now.
        """
        now = to_datetime(now) or utcnow()
        report = MemeReport()
        due = self.due(now)
        new = self.unseen(now)
        wanted = [(str(r["chain"]), str(r["address"])) for r in due]
        wanted += [(t.chain, t.address) for t in new]
        quotes = self.prices.fetch(wanted) if wanted else {}
        if self.prices.last_error:
            report.notes.append("Pump.fun lab: DexScreener not reached this bar "
                                f"({self.prices.last_error}); asking again next bar")
        self._settle(now, due, quotes, report)
        self._look(now, new, quotes, report)
        return report

    # -- reading ------------------------------------------------------------
    def due(self, now: datetime) -> list:
        try:
            return self.writer.query(
                f"SELECT * FROM {TABLE} WHERE status = 'open' AND due_at <= ? "
                "ORDER BY due_at, id", (to_iso(now),)) or []
        except Exception:  # noqa: BLE001 - no table yet is not an error
            return []

    def unseen(self, now: datetime) -> list:
        """Tokens the radar first logged inside the window that the lab has not looked at."""
        try:
            rows = self.writer.query(
                "SELECT source, item_key, title, first_seen FROM intel "
                "WHERE source IN (?, ?, ?) AND first_seen >= ? AND first_seen <= ? "
                "ORDER BY id", (*SOURCES, to_iso(now - self.window), to_iso(now))) or []
            # A token first seen inside the window cannot have been looked at
            # before it was seen, so the last window and a margin is everything.
            looked = {(str(r["source"]), str(r["token_key"])) for r in self.writer.query(
                f"SELECT DISTINCT source, token_key FROM {TABLE} WHERE opened_at >= ?",
                (to_iso(now - self.window - timedelta(hours=1)),)) or []}
        except Exception:  # noqa: BLE001 - an unmigrated ledger has nothing to look at
            return []
        out = []
        for row in rows:
            token = token_from(row)
            if token is not None and (token.source, token.key) not in looked:
                out.append(token)
        return out

    # -- writing ------------------------------------------------------------
    def _settle(self, now: datetime, due: list, quotes: dict, report: MemeReport) -> None:
        for row in due:
            key = str(row["token_key"])
            found = quotes.get(key)
            entry = _dec(row.get("entry_price"))
            if found is not None and entry is not None and entry > 0:
                self.writer.update(TABLE, row["id"], {
                    "exit_price": found.price,
                    "exit_liquidity": found.liquidity,
                    "return_pct": ((found.price / entry - 1) * 100).quantize(FOUR),
                    "status": "closed",
                    "closed_at": to_iso(now),
                })
                report.closed += 1
                continue
            due_at = to_datetime(row.get("due_at"))
            if due_at is None or now - due_at <= self.grace:
                continue                      # no price yet: ask again next bar
            if key in quotes and entry is not None and entry > 0:
                # Asked, a day late, and DexScreener has no pool: it is gone.
                self.writer.update(TABLE, row["id"], {
                    "return_pct": VANISHED_PCT, "status": "vanished", "closed_at": to_iso(now)})
                report.vanished += 1
            else:
                self.writer.update(TABLE, row["id"], {"status": "void", "closed_at": to_iso(now)})
                report.void += 1

    def _look(self, now: datetime, new: list, quotes: dict, report: MemeReport) -> None:
        for token in new:
            if token.key not in quotes:
                continue                      # not answered: asked again inside the window
            found = quotes[token.key]
            base = {"source": token.source, "token_key": token.key, "chain": token.chain,
                    "address": token.address, "name": token.name,
                    "seen_at": token.seen_at or None, "opened_at": to_iso(now)}
            if found is None:
                self.writer.insert(TABLE, {**base, "horizon": "", "status": "unlisted"})
                report.unlisted += 1
                continue
            priced = {"entry_price": found.price, "entry_liquidity": found.liquidity,
                      "entry_mcap": found.mcap}
            if found.liquidity < self.min_liquidity:
                self.writer.insert(TABLE, {**base, **priced, "horizon": "", "status": "thin"})
                report.thin += 1
                continue
            for name, span in self.horizons:
                self.writer.insert(TABLE, {**base, **priced, "horizon": name,
                                           "due_at": to_iso(now + span), "status": "open"})
            report.bought += 1


# =========================================================================
# the scoreboard
# =========================================================================
def after_costs(gross_pct, round_trip_pct=ASSUMED_ROUND_TRIP_PCT) -> Decimal:
    """A gross return after paying half the round trip going in and half coming out.

    Linear in the gross return, so the mean after costs is this of the mean,
    and a token that went to nothing is still -100%, not worse.
    """
    side = D(round_trip_pct) / 200
    kept = (1 - side) / (1 + side)
    return ((kept * (1 + D(gross_pct) / 100) - 1) * 100).quantize(FOUR)


@dataclass
class Record:
    """How the tokens from one list did at one horizon."""

    source: str
    horizon: str
    closed: int
    mean_pct: Decimal
    median_pct: Decimal
    up: int
    halved: int
    vanished: int

    @property
    def after_costs_pct(self) -> Decimal:
        return after_costs(self.mean_pct)

    @property
    def label(self) -> str:
        return LISTED_AS.get(self.source, self.source)

    def share(self, n: int) -> float:
        return round(100.0 * n / self.closed, 1) if self.closed else 0.0


_SETTLED = f"FROM {TABLE} WHERE status IN ('closed', 'vanished') AND return_pct IS NOT NULL"


def scoreboard(db) -> list:
    """Every list's record at every horizon, from the ideas that have closed."""
    try:
        rows = db.query(
            "SELECT source, horizon, COUNT(*) AS n, SUM(return_pct) AS total, "
            "SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END) AS up, "
            f"SUM(CASE WHEN return_pct < {HALVED_PCT} THEN 1 ELSE 0 END) AS halved, "
            "SUM(CASE WHEN status = 'vanished' THEN 1 ELSE 0 END) AS gone "
            f"{_SETTLED} GROUP BY source, horizon") or []
    except Exception:  # noqa: BLE001 - no table yet is not an error
        return []
    records = []
    for row in rows:
        n = int(row["n"] or 0)
        if n <= 0:
            continue
        source, horizon = str(row["source"]), str(row["horizon"])
        records.append(Record(
            source=source, horizon=horizon, closed=n,
            mean_pct=((_dec(row["total"]) or ZERO) / n).quantize(FOUR),
            median_pct=_median(db, source, horizon, n),
            up=int(row["up"] or 0), halved=int(row["halved"] or 0),
            vanished=int(row["gone"] or 0)))
    order = {name: i for i, (name, _) in enumerate(HORIZONS)}
    lists = {name: i for i, name in enumerate(SOURCES)}
    return sorted(records, key=lambda r: (order.get(r.horizon, 99), lists.get(r.source, 99)))


def _median(db, source: str, horizon: str, n: int) -> Decimal:
    """The middle return, found by the database rather than by reading every row."""
    def nth(k: int) -> Decimal:
        row = db.query_one(
            f"SELECT return_pct {_SETTLED} AND source = ? AND horizon = ? "
            "ORDER BY return_pct LIMIT 1 OFFSET ?", (source, horizon, k))
        return _dec((row or {}).get("return_pct")) or ZERO

    low, high = nth((n - 1) // 2), nth(n // 2)
    return ((low + high) / 2).quantize(FOUR)


def looks(db) -> dict:
    """Per list: tokens looked at, bought, too thin, and with no pool."""
    try:
        rows = db.query(
            f"SELECT source, status, horizon, COUNT(*) AS n FROM {TABLE} "
            "WHERE horizon = '' OR horizon = ? GROUP BY source, status, horizon",
            (HORIZONS[0][0],)) or []
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for row in rows:
        seen = out.setdefault(str(row["source"]),
                              {"looked": 0, "bought": 0, "thin": 0, "unlisted": 0})
        n = int(row["n"] or 0)
        seen["looked"] += n
        if str(row["horizon"]):
            seen["bought"] += n               # one first-horizon row per token bought
        elif str(row["status"]) in ("thin", "unlisted"):
            seen[str(row["status"])] += n
    lists = {name: i for i, name in enumerate(SOURCES)}
    return dict(sorted(out.items(), key=lambda kv: lists.get(kv[0], 99)))


def open_count(db) -> int:
    try:
        row = db.query_one(f"SELECT COUNT(*) AS n FROM {TABLE} WHERE status = 'open'")
    except Exception:  # noqa: BLE001
        return 0
    return int((row or {}).get("n") or 0)


def render(db) -> str:
    """The scoreboard as text, for the CLI."""
    lines = [f"Pump.fun lab: {open_count(db)} idea(s) open"]
    for source, seen in looks(db).items():
        lines.append(f"  {LISTED_AS.get(source, source)}: looked at {seen['looked']}, "
                     f"bought {seen['bought']}, {seen['thin']} too thin, "
                     f"{seen['unlisted']} with no pool")
    records = scoreboard(db)
    if not records:
        lines.append("(no idea has closed yet: the first hour-long ones close an hour "
                     "after the lab buys them)")
        return "\n".join(lines)
    for name, _ in HORIZONS:
        mine = [r for r in records if r.horizon == name]
        if not mine:
            continue
        lines.append(f"\nheld for {HELD_FOR.get(name, name)}:")
        width = max(len(r.label) for r in mine)
        for r in mine:
            lines.append(
                f"  {r.label.ljust(width)}  {r.closed:>5} closed  median {r.median_pct:+.2f}%  "
                f"mean {r.mean_pct:+.2f}% ({r.after_costs_pct:+.2f}% after costs)  "
                f"{r.share(r.up):.0f}% up  {r.share(r.halved):.0f}% lost half  "
                f"{r.share(r.vanished):.0f}% vanished")
    lines.append(f"\nafter costs assumes a {ASSUMED_ROUND_TRIP_PCT}% round trip; "
                 "that is a guess, not a measurement")
    return "\n".join(lines)


def _dec(value) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


__all__ = [
    "ASSUMED_ROUND_TRIP_PCT", "DexPrices", "GRACE", "HELD_FOR", "HORIZONS", "IDLE", "LISTED_AS",
    "MIN_LIQUIDITY_USD", "MemeLab", "MemeReport", "Quote", "Record", "SOURCES", "TABLE",
    "Token", "WINDOW", "after_costs", "deepest", "enabled", "looks", "open_count", "render",
    "scoreboard", "token_from",
]
