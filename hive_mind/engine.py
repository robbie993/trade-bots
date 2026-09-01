"""The engine — the council, the book, and the honest arithmetic between them.

One asset, one book, one decision a day. The engine holds the cash, marks the
position at every close, charges the venue for every share it moves, and files
what happened in the Obsidian brain.

Cash, prices and quantities are ``Decimal`` end to end, through the same
``money()``, ``price()`` and ``qty()`` quantisers the village's ledger uses.
That is not ceremony: a book that drifts by fractions of a cent is a book
whose reconciliation is an opinion, and the performance statistics below are
the numbers a walk-forward verdict is made on.

The statistics themselves come from ``src/trading/indicators.py`` — the same
Sharpe, the same drawdown, the same win rate the village scores its firms
with. A second implementation of "how did this do" is a second answer to that
question, and the first symptom would be two parts of this repository quietly
disagreeing about the same run.

Three things it insists on, all for the same reason — that the number at the
end is worth reading:

* **The decision reads only bars that have printed.** The council is handed
  history up to and including today's close and nothing after it. That is the
  invariant a backtest is *for*, and it is one line: everything else here is
  arranged so that line cannot be quietly broken.
* **Every share pays.** Fills go through ``Venue``, which crosses the spread
  and charges impact. The equity curve is after costs or it is fiction.
* **It cannot evolve itself without permission.** ``online_evolution`` is the
  feature that makes this dangerous: a book mutating its genome from its own
  recent fills. It is routed through ``lock.permit``, which refuses unless the
  genome has already survived the walk-forward gauntlet, and refuses to touch
  strategy genes even then. With no lock attached, online evolution is simply
  off — the safe default is the one you get by forgetting to configure it.

The narration (``verbose=True``) is the point of the whole file for a first
run: it prints what the council decided, what the venue actually charged, and
what the brain filed, one day at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from src.money import D, ZERO, fmt_money, money, percent
from src.trading.indicators import max_drawdown_pct, sharpe as sharpe_ratio, win_rate_pct
from src.trading.models import price, qty

from .council import Decision, VillageCouncil
from .evolver import ALL_GENES, Evolver, Genome
from .market import Bar, Venue
from .memory import ObsidianMemory

# Below this many daily returns, a Sharpe is a number with no meaning attached.
MIN_RETURNS_FOR_SHARPE = 20


class MutationRefused(RuntimeError):
    """The engine tried to change its genome where it is not allowed to.

    Only raised under ``strict_evolution``. The default is to record the
    refusal and carry on, because a live book that halts on a refused mutation
    stops managing the positions it already holds — the refusal is meant to
    stop the genome moving, not to stop the risk being managed.
    """


@dataclass
class Trade:
    """One closed round trip, for the win rate and the memory."""

    opened: int
    closed: int
    side: str
    shares: Decimal
    entry: Decimal
    exit: Decimal
    pnl: Decimal
    return_pct: Decimal
    vix: Decimal
    regime: str


@dataclass
class Result:
    """What a run was worth, after everything it cost."""

    label: str = ""
    start_capital: Decimal = ZERO
    final_equity: Decimal = ZERO
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    fills: int = 0
    fees: Decimal = ZERO
    slippage: Decimal = ZERO
    bars: int = 0
    genome: Optional[Genome] = None
    stopped_out: int = 0

    @property
    def return_pct(self) -> Decimal:
        if not self.start_capital:
            return ZERO
        return percent((self.final_equity - self.start_capital) / self.start_capital * D(100))

    @property
    def daily_returns(self) -> list:
        curve = self.equity_curve
        return [(b - a) / a for a, b in zip(curve, curve[1:]) if a]

    @property
    def max_drawdown_pct(self) -> Decimal:
        return max_drawdown_pct(self.equity_curve)

    @property
    def sharpe(self) -> Optional[Decimal]:
        """Annualised, zero risk-free. None below a sample worth quoting."""
        returns = self.daily_returns
        if len(returns) < MIN_RETURNS_FOR_SHARPE:
            return None
        return sharpe_ratio(returns)

    @property
    def closed_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate_pct(self) -> Optional[Decimal]:
        return win_rate_pct([t.pnl for t in self.trades])

    @property
    def cost_drag_pct(self) -> Decimal:
        """What the spread, the impact and the fees took, as a share of capital."""
        if not self.start_capital:
            return ZERO
        return percent((self.slippage + self.fees) / self.start_capital * D(100))

    def summary(self) -> str:
        sharpe = f"{self.sharpe}" if self.sharpe is not None else "—"
        wins = f"{self.win_rate_pct}%" if self.win_rate_pct is not None else "—"
        return (
            f"{self.label or 'run'}: {fmt_money(self.start_capital)} -> "
            f"{fmt_money(self.final_equity)} ({self.return_pct:+}%), "
            f"max drawdown {self.max_drawdown_pct}%, sharpe {sharpe}, "
            f"{self.closed_trades} closed trades, win rate {wins}, "
            f"costs {self.cost_drag_pct}% of capital over {self.bars} bars"
        )


class GodBrokerEngine:
    """The whole village, running one book."""

    def __init__(
        self,
        genome: Genome,
        memory: Optional[ObsidianMemory] = None,
        venue: Optional[Venue] = None,
        capital=D(100_000),
        asset: str = "SPY",
        scouts: int = 5,
        seed: int = 0,
        lock=None,
        online_evolution: bool = False,
        strict_evolution: bool = False,
        evolver: Optional[Evolver] = None,
        verbose: bool = False,
    ):
        self.genome = genome
        self.memory = memory if memory is not None else ObsidianMemory()
        self.venue = venue if venue is not None else Venue()
        self.council = VillageCouncil(self.memory, scouts=scouts, seed=seed)
        self.asset = asset
        self.seed = seed
        self.lock = lock
        self.online_evolution = bool(online_evolution)
        self.strict_evolution = bool(strict_evolution)
        self.evolver = evolver or Evolver(seed=seed or 20260901)
        self.verbose = verbose

        self.start_capital = money(capital)
        self.cash = money(capital)
        self.shares = ZERO
        self.entry_price = ZERO
        self.entry_index = 0

        self.history: list = []
        self.equity_curve: list = []
        self.trades: list = []
        self.stopped_out = 0
        self.mutations = 0
        self.refusals: list = []

    # -- the book ---------------------------------------------------------
    @property
    def position_value(self) -> Decimal:
        return money(self.shares * (self.history[-1].close if self.history else ZERO))

    def equity(self, bar: Optional[Bar] = None) -> Decimal:
        mark = bar.close if bar is not None else (self.history[-1].close if self.history else ZERO)
        return money(self.cash + self.shares * mark)

    def _target_shares(self, decision: Decision, bar: Bar) -> Decimal:
        """What the book should hold if the council gets its way."""
        if decision.action == "hold":
            return self.shares
        if decision.action == "sell":
            return ZERO  # flat, not short: `hedge` is how this engine goes short
        equity = self.equity(bar)
        exposure = self.genome.position_pct * decision.leverage * equity
        exposure = min(exposure, equity * self.genome.leverage_cap)
        target = qty(exposure / max(bar.close, D("0.01")))
        return target if decision.action == "buy" else -target

    def _trade_to(self, target: Decimal, bar: Bar, note: str) -> Optional[Decimal]:
        """Move the book to ``target`` shares. Returns realised P&L, if any."""
        target = qty(target)
        delta = target - self.shares
        if abs(delta) * bar.close < self.equity(bar) * D("0.002"):
            return None  # too small to be worth the spread

        side = "buy" if delta > 0 else "sell"
        fill = self.venue.execute(side, abs(delta), bar)
        if fill is None:
            return None

        realised = None
        closing = (self.shares > 0 > target) or (self.shares < 0 < target) or (
            self.shares != 0 and target == 0
        )
        reducing = abs(target) < abs(self.shares) and self.shares != 0
        if reducing or closing:
            closed_shares = min(abs(self.shares), abs(delta))
            direction = D(1) if self.shares > 0 else D(-1)
            realised = money(closed_shares * (fill.price - self.entry_price) * direction)
            entry_notional = money(closed_shares * self.entry_price)
            return_pct = (
                percent(realised / entry_notional * D(100)) if entry_notional else ZERO
            )
            self.trades.append(
                Trade(
                    opened=self.entry_index,
                    closed=bar.index,
                    side="long" if direction > 0 else "short",
                    shares=closed_shares,
                    entry=self.entry_price,
                    exit=fill.price,
                    pnl=realised,
                    return_pct=return_pct,
                    vix=bar.vix,
                    regime=bar.regime,
                )
            )
            self.memory.store(
                self.asset,
                bar.vix,
                "long" if direction > 0 else "short",
                realised,
                return_pct,
                note=note,
            )

        if self.shares == 0 or (self.shares > 0) != (target > 0) or abs(target) > abs(self.shares):
            # Opening, or adding to, a position: the entry price is the
            # weighted one, so a scale-in cannot flatter the exit.
            if self.shares != 0 and (self.shares > 0) == (target > 0):
                total = abs(self.shares) + abs(delta)
                self.entry_price = price(
                    (abs(self.shares) * self.entry_price + abs(delta) * fill.price) / total
                )
            else:
                self.entry_price = fill.price
                self.entry_index = bar.index

        self.cash = money(self.cash + fill.cash_delta)
        self.shares = target
        if self.shares == 0:
            self.entry_price = ZERO
        return realised

    # -- the stop ---------------------------------------------------------
    def _stop_hit(self, bar: Bar) -> bool:
        if self.shares == 0 or self.entry_price <= 0:
            return False
        direction = D(1) if self.shares > 0 else D(-1)
        move = direction * (bar.close - self.entry_price) / self.entry_price
        return move * D(100) <= -self.genome.stop_loss_pct

    # -- one day ----------------------------------------------------------
    def step(self, bar: Bar) -> Decision:
        self.history.append(bar)

        if self.verbose:
            print(
                f"\n--- {bar.day} | close {fmt_money(bar.close)} | VIX {bar.vix} "
                f"| sentiment {bar.sentiment:+} | {bar.regime} ---"
            )

        # The stop comes before the debate. A book that argues about a new
        # trade while an old one is past its stop is a book with no stop.
        if self._stop_hit(bar):
            self._trade_to(ZERO, bar, note="stop loss")
            self.stopped_out += 1
            if self.verbose:
                print(f"🛑 Stop loss at {self.genome.stop_loss_pct}% — flat.")

        decision = self.council.debate(bar, self.history, self.genome, self.asset)
        if self.verbose:
            print(f"🧠 Council: {decision}")
            if decision.reason:
                print(f"   {decision.reason}")

        before = self.equity(bar)
        if decision.is_trade:
            target = self._target_shares(decision, bar)
            realised = self._trade_to(target, bar, note=decision.reason)
            if self.verbose and realised is not None:
                print(f"💰 Closed for {fmt_money(realised)}")

        if self.verbose and self.shares:
            print(
                f"📈 Position: {self.shares:+f} shares "
                f"({fmt_money(abs(self.position_value))}, entry {fmt_money(self.entry_price)})"
            )

        equity = self.equity(bar)
        self.equity_curve.append(equity)

        # Settle the scouts who backed this call, on tomorrow's terms: a call
        # is judged by the next bar, so this happens one day late, on purpose.
        if len(self.history) >= 2 and decision.is_trade:
            previous = self.history[-2].close
            moved = (bar.close - previous) / previous if previous else ZERO
            wanted_up = decision.action == "buy"
            self.council.settle(decision, was_right=(moved > 0) == wanted_up)

        if self.online_evolution:
            self._maybe_evolve(bar)

        if self.verbose:
            change = percent((equity - before) / before * D(100)) if before else ZERO
            print(
                f"📝 Memory: {self.memory.nodes()} node(s), "
                f"{self.memory.total_trades()} trade(s) | equity {fmt_money(equity)} "
                f"({change:+}% today)"
            )
        return decision

    # -- evolution, which is the dangerous part -----------------------------
    def _maybe_evolve(self, bar: Bar) -> None:
        """Mutate the live genome — if, and only if, something says it may.

        This is the behaviour that makes a live deployment lose money quietly:
        a book with a fortnight of its own fills, mutating toward whatever
        those fills happened to reward. The refusal below is the whole safety
        story, and it is checked here rather than at the call site so that no
        future caller can route around it.
        """
        if len(self.history) < 30 or len(self.history) % 10:
            return

        genes = ALL_GENES
        if self.lock is not None:
            verdict = self.lock.permit(self.genome)
            if not verdict.allowed:
                self._refuse(verdict.reason)
                return
            genes = verdict.genes
        else:
            self._refuse(
                "online evolution asked for, but no walk-forward lock is attached — refused"
            )
            return

        rng = self.evolver.rng(self.mutations, tag="online")
        mutant = self.evolver.mutate(self.genome, rng, genes=genes)
        if mutant.fingerprint() == self.genome.fingerprint():
            return
        self.evolver.record(self.mutations, self.genome, mutant)
        if self.verbose:
            print(f"🧬 Genome mutated ({', '.join(sorted(genes))} only): {self.genome.diff(mutant)}")
        self.genome = mutant
        self.mutations += 1

    def _refuse(self, reason: str) -> None:
        self.refusals.append(reason)
        if self.strict_evolution:
            raise MutationRefused(reason)

    # -- a whole run ------------------------------------------------------
    def run(self, feed, label: str = "") -> Result:
        bars = list(feed)
        for bar in bars:
            self.step(bar)
        if self.shares and bars:
            self._trade_to(ZERO, bars[-1], note="end of run")
            self.equity_curve[-1] = self.equity(bars[-1])

        return Result(
            label=label,
            start_capital=self.start_capital,
            final_equity=self.equity_curve[-1] if self.equity_curve else self.start_capital,
            equity_curve=list(self.equity_curve),
            trades=list(self.trades),
            fills=self.venue.fills,
            fees=self.venue.total_fees,
            slippage=self.venue.total_slippage,
            bars=len(bars),
            genome=self.genome,
            stopped_out=self.stopped_out,
        )


def backtest(
    genome: Genome,
    feed,
    capital=D(100_000),
    seed: int = 0,
    venue: Optional[Venue] = None,
    memory: Optional[ObsidianMemory] = None,
    label: str = "",
) -> Result:
    """One frozen run. No evolution, no lock, nothing carried between calls."""
    engine = GodBrokerEngine(
        genome=genome,
        memory=memory if memory is not None else ObsidianMemory(),
        venue=venue if venue is not None else Venue(),
        capital=capital,
        seed=seed,
        online_evolution=False,
    )
    return engine.run(feed, label=label)


__all__ = ["GodBrokerEngine", "MutationRefused", "Result", "Trade", "backtest"]
