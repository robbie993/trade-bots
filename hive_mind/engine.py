"""The engine — the council, the book, and the honest arithmetic between them.

One asset, one book, one decision a day. The engine holds the cash, marks the
position at every close, charges the venue for every share it moves, and files
what happened in the Obsidian brain.

Three things it insists on, all of them for the same reason — that the number
at the end is worth reading:

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

import math
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Optional

from .council import Decision, VillageCouncil
from .evolver import ALL_GENES, Evolver, Genome
from .market import Bar, Venue
from .memory import ObsidianMemory

TRADING_DAYS = 252


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
    shares: float
    entry: float
    exit: float
    pnl: float
    return_pct: float
    vix: float
    regime: str


@dataclass
class Result:
    """What a run was worth, after everything it cost."""

    label: str = ""
    start_capital: float = 0.0
    final_equity: float = 0.0
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    fills: int = 0
    fees: float = 0.0
    slippage: float = 0.0
    bars: int = 0
    genome: Optional[Genome] = None
    stopped_out: int = 0

    @property
    def return_pct(self) -> float:
        if not self.start_capital:
            return 0.0
        return 100.0 * (self.final_equity - self.start_capital) / self.start_capital

    @property
    def daily_returns(self) -> list:
        curve = self.equity_curve
        return [(b - a) / a for a, b in zip(curve, curve[1:]) if a]

    @property
    def max_drawdown_pct(self) -> float:
        peak, worst = None, 0.0
        for value in self.equity_curve:
            peak = value if peak is None else max(peak, value)
            if peak:
                worst = max(worst, 100.0 * (peak - value) / peak)
        return worst

    @property
    def sharpe(self) -> Optional[float]:
        """Annualised, zero risk-free. None below a sample worth quoting."""
        returns = self.daily_returns
        if len(returns) < 20:
            return None
        deviation = pstdev(returns)
        if deviation == 0:
            return None
        return mean(returns) / deviation * math.sqrt(TRADING_DAYS)

    @property
    def closed_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate_pct(self) -> Optional[float]:
        if not self.trades:
            return None
        return 100.0 * sum(1 for t in self.trades if t.pnl > 0) / len(self.trades)

    @property
    def cost_drag_pct(self) -> float:
        """What the spread, the impact and the fees took, as a share of capital."""
        if not self.start_capital:
            return 0.0
        return 100.0 * (self.slippage + self.fees) / self.start_capital

    def summary(self) -> str:
        sharpe = f"{self.sharpe:.2f}" if self.sharpe is not None else "—"
        wins = f"{self.win_rate_pct:.0f}%" if self.win_rate_pct is not None else "—"
        return (
            f"{self.label or 'run'}: ${self.start_capital:,.0f} -> ${self.final_equity:,.0f} "
            f"({self.return_pct:+.2f}%), max drawdown {self.max_drawdown_pct:.2f}%, "
            f"sharpe {sharpe}, {self.closed_trades} closed trades, win rate {wins}, "
            f"costs {self.cost_drag_pct:.2f}% of capital over {self.bars} bars"
        )


class GodBrokerEngine:
    """The whole village, running one book."""

    def __init__(
        self,
        genome: Genome,
        memory: Optional[ObsidianMemory] = None,
        venue: Optional[Venue] = None,
        capital: float = 100_000.0,
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

        self.start_capital = float(capital)
        self.cash = float(capital)
        self.shares = 0.0
        self.entry_price = 0.0
        self.entry_index = 0

        self.history: list = []
        self.equity_curve: list = []
        self.trades: list = []
        self.stopped_out = 0
        self.mutations = 0
        self.refusals: list = []

    # -- the book ---------------------------------------------------------
    @property
    def position_value(self) -> float:
        return self.shares * (self.history[-1].close if self.history else 0.0)

    def equity(self, bar: Optional[Bar] = None) -> float:
        mark = bar.close if bar is not None else (self.history[-1].close if self.history else 0.0)
        return self.cash + self.shares * mark

    def _target_shares(self, decision: Decision, bar: Bar) -> float:
        """What the book should hold if the council gets its way."""
        if decision.action == "hold":
            return self.shares
        if decision.action == "sell":
            return 0.0  # flat, not short: `hedge` is how this engine goes short
        exposure = self.genome.position_pct * decision.leverage * self.equity(bar)
        exposure = min(exposure, self.equity(bar) * self.genome.leverage_cap)
        target = exposure / max(bar.close, 0.01)
        return target if decision.action == "buy" else -target

    def _trade_to(self, target: float, bar: Bar, note: str) -> Optional[float]:
        """Move the book to ``target`` shares. Returns realised P&L, if any."""
        delta = target - self.shares
        if abs(delta) * bar.close < self.equity(bar) * 0.002:
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
            direction = 1.0 if self.shares > 0 else -1.0
            realised = closed_shares * (fill.price - self.entry_price) * direction
            entry_notional = closed_shares * self.entry_price
            self.trades.append(
                Trade(
                    opened=self.entry_index,
                    closed=bar.index,
                    side="long" if direction > 0 else "short",
                    shares=closed_shares,
                    entry=self.entry_price,
                    exit=fill.price,
                    pnl=realised,
                    return_pct=100.0 * realised / entry_notional if entry_notional else 0.0,
                    vix=bar.vix,
                    regime=bar.regime,
                )
            )
            self.memory.store(
                self.asset,
                bar.vix,
                "long" if direction > 0 else "short",
                realised,
                100.0 * realised / entry_notional if entry_notional else 0.0,
                note=note,
            )

        if self.shares == 0 or (self.shares > 0) != (target > 0) or abs(target) > abs(self.shares):
            # Opening, or adding to, a position: the entry price is the
            # weighted one, so a scale-in cannot flatter the exit.
            if self.shares != 0 and (self.shares > 0) == (target > 0):
                total = abs(self.shares) + abs(delta)
                self.entry_price = (
                    abs(self.shares) * self.entry_price + abs(delta) * fill.price
                ) / total
            else:
                self.entry_price = fill.price
                self.entry_index = bar.index

        self.cash += fill.cash_delta
        self.shares = target
        if abs(self.shares) < 1e-9:
            self.shares = 0.0
            self.entry_price = 0.0
        return realised

    # -- the stop ---------------------------------------------------------
    def _stop_hit(self, bar: Bar) -> bool:
        if self.shares == 0 or self.entry_price <= 0:
            return False
        direction = 1.0 if self.shares > 0 else -1.0
        move = direction * (bar.close - self.entry_price) / self.entry_price
        return move * 100.0 <= -self.genome.stop_loss_pct

    # -- one day ----------------------------------------------------------
    def step(self, bar: Bar) -> Decision:
        self.history.append(bar)

        if self.verbose:
            print(
                f"\n--- {bar.day} | close ${bar.close:,.2f} | VIX {bar.vix:.1f} "
                f"| sentiment {bar.sentiment:+.2f} | {bar.regime} ---"
            )

        # The stop comes before the debate. A book that argues about a new
        # trade while an old one is past its stop is a book with no stop.
        if self._stop_hit(bar):
            self._trade_to(0.0, bar, note="stop loss")
            self.stopped_out += 1
            if self.verbose:
                print(f"🛑 Stop loss at {self.genome.stop_loss_pct:.1f}% — flat.")

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
                print(f"💰 Closed for {realised:+,.2f}")

        if self.verbose and self.shares:
            print(
                f"📈 Position: {self.shares:+,.1f} shares "
                f"(${abs(self.position_value):,.0f}, entry ${self.entry_price:,.2f})"
            )

        equity = self.equity(bar)
        self.equity_curve.append(equity)

        # Settle the scouts who backed this call, on tomorrow's terms: a call
        # is judged by the next bar, so this happens one day late, on purpose.
        if len(self.history) >= 2 and decision.is_trade:
            moved = (bar.close - self.history[-2].close) / self.history[-2].close
            wanted_up = decision.action == "buy"
            self.council.settle(decision, was_right=(moved > 0) == wanted_up)

        if self.online_evolution:
            self._maybe_evolve(bar)

        if self.verbose:
            print(
                f"📝 Memory: {self.memory.nodes()} node(s), "
                f"{self.memory.total_trades()} trade(s) | equity ${equity:,.2f} "
                f"({100 * (equity - before) / before:+.2f}% today)"
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
            self._trade_to(0.0, bars[-1], note="end of run")
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
    capital: float = 100_000.0,
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
