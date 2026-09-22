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
    #: What buying and holding this firm's own universe returned over exactly
    #: these bars, equal-weighted. `None` when it could not be priced, which
    #: is a refusal rather than a zero — see `hurdle_pct`.
    benchmark_pct: Optional[Decimal] = None
    #: What idle cash earned over the same bars. Always computable, because it
    #: is a function of the bar count and a rate, so the hurdle degrades to
    #: this rather than to nothing when the benchmark cannot be priced.
    cash_hurdle_pct: Decimal = ZERO
    #: Why the benchmark leg is missing, when it is. Empty when it is present.
    hurdle_note: str = ""

    @property
    def hurdle_pct(self) -> Decimal:
        """The bar this genome had to clear to have been worth running.

        **Both hurdles, not either.** Taken from
        `/Users/robbie/trade-bots-hive/hive_mind/lock.py:670`, which put it
        best: *"Beating the index alone is cleared by sitting in cash through
        a falling market; beating cash alone was never enough to justify the
        risk of being here."* `max` is what makes it both — clearing the
        higher of the two means clearing each.

        The benchmark is buy-and-hold of **the firm's own universe**, not SPY.
        A crypto desk that made 5% while BTC made 50% is a bad genome, and
        measuring it against an equity index would answer a question nobody
        asked. `benchmark.py` compares the *village* to SPY, which is a
        different question — whether any of this beats indexing — and it keeps
        its own default.

        When the benchmark cannot be priced this falls back to the cash
        hurdle and `hurdle_note` says so. That is a real weakening and it is
        recorded rather than hidden; what it must never do is fall back to
        zero, which is the bug this whole property exists to close.
        """
        cash = D(self.cash_hurdle_pct)
        if self.benchmark_pct is None:
            return cash
        return max(cash, D(self.benchmark_pct))

    @property
    def excess_pct(self) -> Decimal:
        """Return over the hurdle. The number that answers the question."""
        return percent(D(self.return_pct) - self.hurdle_pct)

    @property
    def fitness(self) -> Decimal:
        """One number the evolver can sort on.

        Excess return over the hurdle, penalised by the drawdown it took to
        get there and by having too few trades to mean anything. A genome that
        made 40% in three trades is not fitter than one that made 25% in
        ninety, and this is where that judgement is written down.

        **It used to have no hurdle at all**, and that was the single largest
        correctness gap in the village: `return_pct - maxDD/2` scores a firm
        that returned +0.5% while its own universe returned +10% as a
        *positive* result, and the evolver would happily select for it
        generation after generation. Every fitness number this repository has
        ever reported was a measure of "did it go up", never "was it worth
        doing".

        **Only ever compare this against another fitness measured over the
        same number of bars.** It is a window-sized quantity, not a rate:
        `max_drawdown_pct` can only grow as a window lengthens, so a longer
        window scores worse for reasons that have nothing to do with the
        genome. Measured on 40 random genomes, the same population scored
        -0.950 over 216 bars and -1.921 over 720 — a gap of 0.972, larger
        than any real effect found anywhere in this village. Splitting that
        by running equal-length windows at different times put the regime
        component at +0.051, so effectively all of it was window length.

        That gap was read as "evolution beats random by 0.6" for about an
        hour. Use `comparable_with` before trusting any difference, and rank
        within a window rather than comparing levels across two.
        """
        base = self.excess_pct - D(self.max_drawdown_pct) / D(2)
        if self.closed_trades < 10:
            # Not a penalty for being new — a refusal to reward a small sample.
            base = base * D(self.closed_trades) / D(10)
        return D(base).quantize(D("0.0001"))

    def comparable_with(self, other: "BacktestResult") -> bool:
        """Whether two fitnesses may be subtracted at all.

        Equal bar counts, or the difference is dominated by window length
        rather than by anything either genome did. There is deliberately no
        normalisation offered here: return scales roughly linearly with the
        window while drawdown scales nearer its square root, so no single
        divisor makes two windows comparable, and offering one would hide the
        problem behind a number that looks principled.

        The hurdles must match too. One genome scored against a priced
        benchmark and another against the cash fallback are measured off
        different bars, and subtracting them would read the missing benchmark
        as performance.
        """
        return (self.bars == other.bars and self.bars > 0
                and (self.benchmark_pct is None) == (other.benchmark_pct is None))

    def minus(self, other: "BacktestResult") -> Decimal:
        """`self.fitness - other.fitness`, refusing mismatched windows."""
        if self.bars != other.bars or self.bars <= 0:
            raise ValueError(
                f"fitness over {self.bars} bars is not comparable with "
                f"{other.bars} bars — max drawdown grows with the window, so "
                f"the difference would mostly be length. Rank within a window "
                f"instead."
            )
        if not self.comparable_with(other):
            raise ValueError(
                "one of these was scored against a priced benchmark and the "
                "other against the cash fallback, so the difference would "
                "partly be the missing benchmark rather than the genome"
            )
        return self.fitness - other.fitness

    def summary(self) -> str:
        bench = (f"hold {self.benchmark_pct}%" if self.benchmark_pct is not None
                 else f"no benchmark ({self.hurdle_note or 'unpriced'})")
        return (
            f"{self.firm_key}: {fmt_money(self.start_capital)} -> "
            f"{fmt_money(self.final_equity)} ({self.return_pct}%), "
            f"vs {bench} = {self.excess_pct}% excess, "
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
        start: Optional[int] = None,
        strategy: str = "",
    ) -> BacktestResult:
        """Replay one firm over `market`. `strategy` may name a bot.

        **`strategy="bot:bots/x.py"` backtests the file; leaving it empty
        backtests the genome through the built-in pod.** That distinction is
        the whole point of the argument, because until it existed there was no
        way to ask the first question and the second was being mistaken for it.

        `Firm.propose` has always routed on `record.strategy` — `_from_bot` when
        it names a bot, `_from_pod` otherwise — but this method never set the
        field, so every backtest silently took the pod branch. The strategy
        court is built on this method, and the consequence was that on
        2026-09-14 it tried twelve fleet strategies and rejected sixty of
        sixty-two submissions on `return` findings that belong to a different
        strategy. `veritas_reversion.py` "lost -1.26% over the sample" — the
        built-in pod did, wearing five of VERITAS's parameters. Its actual rule
        (RSI(2) < 10 *and* IBS < 0.3, gated on SMA200) was never evaluated,
        because nothing here ever ran it.

        `adapter.py` opens by naming this exact failure: the importer "works
        when the bot *is* seven genes and fails quietly when it is not." The
        court was the place it failed quietly.
        """
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
            strategy=strategy,
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
        # `start` runs the strategy over a *later* window than the warmup — the
        # held-out tail the evolver uses to check whether a genome learned
        # anything or merely memorised. Indicators still see everything before
        # it, because `seek(index)` exposes the history up to that bar; only
        # the scoring begins later.
        floor = min(self.warmup, max(0, total_bars - 2))
        first = floor if start is None else max(int(start), floor)
        first = min(first, max(floor, total_bars - 1))
        last = total_bars if steps is None else min(total_bars, first + steps)

        positions: dict = {}
        curve: list = []
        realized: list = []
        fees = ZERO
        fill_count = 0

        # Buy-and-hold of this firm's own universe, entered at the first bar
        # it was scored on and never sold. Read through `market.mark`, the
        # same price source the firm's own fills cross, so the two sides of
        # the comparison cannot disagree about what a bar was worth.
        entry_marks: dict = {}
        exit_marks: dict = {}

        for index in range(first, last):
            market.seek(index)
            if index == first:
                for symbol in record.universe:
                    try:
                        mark = D(market.mark(symbol))
                    except Exception:  # noqa: BLE001 - an unpriced leg is a refusal
                        continue
                    if mark > 0:
                        entry_marks[symbol] = mark
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

        for symbol in entry_marks:
            try:
                mark = D(market.mark(symbol))
            except Exception:  # noqa: BLE001
                continue
            if mark > 0:
                exit_marks[symbol] = mark

        benchmark_pct, hurdle_note = self._hold_pct(entry_marks, exit_marks,
                                                    record.universe)
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
            benchmark_pct=benchmark_pct,
            cash_hurdle_pct=self._cash_pct(len(curve)),
            hurdle_note=hurdle_note,
        )

    # -- the hurdles -------------------------------------------------------
    def _hold_pct(self, entry: dict, exit_: dict, universe) -> tuple:
        """Equal-weighted buy-and-hold of the firm's own universe, in percent.

        Returns ``(pct, note)``; ``pct`` is ``None`` when the benchmark cannot
        be priced and ``note`` says why. A benchmark nobody could measure must
        not quietly become a benchmark of zero — that is the same refusal
        `benchmark.py` makes, for the same reason.

        **Every leg must price, not merely some of them.** A universe of four
        where only the two that rose could be priced is not a benchmark, it is
        a survivorship filter, and it would hand the genome a hurdle lower
        than the thing it actually traded.

        Entry crosses the spread and pays a fee, exactly as the firm's own
        fills do. There is no exit cost, because the alternative being modelled
        is that you bought it and are still holding it — charging an exit the
        firm has not paid would tilt the comparison the firm's way.
        """
        wanted = [s for s in (universe or ())]
        if not wanted:
            return None, "the firm has no universe to hold"
        missing = [s for s in wanted if s not in entry or s not in exit_]
        if missing:
            return None, f"could not price {', '.join(sorted(missing)[:4])}"

        slip = D(self.config.data.slippage_bps) / D(10_000)
        fee = D(self.config.data.fee_bps) / D(10_000)
        legs = []
        for symbol in wanted:
            paid = entry[symbol] * (D(1) + slip)
            if paid <= 0:
                return None, f"{symbol} had no usable entry price"
            shares = (D(1) - fee) / paid
            legs.append(shares * exit_[symbol] - D(1))
        return percent(sum(legs, ZERO) / D(len(legs)) * D(100)), ""

    def _cash_pct(self, bars: int) -> Decimal:
        """What idle cash earned over `bars`, compounded per bar.

        Zero by default, which makes this hurdle read "at minimum, make
        money". `TRADE_CASH_YIELD_PCT` turns it into a real one. It is a
        property of the world rather than of the genome, which is why it is a
        config value and not a gene.
        """
        rate = D(self.config.data.cash_yield_pct)
        if not rate or bars <= 0:
            return ZERO
        per_bar = rate / D(100) / self.config.data.resolution.bars_per_year
        return percent(((D(1) + per_bar) ** int(bars) - D(1)) * D(100))


__all__ = ["Backtester", "BacktestResult"]
