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
from ...db.connection import to_datetime
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


# How many bar-lengths of silence still counts as "the next bar". Three, so a
# weekend, a holiday and a short outage all stay inside one series, and a change
# of resolution or a week of missing data does not.
MAX_GAP_IN_BARS = 3


def _one_unbroken_run(observations, resolution):
    """The most recent stretch of observations spaced like one bar.

    `observations` is (timestamp, value), oldest first. Returns the tail of it
    from the last unexplained gap onwards, so the series that reaches `sharpe`
    is one series sampled one way.

    Timestamps that cannot be read are not guessed at — they end the run, on
    the same principle as everywhere else here: an observation whose place in
    time is unknown is not evidence about a rate.
    """
    if len(observations) < 2:
        return list(observations)
    limit = resolution.seconds * MAX_GAP_IN_BARS
    start = 0
    for i in range(1, len(observations)):
        before, now = observations[i - 1][0], observations[i][0]
        if before is None or now is None:
            start = i
            continue
        elapsed = (now - before).total_seconds()
        # Not just "too big a gap" but "any step that is not forward". Time
        # running backwards or standing still means two different passes over
        # the same bars have been spliced together, and the caller sorts
        # precisely so this should never fire — it is here because a silent
        # scramble is what this whole function exists to have caught once
        # already, and the cheapest guard against the next one is to refuse
        # anything that is not a step forward in time.
        if elapsed <= 0 or elapsed > limit:
            start = i
    return list(observations[start:])


def _consecutive_losing_bars(fills, resolution) -> int:
    """How many bars in a row the firm closed down. Bars, not fills.

    **The same collapse the Sharpe series gets, for the same reason.** A
    village ticking every sixty seconds against an hourly bar re-proposes the
    same decision until it stops being true, so one exit arrives as four
    identical sells a minute apart. The stored counter in `store.py` increments
    once per *fill*, so that single decision reads as four consecutive losses,
    and a firm is five sixths of the way to a kill on the strength of one.

    That is what happened. Six firms were killed between 14 and 23 August with
    a byte-identical reason, and the memecoin desk's last five minutes were one
    decision — exit DOGE and WIF — executed four times over eight legs, counted
    six deep. The losses were real money, but they were one loss, and the
    threshold is written in decisions.

    So a bar is the unit. Everything closed within one bar nets to one result,
    and only bars that closed something count: a bar the firm sat out is not a
    win, and must not reset the run.
    """
    by_bar: dict = {}
    order: list = []
    for fill in fills:
        realized = D(getattr(fill, "realized_pnl", 0) or 0)
        if realized == 0:
            continue            # an opening fill has no result to judge
        key = resolution.bar_key(getattr(fill, "as_of", None)) or str(
            getattr(fill, "as_of", "")
        )
        if key not in by_bar:
            order.append(key)
            by_bar[key] = ZERO
        by_bar[key] += realized

    run = 0
    for key in reversed(order):
        if by_bar[key] < 0:
            run += 1
        else:
            break               # the first bar that did not lose ends the run
    return run


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
        #
        # **And one series, not two spliced together.** The annualisation
        # assumes every step is one bar wide. It stops being true the moment
        # the bar changes size — switch a running village from daily to hourly
        # and the history holds forty daily steps followed by forty hourly
        # ones, differenced together and annualised at the hourly rate. The
        # daily steps are about six and a half times too large, which inflates
        # the deviation and drags the ratio down by about a fifth. Same shape
        # of error, arriving through the door marked "upgrade".
        #
        # A gap in the feed does the same thing more crudely: a week of silence
        # differenced as a single step is a week of movement wearing one bar's
        # clothing.
        #
        # So the series is cut at any gap wider than a few bars and only the
        # most recent unbroken run is used. This needs no record of when the
        # resolution changed, because the change announces itself in the
        # spacing — which is the only place it was ever really visible.
        resolution = self.config.data.resolution
        history = self.store.performance_history(firm.id, limit=90)
        by_bar: dict = {}
        for row in reversed(history):          # oldest first; last row per bar wins
            key = resolution.bar_key(row["as_of"]) or str(row["as_of"])
            by_bar[key] = (
                to_datetime(row["as_of"]),
                D(row["realized_pnl"]) + D(row["unrealized_pnl"]) - D(row.get("fees") or 0),
            )
        # The reading being taken now belongs to the current bar, and replaces
        # any earlier reading of it rather than being appended beside it.
        now_key = resolution.bar_key(market.as_of()) or str(market.as_of())
        by_bar[now_key] = (to_datetime(market.as_of()), net_pnl)

        # **Sorted by the bar, not by insertion.** `by_bar` is a dict keyed on
        # the bar date, and a dict keeps the position of a key's *first*
        # insertion. So when a replay revisits bars the history already holds —
        # `trade simulate` run twice, which is a documented thing to do — the
        # values update and the order does not, and the series comes out as
        # bars 31..60 followed by bars 1..30.
        #
        # Every difference across that seam is nonsense, and the Sharpe built
        # on it is nonsense with a decimal point. On a clean village, running
        # `simulate` a second time took `firm_b_stocks` from Sharpe 3.4004 to
        # -0.0644 and filed a kill request against a firm that had won 69% of
        # its trades, drawn down 0.42% and made money.
        observations = sorted(by_bar.values(), key=lambda pair: (pair[0] is None, pair[0]))
        pnl_curve = [value for _, value in _one_unbroken_run(observations, resolution)]
        returns = (
            [(b - a) / capital_base for a, b in zip(pnl_curve, pnl_curve[1:])]
            if capital_base > 0
            else []
        )
        # Annualised against the bar the returns were actually sampled on.
        # Passing the wrong figure here is the Sharpe kill, and it is the kill
        # switch that reads the answer.
        per_year = self.config.data.resolution.bars_per_year
        sharpe = sharpe_ratio(returns, periods_per_year=per_year) if len(returns) >= 2 else None

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
            consecutive_losses=_consecutive_losing_bars(fills, resolution),
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

            record_bar(self.store, card.firm_id, feed_name, as_of,
                       self.config.data.resolution)
        firm = self.store.require_firm_by_id(card.firm_id)
        if card.equity > D(firm.high_water_mark):
            self.store.update_firm_fields(card.firm_id, high_water_mark=money(card.equity))
        return row_id


def _cap(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, D(value)))


__all__ = ["Evaluator", "Scorecard"]
