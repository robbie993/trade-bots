"""Domain models for the trading ecosystem — one dataclass per table.

Mirrors ``src/models`` in style and in its one non-negotiable rule: money and
prices are ``decimal.Decimal`` end to end. Prices carry eight decimal places
so a crypto quantity and an ETF quantity go through the same arithmetic;
cash carries two, because that is what a ledger reconciles at.

The position maths lives here rather than in the venue on purpose. A paper
fill and a live fill must move the book identically, or the backtest is
measuring a different system than the one that trades.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Optional

from ..db.connection import to_datetime, to_iso, utcnow_iso
from ..money import D, ZERO, money

QTY = Decimal("0.00000001")  # DECIMAL(18,8)
PRICE = Decimal("0.00000001")


def qty(value) -> Decimal:
    """Quantise a quantity to the column's precision."""
    return D(value).quantize(QTY, rounding=ROUND_HALF_UP)


def price(value) -> Decimal:
    """Quantise a price to the column's precision."""
    return D(value).quantize(PRICE, rounding=ROUND_HALF_UP)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class FirmStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    KILLED = "killed"
    #: Killed, wound up, and finished: flat, capital returned, postmortem
    #: written. `KILLED` is the moment of death; this is the end of the estate.
    #: A firm sits at KILLED only while it still has a book to sell.
    BANKRUPT = "bankrupt"


class ProposalStatus(str, Enum):
    PROPOSED = "proposed"
    FILLED = "filled"
    REJECTED = "rejected"   # risk manager said no
    BLOCKED = "blocked"     # conscience or approval gate said no


class RiskVerdict(str, Enum):
    ALLOW = "allow"
    RESIZE = "resize"
    BLOCK = "block"


class EthicsVerdict(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class Bar:
    """One period of market data. OHLCV, all Decimal."""

    symbol: str
    as_of: datetime
    open: Decimal = ZERO
    high: Decimal = ZERO
    low: Decimal = ZERO
    close: Decimal = ZERO
    volume: Decimal = ZERO

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, D(getattr(self, name)))

    @property
    def typical_price(self) -> Decimal:
        return price((self.high + self.low + self.close) / 3)


@dataclass(frozen=True)
class Signal:
    """One analyst's reading.

    ``score`` runs -100 (maximally bearish) to +100 (maximally bullish) and
    ``confidence`` 0-100. An analyst with no data returns confidence 0, which
    the debate treats as silence rather than as neutrality — the same
    distinction ``safe_div`` makes in ``src/money.py``.
    """

    analyst: str
    symbol: str
    score: Decimal = ZERO
    confidence: Decimal = ZERO
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _clamp(D(self.score), D("-100"), D("100")))
        object.__setattr__(self, "confidence", _clamp(D(self.confidence), ZERO, D("100")))

    @property
    def is_silent(self) -> bool:
        return self.confidence <= 0

    @property
    def weighted(self) -> Decimal:
        return self.score * self.confidence / D("100")

    def to_dict(self) -> dict:
        return {
            "analyst": self.analyst,
            "symbol": self.symbol,
            "score": str(self.score),
            "confidence": str(self.confidence),
            "note": self.note,
        }


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


