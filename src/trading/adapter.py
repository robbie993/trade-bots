"""Running somebody else's bot as a firm's strategy.

The importer translates a foreign bot into seven genes, which works when the
bot *is* seven genes and fails quietly when it is not. Most real strategies are
not: the logic lives in a function, and no amount of alias-matching on
module-level constants will find it.

So this inverts the relationship. Instead of the village modelling your
strategy, your strategy decides and the village governs it:

    the village gives you   bars, your positions, your cash, the clock
    you give the village    a list of things you would like to do
    the village keeps       the risk manager, the conscience, the atomic
                            ledger, the kill switch, the approval gate, the
                            court, the leaderboard

Everything that makes this a village rather than a script happens *after* your
bot returns. A proposal from a bot goes through exactly the same review as a
proposal from the built-in pod — same risk limits, same conscience, same
position sizing, same audit row. There is no path from here into the store, the
gate or the ledger.

**This executes your file, and that is a real change.** Everywhere else in this
repository a strategy file is parsed with ``ast`` and never run — the court and
the importer both refuse to execute, deliberately, because they read files you
might have been handed. This does not read files you were handed. It runs one
file you named, in one firm you configured, on your own machine. Both of those
are opt-in and neither happens by scanning a directory:

* ``recruit-watch`` and the court still only parse. They gained nothing here.
* A firm runs a bot only when its config says ``bot: <path>``.

**A bad bot must not be able to hurt the village.** One that raises is reported
and skipped. One that hangs is abandoned after a timeout and the tick carries
on without it. One that returns nonsense — an unknown symbol, a negative size,
ten thousand orders — has the nonsense dropped and the rest kept, with a note
saying what was dropped. In every case the firm proposes nothing that tick,
which is the same as a quiet day, and the rest of the village is unaffected.
"""

from __future__ import annotations

import importlib.util
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Sequence

from ..money import ZERO, D, money, percent
from .models import Position, Side, TradeProposal

# A bot gets this long to answer before the tick moves on without it. Generous
# for anything reasonable, short enough that a hung bot is an inconvenience
# rather than a stopped village.
TIMEOUT_SECONDS = 10.0

# More than this from one firm in one tick is a runaway loop, not a strategy.
MAX_PROPOSALS = 50

# The names a bot may define, in the order they are tried.
ENTRY_POINTS = ("propose", "on_bar", "decide", "strategy")

PREFIX = "bot:"


class AdapterError(Exception):
    """The bot could not be loaded. Never raised into a tick."""


def is_bot(strategy: str) -> bool:
    return bool(strategy) and str(strategy).startswith(PREFIX)


def bot_path(strategy: str) -> Path:
    return Path(str(strategy)[len(PREFIX):].strip())


# =========================================================================
# what the bot is allowed to see
# =========================================================================
@dataclass(frozen=True)
class Holding:
    """One position, as a bot sees it: numbers, not a database row."""

    symbol: str
    quantity: Decimal
    average_price: Decimal


@dataclass(frozen=True)
class Context:
    """Everything the village hands a bot, and nothing else.

    Deliberately not the store, the database, the gate or the other firms'
    books. A strategy needs prices and its own position; anything more is a
    route around the parts of this system that make it trustworthy.
    """

    universe: tuple
    cash: Decimal
    equity: Decimal
    as_of: object
    _closes: dict = field(default_factory=dict, repr=False)
    _marks: dict = field(default_factory=dict, repr=False)
    _positions: dict = field(default_factory=dict, repr=False)

    # -- prices -----------------------------------------------------------
    def price(self, symbol: str) -> Optional[Decimal]:
        """The latest mark, or None if the feed has nothing for it."""
        return self._marks.get(str(symbol).upper())

    def closes(self, symbol: str, lookback: int = 0) -> list:
        """Closing prices, oldest first. `lookback` 0 means everything held."""
        series = self._closes.get(str(symbol).upper(), [])
        return list(series[-lookback:] if lookback else series)

    # -- your book --------------------------------------------------------
    def position(self, symbol: str) -> Optional[Holding]:
        return self._positions.get(str(symbol).upper())

    def quantity(self, symbol: str) -> Decimal:
        held = self.position(symbol)
        return held.quantity if held else ZERO

    def entry(self, symbol: str) -> Optional[Decimal]:
        """What this firm paid, on average, for what it is holding.

        The one number a stop needs, and it was missing. Every imported bot
        with a stop or a target had to anchor it to some past close instead —
        a proxy that drifts away from the truth the moment the position is
        added to. The data was always here on the position; it simply was not
        reachable from a strategy.

        None when the firm holds nothing, because there is no entry to speak
        of and zero would read as a 100% loss.
        """
        held = self.position(symbol)
        return held.average_price if held and held.quantity != 0 else None

    def unrealized_pct(self, symbol: str) -> Optional[Decimal]:
        """How far this position is up or down, in percent of what it cost.

        Signed for the direction actually held: a short that has fallen is up.
        None if there is no position or no mark — never zero, which would read
        as "flat" rather than "unknown".
        """
        held = self.position(symbol)
        mark = self.price(symbol)
        if held is None or held.quantity == 0 or mark is None:
            return None
        entry = D(held.average_price)
        if entry <= 0:
            return None
        move = (D(mark) - entry) / entry * D(100)
        return percent(move if held.quantity > 0 else -move)

    @property
    def positions(self) -> list:
        return list(self._positions.values())


