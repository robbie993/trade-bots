"""Where the money actually went.

"We are losing money" is a feeling. This turns it into an arithmetic identity
with one line per cause, because the four things that empty a paper account
look identical on the equity curve and want completely different fixes:

``costs``
    Fees and slippage. Every round trip pays them, so a strategy that trades
    twice as often pays twice as much for the same edge. This is the failure
    that arrives quietly after a change nobody thought was risky — moving from
    daily to hourly bars multiplies the trade count without improving a single
    decision, and the bill lands immediately while the benefit takes weeks.

``losing entries``
    Trades that went the wrong way. A real edge problem, and the only one of
    the four the evolver can do anything about.

``the exit``
    Cutting winners short and letting losers run. It shows up as an average
    win smaller than the average loss even when more than half the trades are
    profitable, and no amount of better entries repairs it.

``being wrong more often than right``
    A win rate below what the payoff ratio needs. Different from the above:
    the trades are sized fine and exited fine, there are simply not enough
    good ones.

Everything here is read from `fills`. Nothing is estimated, modelled or
assumed, and every figure is a `Decimal`, because a post-mortem that disagrees
with the ledger is a second opinion nobody asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..money import D, ZERO, fmt_money, money, percent

#: Costs above this share of gross winnings and the trading frequency itself is
#: the problem, whatever the strategy thinks it is doing.
COST_ALARM_PCT = D("30")

#: An average loss this much bigger than the average win means the exit is
#: broken — the entries can be right and the account still drains.
PAYOFF_ALARM = D("1.5")


@dataclass
class SymbolLine:
    symbol: str
    closed: int = 0
    wins: int = 0
    gross: Decimal = ZERO          # realised P&L before costs
    costs: Decimal = ZERO
    net: Decimal = ZERO

    @property
    def win_rate(self) -> Optional[Decimal]:
        return percent(D(self.wins) / D(self.closed) * D(100)) if self.closed else None


@dataclass
class PostMortem:
    firm_key: str
    capital: Decimal = ZERO
    equity: Decimal = ZERO
    realized: Decimal = ZERO
    unrealized: Decimal = ZERO
    costs: Decimal = ZERO
    fills: int = 0
    closed: int = 0
    wins: int = 0
    losses: int = 0
    gross_won: Decimal = ZERO
    gross_lost: Decimal = ZERO
    symbols: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    # -- the ratios that name the disease ---------------------------------
    @property
    def net(self) -> Decimal:
        return money(self.equity - self.capital)

    @property
    def win_rate(self) -> Optional[Decimal]:
        return percent(D(self.wins) / D(self.closed) * D(100)) if self.closed else None

    @property
    def average_win(self) -> Optional[Decimal]:
        return money(self.gross_won / D(self.wins)) if self.wins else None

    @property
    def average_loss(self) -> Optional[Decimal]:
        return money(abs(self.gross_lost) / D(self.losses)) if self.losses else None

    @property
    def payoff(self) -> Optional[Decimal]:
        """Average loss ÷ average win. Above 1 means losses are bigger."""
        win, loss = self.average_win, self.average_loss
        if not win or win <= 0 or loss is None:
            return None
        return percent(loss / win)

    @property
    def cost_share(self) -> Optional[Decimal]:
        """Costs as a share of gross winnings. The trading-too-much number."""
        if self.gross_won <= 0:
            return None
        return percent(self.costs / self.gross_won * D(100))

    @property
    def breakeven_win_rate(self) -> Optional[Decimal]:
        """The win rate this payoff ratio needs just to stand still.

        Printed beside the actual one, because "55% of trades win" means
        nothing until you know whether 55% is enough.
        """
        ratio = self.payoff
        if ratio is None or ratio <= 0:
            return None
        return percent(ratio / (D(1) + ratio) * D(100))


def _cost_of(fill) -> Decimal:
    return money(D(fill.fee or 0) + abs(D(getattr(fill, "slippage", 0) or 0)))


def examine(store, firm, card=None) -> PostMortem:
    """Take one firm apart. Reads the ledger; changes nothing."""
    out = PostMortem(firm_key=firm.firm_key)
    out.capital = money(firm.initial_allocation or firm.allocation)
    out.equity = money(card.equity) if card is not None else money(firm.cash)
    out.unrealized = money(card.unrealized_pnl) if card is not None else ZERO

    by_symbol: dict = {}
    for fill in store.fills(firm.id):
        out.fills += 1
        cost = _cost_of(fill)
        out.costs = money(out.costs + cost)
        pnl = D(fill.realized_pnl or 0)
        line = by_symbol.setdefault(fill.symbol, SymbolLine(symbol=fill.symbol))
        line.costs = money(line.costs + cost)
        if pnl != 0:
            out.closed += 1
            out.realized = money(out.realized + pnl)
            line.closed += 1
            line.gross = money(line.gross + pnl)
            if pnl > 0:
                out.wins += 1
                line.wins += 1
                out.gross_won = money(out.gross_won + pnl)
            else:
                out.losses += 1
                out.gross_lost = money(out.gross_lost + pnl)
    for line in by_symbol.values():
        line.net = money(line.gross - line.costs)
    out.symbols = sorted(by_symbol.values(), key=lambda s: s.net)
    out.notes = _diagnose(out)
    return out


def _diagnose(pm: PostMortem) -> list:
    """Name what is wrong, in the order worth acting on.

    Deliberately conservative: below the sample gate it says so and stops,
    because the whole village refuses to draw conclusions from a handful of
    trades and a post-mortem that ignored that rule would be the loudest place
    it got broken.
    """
    notes: list = []

    if pm.fills == 0:
        return ["Nothing has traded yet. There is no loss to explain."]

    if pm.closed < 20:
        notes.append(
            f"Only {pm.closed} closed trade(s) — below the sample gate. Everything "
            "below is description, not diagnosis: a run of bad luck and a broken "
            "strategy look identical at this size."
        )

    share = pm.cost_share
    if share is not None and share >= COST_ALARM_PCT:
        notes.append(
            f"COSTS ARE THE PROBLEM. Fees and slippage came to {fmt_money(pm.costs)}, "
            f"which is {share}% of everything the winning trades made. The edge is "
            "being spent on turnover rather than lost on direction — trade less "
            "often (a slower TRADE_BAR, a higher confidence floor) before touching "
            "the strategy."
        )
    elif pm.gross_won <= 0 and pm.costs > 0:
        notes.append(
            f"Every closed trade lost, and costs added {fmt_money(pm.costs)} on top. "
            "The direction is the problem, not the turnover."
        )

    payoff = pm.payoff
    if payoff is not None and payoff >= PAYOFF_ALARM:
        notes.append(
            f"THE EXIT IS THE PROBLEM. The average loss ({fmt_money(pm.average_loss)}) "
            f"is {payoff}x the average win ({fmt_money(pm.average_win)}) — winners are "
            "being cut short and losers held. Better entries do not repair this; a "
            "tighter stop or a slower exit does."
        )

    rate, need = pm.win_rate, pm.breakeven_win_rate
    if rate is not None and need is not None and rate < need:
        notes.append(
            f"It wins {rate}% of the time and needs {need}% to break even at this "
            "payoff ratio. Either the entries have to get better or the winners "
            "have to get bigger."
        )

    worst = [s for s in pm.symbols if s.net < 0][:3]
    if worst and len(pm.symbols) > 1:
        names = ", ".join(f"{s.symbol} ({fmt_money(s.net)})" for s in worst)
        notes.append(
            f"Worst names: {names}. If one symbol is most of the loss, narrowing "
            "the universe is cheaper than re-tuning the strategy."
        )

    if not notes or all(n.startswith("Only ") for n in notes):
        notes.append(
            "No single cause stands out — costs, exits and win rate are all inside "
            "their limits. That is what an ordinary losing stretch looks like."
        )
    return notes


def table(pm: PostMortem) -> list:
    """The identity, one line per cause. Every row is read, none is modelled."""
    rows = [
        {"line": "capital entrusted", "amount": fmt_money(pm.capital)},
        {"line": "realised P&L (gross)", "amount": fmt_money(pm.realized)},
        {"line": "  from winners", "amount": fmt_money(pm.gross_won)},
        {"line": "  from losers", "amount": fmt_money(pm.gross_lost)},
        {"line": "fees and slippage", "amount": fmt_money(-pm.costs)},
        {"line": "open positions (unrealised)", "amount": fmt_money(pm.unrealized)},
        {"line": "equity now", "amount": fmt_money(pm.equity)},
        {"line": "NET", "amount": fmt_money(pm.net)},
    ]
    return rows


__all__ = ["COST_ALARM_PCT", "PAYOFF_ALARM", "PostMortem", "SymbolLine",
           "examine", "table"]