@dataclass
class TradeProposal:
    """What a firm wants to do, and the whole argument for it.

    Written to the database *before* execution, including proposals that never
    execute. "Why did this firm buy that?" has to be answerable from a row.
    """

    firm_id: Optional[int]
    symbol: str
    side: str = Side.BUY.value
    quantity: Decimal = ZERO
    reference_price: Decimal = ZERO
    notional: Decimal = ZERO
    confidence: Decimal = ZERO
    signals: list = field(default_factory=list)
    bull_case: str = ""
    bear_case: str = ""
    debate_winner: str = ""
    rationale: str = ""
    risk_verdict: str = ""
    risk_reason: str = ""
    ethics_verdict: str = ""
    ethics_reason: str = ""
    status: str = ProposalStatus.PROPOSED.value
    as_of: Optional[datetime] = None
    id: Optional[int] = None

    def __post_init__(self) -> None:
        self.quantity = qty(self.quantity)
        self.reference_price = price(self.reference_price)
        self.notional = money(self.notional or self.quantity * self.reference_price)
        self.confidence = D(self.confidence)
        if isinstance(self.side, Side):
            self.side = self.side.value

    @property
    def side_enum(self) -> Side:
        return Side(self.side)

    @property
    def is_executable(self) -> bool:
        return (
            self.status == ProposalStatus.PROPOSED.value
            and self.quantity > 0
            and self.risk_verdict != RiskVerdict.BLOCK.value
            and self.ethics_verdict != EthicsVerdict.BLOCK.value
        )

    def to_row(self) -> dict:
        return {
            "firm_id": self.firm_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": qty(self.quantity),
            "reference_price": price(self.reference_price),
            "notional": money(self.notional),
            "confidence": D(self.confidence),
            "signals": json.dumps([s.to_dict() if isinstance(s, Signal) else s for s in self.signals]),
            "bull_case": self.bull_case,
            "bear_case": self.bear_case,
            "debate_winner": self.debate_winner,
            "rationale": self.rationale,
            "risk_verdict": self.risk_verdict,
            "risk_reason": self.risk_reason,
            "ethics_verdict": self.ethics_verdict,
            "ethics_reason": self.ethics_reason,
            "status": self.status,
            "as_of": to_iso(self.as_of) or utcnow_iso(),
        }

    @classmethod
    def from_row(cls, row: dict) -> "TradeProposal":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in row.items() if k in known}
        data["as_of"] = to_datetime(data.get("as_of"))
        raw = data.get("signals")
        if isinstance(raw, str):
            try:
                data["signals"] = json.loads(raw)
            except (TypeError, ValueError):
                data["signals"] = []
        return cls(**data)


@dataclass
class Fill:
    """What actually happened. Signed ``cash_delta`` is the ledger's view."""

    firm_id: Optional[int]
    symbol: str
    side: str
    quantity: Decimal = ZERO
    price: Decimal = ZERO
    fee: Decimal = ZERO
    slippage: Decimal = ZERO
    cash_delta: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    venue: str = "paper"
    proposal_id: Optional[int] = None
    as_of: Optional[datetime] = None
    id: Optional[int] = None
    # The feed whose prices this fill was struck at. Carried in memory and
    # stored in `position_provenance` rather than on the fills row, so that
    # `to_row()` still matches the table. See migration 019.
    priced_by: str = ""

    def __post_init__(self) -> None:
        self.quantity = qty(self.quantity)
        self.price = price(self.price)
        for name in ("fee", "slippage", "cash_delta", "realized_pnl"):
            setattr(self, name, money(getattr(self, name)))
        if isinstance(self.side, Side):
            self.side = self.side.value

    @property
    def side_enum(self) -> Side:
        return Side(self.side)

    @property
    def multiplier(self) -> int:
        """Shares one unit controls: 100 for an option, 1 for everything else.

        Derived from the symbol rather than stored — see
        `options.contract_size` for why a stored multiplier is a column that
        can be wrong in four different ways, each of them a silent
        factor-of-a-hundred error in the money.
        """
        from .options import contract_size

        return contract_size(self.symbol)

    @property
    def gross(self) -> Decimal:
        """What this fill is worth in cash, contract size included.

        An option is quoted per share and traded per contract. One contract at
        $3.20 costs $320, and a `gross` that answered $3.20 would understate
        every cash movement, position cap and reconciliation by a hundred.
        """
        return money(self.quantity * self.price * D(self.multiplier))

    def to_row(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "firm_id": self.firm_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": qty(self.quantity),
            "price": price(self.price),
            "fee": money(self.fee),
            "slippage": money(self.slippage),
            "cash_delta": money(self.cash_delta),
            "realized_pnl": money(self.realized_pnl),
            "venue": self.venue,
            "as_of": to_iso(self.as_of) or utcnow_iso(),
        }

    @classmethod
    def from_row(cls, row: dict) -> "Fill":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in row.items() if k in known}
        data["as_of"] = to_datetime(data.get("as_of"))
        return cls(**data)