def build_context(record, market, positions: Sequence[Position], equity, lookback: int = 250):
    universe = tuple(record.universe or [])
    closes, marks = {}, {}
    for symbol in universe:
        try:
            marks[symbol] = D(market.mark(symbol))
        except Exception:  # noqa: BLE001 - a symbol the feed lacks is not fatal
            marks[symbol] = None
        try:
            closes[symbol] = [D(c) for c in market.closes(symbol, lookback)]
        except Exception:  # noqa: BLE001
            closes[symbol] = []
    held = {
        str(p.symbol).upper(): Holding(
            symbol=str(p.symbol).upper(),
            quantity=D(p.quantity),
            average_price=D(p.avg_price),
        )
        for p in positions
    }
    return Context(
        universe=universe,
        cash=D(record.cash),
        equity=D(equity),
        as_of=market.as_of(),
        _closes=closes,
        _marks=marks,
        _positions=held,
    )


# =========================================================================
# loading
# =========================================================================
def load(path, entry_points: Sequence[str] = ENTRY_POINTS) -> callable:
    """Import a file and find the function that decides. Raises AdapterError.

    ``entry_points`` is a parameter because a scanner is loaded exactly like a
    strategy but answers to different names — see ``src/trading/signals.py``.
    The two lists are kept disjoint on purpose: a file defining ``propose`` is
    telling you it wants to trade, not to publish a score.
    """
    path = Path(str(path))
    if not path.exists():
        raise AdapterError(f"{path} does not exist")
    if path.suffix.lower() != ".py":
        raise AdapterError(f"a bot has to be a .py file, not {path.suffix}")

    spec = importlib.util.spec_from_file_location(f"village_bot_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise AdapterError(f"{path} could not be loaded as a module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:  # noqa: BLE001 - importing runs the file
        raise AdapterError(f"{path.name} failed while loading: {exc}") from exc

    for name in entry_points:
        func = getattr(module, name, None)
        if callable(func):
            return func
    raise AdapterError(
        f"{path.name} defines none of {', '.join(entry_points)} — "
        "the village needs one function it can call"
    )


# =========================================================================
# calling, without letting it take the tick down
# =========================================================================
def call(func, context, timeout: float = TIMEOUT_SECONDS) -> tuple:
    """Run the bot. Returns (result, error). Never raises.

    The bot runs in its own thread so that one which never returns costs a
    tick rather than the village. A timed-out thread is abandoned, not killed —
    Python cannot kill a thread, and pretending otherwise would be worse than
    saying so. It is a daemon thread, so it does not hold up shutdown.
    """
    box: dict = {}

    def run():
        try:
            box["value"] = func(context)
        except BaseException as exc:  # noqa: BLE001 - the bot's problem, not ours
            box["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return None, f"took longer than {timeout:g}s and was left running"
    if "error" in box:
        return None, box["error"]
    return box.get("value"), ""


# =========================================================================
# what came back
# =========================================================================
def _one(raw, record, context, as_of) -> tuple:
    """Turn one returned item into a proposal, or say why it is not usable."""
    if isinstance(raw, dict):
        get = raw.get
    else:
        get = lambda key, default=None: getattr(raw, key, default)  # noqa: E731

    symbol = str(get("symbol") or get("ticker") or "").upper()
    if not symbol:
        return None, "no symbol"
    if symbol not in context.universe:
        return None, f"{symbol} is not in this firm's universe"

    side_raw = str(get("side") or get("action") or "").lower().strip()
    if side_raw in ("buy", "long", "b"):
        side = Side.BUY
    elif side_raw in ("sell", "exit", "close", "s"):
        side = Side.SELL
    else:
        return None, f"{symbol}: side {side_raw!r} is not buy or sell"

    reference = context.price(symbol)
    if reference is None or reference <= 0:
        return None, f"{symbol}: the feed has no price for it"

    quantity = get("quantity", None)
    if quantity is None:
        quantity = get("qty", None)
    if quantity is None:
        notional = get("notional", None) or get("amount", None)
        if notional is None:
            return None, f"{symbol}: no quantity and no notional"
        try:
            quantity = D(notional) / reference
        except (InvalidOperation, ZeroDivisionError, TypeError):
            return None, f"{symbol}: notional {notional!r} is not a number"
    try:
        quantity = D(quantity)
    except (InvalidOperation, TypeError, ValueError):
        return None, f"{symbol}: quantity {quantity!r} is not a number"
    if quantity <= 0:
        return None, f"{symbol}: quantity must be positive, got {quantity}"

    return TradeProposal(
        firm_id=record.id,
        symbol=symbol,
        side=side.value,
        quantity=quantity,
        reference_price=reference,
        notional=money(quantity * reference),
        confidence=D(get("confidence", 0) or 0),
        rationale=str(get("rationale") or get("reason") or "")[:400] or "(the bot gave no reason)",
        debate_winner="bot",
        as_of=as_of,
    ), ""


def to_proposals(result, record, context, as_of) -> tuple:
    """Validate whatever the bot returned. Returns (proposals, complaints)."""
    if result is None:
        return [], []
    if isinstance(result, dict) and ("symbol" in result or "ticker" in result):
        result = [result]
    if not isinstance(result, (list, tuple)):
        return [], [f"expected a list of orders, got {type(result).__name__}"]

    proposals, complaints = [], []
    for raw in list(result)[:MAX_PROPOSALS]:
        proposal, why = _one(raw, record, context, as_of)
        if proposal is None:
            complaints.append(why)
        else:
            proposals.append(proposal)
    if len(result) > MAX_PROPOSALS:
        complaints.append(
            f"returned {len(result)} orders; only the first {MAX_PROPOSALS} were read"
        )
    return proposals, complaints


__all__ = [
    "AdapterError", "Context", "Holding", "PREFIX", "TIMEOUT_SECONDS",
    "MAX_PROPOSALS", "bot_path", "build_context", "call", "is_bot", "load",
    "to_proposals",
]
