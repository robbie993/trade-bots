"""The Evaluator — deterministic Python settles what the agents proposed.

This is the ``clawock`` principle from the build document, kept as the house
rule of the whole layer: the model proposes, Python grades. No part of a
firm's score comes from a language model, and every component of it is stored
next to the score, so a capital cut can be re-derived from a row months later.

The score is a plain weighted sum in the 0-100 range:

    50                      everyone starts even
    + return                what it made on the capital it was given
    - drawdown              what it put at risk to make it
    + win rate             consistency, once there are enough trades
    + Sharpe               return per unit of volatility

Below the sample gate the components involving trade statistics are simply
absent rather than assumed — ``sufficient_data`` on the row says which kind
of score this is, and the allocator refuses to cut capital on the strength of
a score that was computed without enough trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ...money import D, ZERO, money, percent
from ..config import TradingConfig
from ..data.market_data import MarketData
from ..firms.kill_switch import FirmMetrics
from ..indicators import drawdown_pct, sharpe as sharpe_ratio, win_rate_pct
from ..models import FirmRecord
from ..store import TradingStore


@dataclass
class Scorecard:
    firm_key: str
    firm_id: Optional[int]
    equity: Decimal = ZERO
    cash: Decimal = ZERO
    market_value: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    unrealized_pnl: Decimal = ZERO
    fees: Decimal = ZERO
    capital_base: Decimal = ZERO
    return_pct: Decimal = ZERO
    drawdown_pct: Decimal = ZERO
    win_rate_pct: Optional[Decimal] = None
    sharpe: Optional[Decimal] = None
    trades: int = 0
    closed_trades: int = 0
    consecutive_losses: int = 0
    worst_trade_pct: Decimal = ZERO
    score: Decimal = ZERO
    sufficient_data: bool = False
    components: dict = field(default_factory=dict)
    as_of: Optional[datetime] = None
    # Open positions the feed could not price. Everything above is computed
    # from marks, so this being non-empty means the rest of this card is not
    # a measurement of anything.
    unpriceable: tuple = ()
    # Open positions built on a different feed than the one marking them.
    # Arithmetically fine, economically meaningless — see migration 019.
    mispriced: tuple = ()

    @property
    def can_be_valued(self) -> bool:
        return not self.unpriceable and not self.mispriced

    def to_metrics(self) -> FirmMetrics:
        return FirmMetrics(
            trades=self.closed_trades,
            drawdown_pct=self.drawdown_pct,
            win_rate_pct=self.win_rate_pct,
            sharpe=self.sharpe,
            worst_trade_pct=self.worst_trade_pct,
            consecutive_losses=self.consecutive_losses,
            unpriceable=self.unpriceable,
            mispriced=self.mispriced,
        )

    def to_row(self) -> dict:
        return {
            "as_of": self.as_of,
            "equity": money(self.equity),
            "cash": money(self.cash),
            "market_value": money(self.market_value),
            "realized_pnl": money(self.realized_pnl),
            "unrealized_pnl": money(self.unrealized_pnl),
            "fees": money(self.fees),
            "capital_base": money(self.capital_base),
            "return_pct": self.return_pct,
            "drawdown_pct": self.drawdown_pct,
            "win_rate_pct": self.win_rate_pct if self.win_rate_pct is not None else ZERO,
            "sharpe": self.sharpe,
            "trades": self.trades,
            "consecutive_losses": self.consecutive_losses,
            "worst_trade_pct": self.worst_trade_pct,
            "score": self.score,
            "sufficient_data": self.sufficient_data,
        }


class Evaluator:
    def __init__(
        self,
        store: TradingStore,
        config: Optional[TradingConfig] = None,
    ):
        self.store = store
        self.config = config or TradingConfig()

    def evaluate(self, firm: FirmRecord, market: MarketData) -> Scorecard:
        positions = self.store.positions(firm.id, open_only=False)
        open_positions = [p for p in positions if p.is_open]

        # Which of this firm's holdings the feed cannot price right now.
        # `mark()` answers zero for a symbol with no bars, which is a perfectly
        # good answer to "what is it worth" and a catastrophic one to "has this
        # firm lost everything" — so the question is asked separately, and the
        # answer travels with the scorecard all the way to the kill switch.
        blind = tuple(
            sorted({
                p.symbol for p in open_positions
                if market.bar(p.symbol) is None
                or p.symbol in getattr(market, "unpriceable", {})
            })
        )

        # Was this book built on the feed we are marking it with? A position
        # bought at synthetic prices and valued at real ones produces arithmetic
        # that is correct and meaningless — see migration 019. Unknown
        # provenance (opened before this was recorded) is left alone: unknown is
        # not a mismatch, and treating it as one would freeze every old book.
        feed_now = str(getattr(getattr(market, "feed", None), "name", "") or "")
        built_with = self.store.provenance(firm.id) if firm.id else {}
        mismatched = tuple(
            sorted({
                p.symbol for p in open_positions
                if built_with.get(p.symbol) and built_with[p.symbol] != feed_now
            })
        )

        market_value = sum(
            (p.market_value(market.mark(p.symbol)) for p in open_positions), ZERO
        )
        unrealized = sum(
            (p.unrealized_pnl(market.mark(p.symbol)) for p in open_positions), ZERO
        )
        equity = money(firm.cash + market_value)

        fills = self.store.fills(firm.id)
        realized_list = [D(f.realized_pnl) for f in fills]
        closed = [p for p in realized_list if p != 0]
        realized = sum(realized_list, ZERO)
        fees = sum((D(f.fee) for f in fills), ZERO)

        # Performance is measured against the capital the firm was *given*, not
        # against what it currently holds. The brokerage withdraws capital from
        # losers, and a metric that divides by the shrunken allocation would
        # show a firm's numbers improving purely because money was taken away
        # from it. capital_base is the largest mandate the firm has held.
        capital_base = money(max(D(firm.initial_allocation), D(firm.allocation)))
        net_pnl = money(realized + unrealized - fees)
        return_pct = (
            percent(net_pnl / capital_base * D(100)) if capital_base > 0 else ZERO
        )

        high_water = max(D(firm.high_water_mark), equity)
        drawdown = drawdown_pct(equity, high_water)

        worst = min(closed) if closed else ZERO
        worst_pct = (
            percent(abs(worst) / capital_base * D(100))
            if capital_base > 0 and worst < 0
            else ZERO
        )

        # Sharpe over the P&L series rather than the equity series, for the
        # same reason: a capital withdrawal is not a bad day of trading.
        #
        # **One observation per market bar, not per tick.** A village ticking
        # every sixty seconds against a daily feed writes a row a minute, and
        # between bars the only thing that moves is fees. That produced a
        # series of tiny, almost identical negative steps — a small negative
        # mean over a near-zero deviation — which the annualisation multiplies
        # by root-252 into a confident, meaningless number. It killed a firm
        # with an 80% win rate, a 1.27% drawdown and a positive return.
        #
        # The bug was dimensional: `sharpe()` annualises assuming each
        # observation is a trading day, so two ticks against the same bar have
        # to be one observation. Collapsing by `as_of` is what makes the ratio
        # mean what its name says.
        history = self.store.performance_history(firm.id, limit=90)
        by_bar: dict = {}
        for row in reversed(history):          # oldest first; last row per bar wins
            by_bar[str(row["as_of"])] = (
                D(row["realized_pnl"]) + D(row["unrealized_pnl"]) - D(row.get("fees") or 0)
            )
        # The reading being taken now belongs to the current bar, and replaces
        # any earlier reading of it rather than being appended beside it.
        by_bar[str(market.as_of())] = net_pnl

        pnl_curve = list(by_bar.values())
        returns = (
            [(b - a) / capital_base for a, b in zip(pnl_curve, pnl_curve[1:])]
            if capital_base > 0
            else []
        )
        sharpe = sharpe_ratio(returns) if len(returns) >= 2 else None

        card = Scorecard(
            firm_key=firm.firm_key,
            firm_id=firm.id,
            equity=equity,
            cash=money(firm.cash),
            market_value=money(market_value),
            realized_pnl=money(realized),
            unrealized_pnl=money(unrealized),
            fees=money(fees),
            capital_base=capital_base,
            return_pct=return_pct,
            drawdown_pct=drawdown,
            win_rate_pct=win_rate_pct(closed),
            sharpe=sharpe,
            trades=len(fills),
            closed_trades=len(closed),
            consecutive_losses=firm.consecutive_losses,
            worst_trade_pct=worst_pct,
            as_of=market.as_of(),
            unpriceable=blind,
            mispriced=mismatched,
        )
        card.sufficient_data = card.closed_trades >= self.config.kill.minimum_trades
        card.score, card.components = self.score(card)
        return card

    def score(self, card: Scorecard) -> tuple[Decimal, dict]:
        """The weighted sum, with every term reported alongside the total."""
        components = {
            "base": D(50),
            # 1% of return is worth 2 points, capped so one lucky month cannot
            # buy a firm an unlimited allocation.
            "return": _cap(card.return_pct * D(2), D(-30), D(30)),
            # Drawdown is subtracted at full weight and is not capped upward:
            # a firm can lose all of its points to risk-taking alone.
            "drawdown": -_cap(card.drawdown_pct, ZERO, D(50)),
        }
        if card.sufficient_data:
            if card.win_rate_pct is not None:
                components["win_rate"] = _cap((card.win_rate_pct - D(50)) / D(2), D(-15), D(15))
            if card.sharpe is not None:
                components["sharpe"] = _cap(card.sharpe * D(5), D(-15), D(15))
        total = sum(components.values(), ZERO)
        return percent(_cap(total, ZERO, D(100))), {k: str(v) for k, v in components.items()}

    def evaluate_all(self, firms, market: MarketData) -> list[Scorecard]:
        return [self.evaluate(firm, market) for firm in firms]

    def persist(self, card: Scorecard, feed_name: str = "", as_of=None) -> int:
        """Write the scorecard and move the firm's high-water mark up.

        `feed_name` is counted against the firm's history so that "measured on
        alpaca" is later a fact rather than an assumption — see migration 021
        and src/trading/promotion.py. Promotion to live money turns on it.

        `as_of` is the market's own bar. It is required for that history to
        mean anything: this is called once per tick, the loop ticks every
        sixty seconds, and without the bar the record counts minutes of uptime
        instead of observations of the market.
        """
        row_id = self.store.record_performance(card.firm_id, card.to_row())
        if feed_name:
            from ..promotion import record_bar

            record_bar(self.store, card.firm_id, feed_name, as_of)
        firm = self.store.require_firm_by_id(card.firm_id)
        if card.equity > D(firm.high_water_mark):
            self.store.update_firm_fields(card.firm_id, high_water_mark=money(card.equity))
        return row_id


def _cap(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, D(value)))


__all__ = ["Evaluator", "Scorecard"]