@dataclass
class Position:
    """A firm's holding in one symbol.

    Signed quantity: positive is long, negative is short. ``apply`` handles
    increase, decrease and flip in one place so a paper fill and a live fill
    can never disagree about what the book now says.
    """

    firm_id: Optional[int]
    symbol: str
    quantity: Decimal = ZERO
    avg_price: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    id: Optional[int] = None

    def __post_init__(self) -> None:
        self.quantity = qty(self.quantity)
        self.avg_price = price(self.avg_price)
        self.realized_pnl = money(self.realized_pnl)

    #: Below this many units a position is dust: too small to be worth a cent
    #: at any price this village trades, and too small to close without the
    #: proceeds rounding to zero.
    #:
    #: **A position that cannot be closed is closed every bar, forever.**
    #: `firm_a_etf_ii_v` held 0.00000024 EFA and sold half of it every fifteen
    #: minutes — 3.78e-06, 1.89e-06, 9.4e-07, 4.7e-07, 2.4e-07 — each fill
    #: moving zero cash and zero fee, each counted as a trade in the tick
    #: summary and in every statistic built on the fill table. Halving never
    #: reaches zero, so the loop had no end.
    #:
    #: Treating dust as flat ends it: `is_open` is what the tick, the
    #: liquidation and the reconciler all ask, so nothing proposes against a
    #: holding worth 0.000026 of a cent.
    DUST = Decimal("0.000001")

    @property
    def is_open(self) -> bool:
        return abs(self.quantity) >= self.DUST

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def multiplier(self) -> int:
        """Shares one unit controls: 100 for an option, 1 otherwise.

        Derived from the symbol — see `options.contract_size`.
        """
        from .options import contract_size

        return contract_size(self.symbol)

    def market_value(self, mark: Decimal) -> Decimal:
        return money(self.quantity * D(mark) * D(self.multiplier))

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        if self.quantity == 0:
            return ZERO
        return money((D(mark) - self.avg_price) * self.quantity * D(self.multiplier))

    def apply(self, fill: Fill) -> Decimal:
        """Fold a fill into the position. Returns realised P&L for this fill."""
        signed = qty(fill.quantity * fill.side_enum.sign)
        if signed == 0:
            return ZERO

        realized = ZERO
        old_qty = self.quantity
        new_qty = qty(old_qty + signed)

        same_direction = old_qty == 0 or (old_qty > 0) == (signed > 0)
        if same_direction:
            # Increasing (or opening): weighted-average the cost basis.
            total_cost = self.avg_price * abs(old_qty) + fill.price * abs(signed)
            self.avg_price = price(total_cost / abs(new_qty)) if new_qty != 0 else ZERO
        else:
            closing = min(abs(signed), abs(old_qty))
            direction = D(1) if old_qty > 0 else D(-1)
            realized = money(
                (fill.price - self.avg_price) * closing * direction * D(self.multiplier)
            )
            self.realized_pnl = money(self.realized_pnl + realized)
            if abs(signed) > abs(old_qty):
                # Flipped through zero: the remainder opens the other way.
                self.avg_price = fill.price
            elif new_qty == 0:
                self.avg_price = ZERO

        self.quantity = new_qty
        return realized

    def to_row(self) -> dict:
        return {
            "firm_id": self.firm_id,
            "symbol": self.symbol,
            "quantity": qty(self.quantity),
            "avg_price": price(self.avg_price),
            "realized_pnl": money(self.realized_pnl),
            "updated_at": utcnow_iso(),
        }

    @classmethod
    def from_row(cls, row: dict) -> "Position":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in row.items() if k in known})


