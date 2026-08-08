"""Backtesting — the same pod, the same fills, no database.

The runner drives a ``Firm`` over a market cursor and settles its proposals
with the same ``PaperVenue`` arithmetic the live paper loop uses. Nothing here
is a second implementation of the strategy: if the backtest and the paper
loop disagree, it is because the *data* differed, not the code.

Three rules that keep the numbers honest:

* A warm-up window is skipped before the first trade, so no decision is made
  on a half-filled indicator.
* Positions are marked at the close of the bar the decision was made on, and
  the fill happens at that same bar. No decision ever reads a price that had
  not printed yet — enforced by ``MarketData``'s cursor, not by convention.
* Fees and slippage are charged on every fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Sequence

from ..money import D, ZERO, fmt_money, money, percent
from .config import TradingConfig
from .data.market_data import MarketData
from .execution.paper import PaperVenue
from .firms.analysts import build_analysts
from .firms.firm import Firm
from .indicators import max_drawdown_pct, sharpe as sharpe_ratio, win_rate_pct
from .models import FirmRecord, Position, Side


@dataclass
class BacktestResult:
    firm_key: str
    genome: dict = field(default_factory=dict)
    start_capital: Decimal = ZERO
    final_equity: Decimal = ZERO
    return_pct: Decimal = ZERO
    max_drawdown_pct: Decimal = ZERO
    win_rate_pct: Optional[Decimal] = None
    sharpe: Optional[Decimal] = None
    trades: int = 0
    closed_trades: int = 0
    fees: Decimal = ZERO
    equity_curve: list = field(default_factory=list)
    bars: int = 0

    @property
    def fitness(self) -> Decimal:
        """One number the evolver can sort on.

        Return, penalised by the drawdown it took to get there and by having
        too few trades to mean anything. A genome that made 40% in three
        trades is not fitter than one that made 25% in ninety, and this is
        where that judgement is written down.
        """
        base = D(self.return_pct) - D(self.max_drawdown_pct) / D(2)
        if self.closed_trades < 10:
            # Not a penalty for being new — a refusal to reward a small sample.
            base = base * D(self.closed_trades) / D(10)
        return D(base).quantize(D("0.0001"))

    def summary(self) -> str:
        return (
            f"{self.firm_key}: {fmt_money(self.start_capital)} -> "
            f"{fmt_money(self.final_equity)} ({self.return_pct}%), "
            f"max drawdown {self.max_drawdown_pct}%, {self.closed_trades} closed trades, "
            f"win rate {self.win_rate_pct}%, sharpe {self.sharpe}, "
            f"fees {fmt_money(self.fees)}, fitness {self.fitness}"
        )


class Backtester:
    def __init__(self, config: Optional[TradingConfig] = None, warmup: int = 90):
        self.config = config or TradingConfig()
        self.venue = PaperVenue(self.config.data)
        self.warmup = warmup

    def run(
        self,
        firm_key: str,
        symbols: Sequence[str],
        market: MarketData,
        genome: Optional[dict] = None,
        analysts: Sequence[str] = ("technical", "sentiment", "macro"),
        capital: Optional[Decimal] = None,
        risk_limit: Optional[Decimal] = None,
        steps: Optional[int] = None,
    ) -> BacktestResult:
        start_capital = money(capital if capital is not None else self.config.firm.allocation)
        record = FirmRecord(
            firm_key=firm_key,
            name=firm_key,
            allocation=start_capital,
            cash=start_capital,
            initial_allocation=start_capital,
            high_water_mark=start_capital,
            risk_limit=D(risk_limit if risk_limit is not None else self.config.firm.risk_limit),
            genome=dict(genome or {}),
            universe=[s.upper() for s in symbols],
            id=None,
        )
        firm = Firm(
            record,
            analysts=build_analysts(analysts),
            limits=self.config.firm,
            kill_config=self.config.kill,
        )

        market.register(record.universe)
        total_bars = market.length()
        first = min(self.warmup, max(0, total_bars - 2))
        last = total_bars if steps is None else min(total_bars, first + steps)

        positions: dict = {}
        curve: list = []
        realized: list = []
        fees = ZERO
        fill_count = 0

        for index in range(first, last):
            market.seek(index)
            open_positions = [p for p in positions.values() if p.is_open]
            for proposal in firm.propose(market, open_positions):
                if not proposal.is_executable:
                    continue
                fill = self.venue.execute(proposal, market.mark(proposal.symbol))
                position = positions.setdefault(
                    proposal.symbol, Position(firm_id=None, symbol=proposal.symbol)
                )
                gross = money(fill.quantity * fill.price)
                cash_delta = money(
                    -gross - fill.fee if fill.side_enum is Side.BUY else gross - fill.fee
                )
                if record.cash + cash_delta < 0:
                    continue  # the backtest does not get credit the live loop would refuse
                pnl = position.apply(fill)
                record.cash = money(record.cash + cash_delta)
                fees = money(fees + fill.fee)
                fill_count += 1
                if pnl != 0:
                    realized.append(pnl)

            equity = money(
                record.cash
                + sum(
                    (p.market_value(market.mark(p.symbol)) for p in positions.values() if p.is_open),
                    ZERO,
                )
            )
            curve.append(equity)
            record.high_water_mark = max(record.high_water_mark, equity)

        final_equity = curve[-1] if curve else start_capital
        return BacktestResult(
            firm_key=firm_key,
            genome=dict(genome or {}),
            start_capital=start_capital,
            final_equity=final_equity,
            return_pct=percent((final_equity - start_capital) / start_capital * D(100))
            if start_capital
            else ZERO,
            max_drawdown_pct=max_drawdown_pct(curve),
            win_rate_pct=win_rate_pct(realized),
            sharpe=sharpe_ratio(
                [(b - a) / a for a, b in zip(curve, curve[1:]) if a != 0]
            ),
            trades=fill_count,
            closed_trades=len(realized),
            fees=fees,
            equity_curve=curve,
            bars=len(curve),
        )


__all__ = ["Backtester", "BacktestResult"]
