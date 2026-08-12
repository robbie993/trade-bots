"""The firm-level Kill Switch — ``src/kill_criteria.py`` for a trading pod.

Same two rules as the product version, and they matter more here because a
strategy can lose money faster than a store can:

1. No decision is made until the sample gate is met. Below ``minimum_trades``
   the answer is "Insufficient data", never "kill". A firm that lost its
   first three trades has not been proven bad.
2. Conditions are evaluated in a fixed order, so the reason attached to a
   kill is reproducible from the stored metrics.

What a trip does is also unchanged: the switch *pauses* the firm — no new
positions, exits still allowed — and asks a human whether to kill it. Pausing
is stopping the bleeding, so it is autonomous. Killing is final, so it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from ...money import D, ZERO
from ..config import FirmKillConfig

INSUFFICIENT_DATA = "Insufficient data"

# Distinct from INSUFFICIENT_DATA on purpose: one means "not enough trades yet
# to judge this strategy", the other means "the numbers in front of me are not
# real". They are both refusals to kill, and confusing them in a log would hide
# an outage behind a firm that merely looks new.
CANNOT_VALUE = "Cannot value this book"


@dataclass(frozen=True)
class FirmMetrics:
    """The read-only surface the kill logic needs.

    Built by ``brokerage.evaluator`` from the fills and the mark-to-market, so
    the same numbers drive the kill decision and the scorecard.
    """

    trades: int = 0
    drawdown_pct: Decimal = ZERO
    win_rate_pct: Optional[Decimal] = None
    sharpe: Optional[Decimal] = None
    worst_trade_pct: Decimal = ZERO
    consecutive_losses: int = 0
    # Open positions the feed could not price when these numbers were taken.
    # Not a detail: every figure above is computed from marks, so a non-empty
    # tuple here means the rest of this object is fiction. See `should_kill_firm`.
    unpriceable: tuple = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "drawdown_pct", D(self.drawdown_pct))
        object.__setattr__(self, "worst_trade_pct", D(self.worst_trade_pct))
        object.__setattr__(self, "unpriceable", tuple(self.unpriceable or ()))

    @property
    def can_be_valued(self) -> bool:
        return not self.unpriceable


def meets_sample_gate(metrics: FirmMetrics, config: Optional[FirmKillConfig] = None) -> bool:
    cfg = config or FirmKillConfig()
    return metrics.trades >= cfg.minimum_trades


def should_kill_firm(
    metrics: FirmMetrics, config: Optional[FirmKillConfig] = None
) -> Tuple[bool, Optional[str]]:
    """Returns (should_kill, reason), in the build document's stated order.

    Drawdown and single-trade loss are checked *before* the sample gate. Those
    two are not statistical judgements about a strategy's edge — they are the
    account being emptied — and waiting for twenty trades to notice a 40%
    drawdown would be the system failing to stop the bleeding.

    **Before any of that: can this book be priced at all?** Every number below
    is computed from marks, and ``MarketData.mark`` returns zero for a symbol
    with no bars. So a feed that goes dark does not report an error here — it
    reports that every position is worth nothing, which is indistinguishable
    from the account being emptied and is checked first, precisely because
    emptying the account is the thing we never wait to act on.

    That is not theoretical. A live village on an Alpaca feed that stopped
    returning bars marked every position to zero, showed every firm a total
    drawdown, and killed all eleven of them in one pass — a whole village
    destroyed by a data outage, with `killed` being terminal by design.

    "The system may always stop the bleeding" is not a licence to amputate on
    a broken thermometer. A kill decided on numbers the system knows are
    missing is not stopping bleeding; it is inventing it. So an unpriceable
    book returns the same answer the sample gate returns — *insufficient
    data*, never *kill* — and the tick reports the blindness loudly instead.
    """
    cfg = config or FirmKillConfig()

    if not metrics.can_be_valued:
        return False, (
            f"{CANNOT_VALUE}: {', '.join(metrics.unpriceable[:4])}"
            + (" and others" if len(metrics.unpriceable) > 4 else "")
        )

    if metrics.drawdown_pct > cfg.max_drawdown_pct:
        return True, f"Drawdown {metrics.drawdown_pct}% exceeds {cfg.max_drawdown_pct}%"
    if metrics.worst_trade_pct > cfg.max_single_loss_pct:
        return True, (
            f"Single trade lost {metrics.worst_trade_pct}% of equity "
            f"(limit {cfg.max_single_loss_pct}%)"
        )
    if metrics.consecutive_losses > cfg.max_consecutive_losses:
        return True, (
            f"{metrics.consecutive_losses} consecutive losing trades "
            f"(limit {cfg.max_consecutive_losses})"
        )

    if not meets_sample_gate(metrics, cfg):
        return False, INSUFFICIENT_DATA

    if metrics.win_rate_pct is not None and metrics.win_rate_pct < cfg.min_win_rate_pct:
        return True, f"Win rate {metrics.win_rate_pct}% below {cfg.min_win_rate_pct}%"
    if metrics.sharpe is not None and metrics.sharpe < cfg.min_sharpe:
        return True, f"Sharpe {metrics.sharpe} below {cfg.min_sharpe}"
    return False, None


def kill_check_table(
    metrics: FirmMetrics, config: Optional[FirmKillConfig] = None
) -> list[dict]:
    """Every condition with its actual value — the audit view of a decision.

    Rendered by `trade show`, so a human approving a kill sees which conditions
    fired and which did not, not just the first one that tripped.
    """
    cfg = config or FirmKillConfig()
    checks = [
        (
            "Drawdown",
            metrics.drawdown_pct,
            f"> {cfg.max_drawdown_pct}%",
            metrics.drawdown_pct > cfg.max_drawdown_pct,
        ),
        (
            "Worst single trade",
            metrics.worst_trade_pct,
            f"> {cfg.max_single_loss_pct}%",
            metrics.worst_trade_pct > cfg.max_single_loss_pct,
        ),
        (
            "Consecutive losses",
            metrics.consecutive_losses,
            f"> {cfg.max_consecutive_losses}",
            metrics.consecutive_losses > cfg.max_consecutive_losses,
        ),
        (
            "Win rate",
            metrics.win_rate_pct,
            f"< {cfg.min_win_rate_pct}%",
            metrics.win_rate_pct is not None
            and meets_sample_gate(metrics, cfg)
            and metrics.win_rate_pct < cfg.min_win_rate_pct,
        ),
        (
            "Sharpe",
            metrics.sharpe,
            f"< {cfg.min_sharpe}",
            metrics.sharpe is not None
            and meets_sample_gate(metrics, cfg)
            and metrics.sharpe < cfg.min_sharpe,
        ),
        (
            "Trades (sample gate)",
            metrics.trades,
            f"< {cfg.minimum_trades} = no verdict",
            False,
        ),
    ]
    return [
        {
            "condition": name,
            "value": "—" if value is None else value,
            "kill_when": rule,
            "triggered": bool(fired),
        }
        for name, value, rule, fired in checks
    ]


class KillSwitch:
    """The build document's ``KillSwitch``, with the village's semantics.

    ``trigger`` records and reports; it does not kill. Killing a firm is an
    approved action taken by the brokerage, which is why this class holds no
    database handle: nothing it does is irreversible.
    """

    def __init__(self, config: Optional[FirmKillConfig] = None):
        self.config = config or FirmKillConfig()
        self.triggered: list[dict] = []

    @property
    def conditions(self) -> list[str]:
        cfg = self.config
        return [
            f"drawdown > {cfg.max_drawdown_pct}%",
            f"win_rate < {cfg.min_win_rate_pct}% over {cfg.minimum_trades} trades",
            f"sharpe_ratio < {cfg.min_sharpe}",
            f"max_loss > {cfg.max_single_loss_pct}% in one trade",
            f"consecutive_losses > {cfg.max_consecutive_losses}",
        ]

    def evaluate(self, metrics: FirmMetrics) -> Tuple[bool, Optional[str]]:
        return should_kill_firm(metrics, self.config)

    def trigger(self, firm_key: str, reason: str) -> dict:
        record = {
            "status": "triggered",
            "firm": firm_key,
            "reason": reason,
            "action": "trading paused; kill requires human approval",
        }
        self.triggered.append(record)
        return record


__all__ = [
    "CANNOT_VALUE",
    "INSUFFICIENT_DATA",
    "FirmMetrics",
    "KillSwitch",
    "kill_check_table",
    "meets_sample_gate",
    "should_kill_firm",
]