@dataclass(frozen=True)
class CashView:
    """A firm's capital, split into the buckets that behave differently.

    The single ``cash`` column has always been the truth; what it hid is that
    two different questions have two different answers:

        available    what the *risk manager* may deploy into a new position.
                     Excludes the reserve floor, which is never spent.
        withdrawable what the *brokerage* may take back. Includes the reserve,
                     because a withdrawal lowers the allocation the floor is a
                     fraction of — handing capital back never breaches it.

    Conflating those two is what drove cash negative in the first long
    simulation, so they are named here rather than recomputed at each call
    site. Every field is derived from the ledger; nothing new is stored, and
    the reconciliation identities are untouched.
    """

    cash: Decimal = ZERO
    reserve: Decimal = ZERO
    in_positions: Decimal = ZERO

    @property
    def available(self) -> Decimal:
        """Deployable into a new position. Never negative."""
        return max(ZERO, money(self.cash - self.reserve))

    @property
    def withdrawable(self) -> Decimal:
        """Returnable to the brokerage — the reserve included."""
        return max(ZERO, money(self.cash))

    @property
    def equity(self) -> Decimal:
        return money(self.cash + self.in_positions)

    @property
    def at_floor(self) -> bool:
        return self.available <= 0

    def to_dict(self) -> dict:
        return {
            "cash": str(money(self.cash)),
            "reserve": str(money(self.reserve)),
            "in_positions": str(money(self.in_positions)),
            "available": str(self.available),
            "withdrawable": str(self.withdrawable),
            "equity": str(self.equity),
        }


@dataclass
class FirmRecord:
    """The firm row itself — capital, limits, and whether it is still alive."""

    firm_key: str
    name: str = ""
    asset_class: str = ""
    strategy: str = ""
    venue: str = "paper"
    status: str = FirmStatus.ACTIVE.value
    risk_limit: Decimal = D("0.02")
    initial_allocation: Decimal = ZERO
    allocation: Decimal = ZERO
    cash: Decimal = ZERO
    high_water_mark: Decimal = ZERO
    consecutive_losses: int = 0
    genome: dict = field(default_factory=dict)
    universe: list = field(default_factory=list)
    kill_reason: Optional[str] = None
    killed_at: Optional[datetime] = None
    id: Optional[int] = None

    def __post_init__(self) -> None:
        self.risk_limit = D(self.risk_limit)
        for name in ("initial_allocation", "allocation", "cash", "high_water_mark"):
            setattr(self, name, money(getattr(self, name)))
        self.consecutive_losses = int(self.consecutive_losses or 0)
        if isinstance(self.genome, str):
            self.genome = _loads(self.genome, {})
        if isinstance(self.universe, str):
            self.universe = _loads(self.universe, [])

    @property
    def is_active(self) -> bool:
        return self.status == FirmStatus.ACTIVE.value

    @property
    def is_killed(self) -> bool:
        """Dead, by either route. Bankruptcy is a kind of death, not a recovery.

        Every existing caller means "does not trade any more" by this, and a
        wound-up firm does not, so bankruptcy has to answer yes or each of them
        silently starts counting the estate as a living firm.
        """
        return self.status in (FirmStatus.KILLED.value, FirmStatus.BANKRUPT.value)

    @property
    def is_bankrupt(self) -> bool:
        """Wound up: flat, capital handed back, nothing left to settle."""
        return self.status == FirmStatus.BANKRUPT.value

    @property
    def is_liquidating(self) -> bool:
        """Killed but still holding a book it has to sell before it can rest."""
        return self.status == FirmStatus.KILLED.value

    def to_row(self) -> dict:
        return {
            "firm_key": self.firm_key,
            "name": self.name,
            "asset_class": self.asset_class,
            "strategy": self.strategy,
            "venue": self.venue,
            "status": self.status,
            "risk_limit": self.risk_limit,
            "initial_allocation": money(self.initial_allocation),
            "allocation": money(self.allocation),
            "cash": money(self.cash),
            "high_water_mark": money(self.high_water_mark),
            "consecutive_losses": self.consecutive_losses,
            "genome": json.dumps(self.genome, default=str),
            "universe": json.dumps(self.universe),
            "kill_reason": self.kill_reason,
            "killed_at": to_iso(self.killed_at),
            "updated_at": utcnow_iso(),
        }

    @classmethod
    def from_row(cls, row: dict) -> "FirmRecord":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in row.items() if k in known}
        data["killed_at"] = to_datetime(data.get("killed_at"))
        return cls(**data)


def _loads(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


__all__ = [
    "Bar",
    "CashView",
    "EthicsVerdict",
    "Fill",
    "FirmRecord",
    "FirmStatus",
    "Position",
    "ProposalStatus",
    "RiskVerdict",
    "Side",
    "Signal",
    "TradeProposal",
    "price",
    "qty",
]
