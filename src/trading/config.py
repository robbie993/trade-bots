"""Configuration for the trading ecosystem.

Same rule as ``src/config.py``: every tunable that can cost money lives in one
file so the whole risk surface is readable in a sitting, everything is
overridable by environment variable, and nothing that spends money has a
permissive default.

Two defaults in here are load-bearing and should be changed only on purpose:

``venue = "paper"``
    The only venue that executes without a human. Every other venue name
    routes through ``execution.live``, which refuses to send an order unless
    an approval row says otherwise.

``allocation_increase_needs_approval = True``
    The brokerage may cut capital by itself and may never add it by itself.
    That asymmetry is the whole safety story of the layer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from decimal import Decimal
from pathlib import Path

from ..config import REPO_ROOT, _env_bool, _env_decimal, _env_int
from ..money import D


if TYPE_CHECKING:  # the real import lives in living.py, which imports this module
    from .living import LivingConfig


@dataclass(frozen=True)
class FirmDefaults:
    """What a firm gets when its config file does not say otherwise."""

    allocation: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_FIRM_ALLOCATION", "100000.00")
    )
    # Fraction of allocation at risk in a single position.
    risk_limit: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_RISK_LIMIT", "0.02")
    )
    # Hard cap on one position's notional as a fraction of equity, applied
    # after the risk limit sizes the trade.
    max_position_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_MAX_POSITION_PCT", "0.25")
    )
    max_positions: int = field(default_factory=lambda: _env_int("TRADE_MAX_POSITIONS", 8))
    # Cash the firm may never dip below, as a fraction of its allocation.
    cash_floor_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_CASH_FLOOR_PCT", "0.05")
    )
    # Confidence below which the trader does nothing. The debate has to be won
    # convincingly, not narrowly.
    min_confidence: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_MIN_CONFIDENCE", "55.0")
    )
    venue: str = field(default_factory=lambda: os.environ.get("TRADE_VENUE", "paper"))


@dataclass(frozen=True)
class FirmKillConfig:
    """Per-firm kill triggers — the trading edition of ``kill_criteria``.

    Sample gates first: below them the answer is "insufficient data", never
    "kill". A firm is not a failure because its first two trades lost.
    """

    minimum_trades: int = field(default_factory=lambda: _env_int("TRADE_MIN_TRADES", 20))
    max_drawdown_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_MAX_DRAWDOWN_PCT", "20.0")
    )
    min_win_rate_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_MIN_WIN_RATE_PCT", "30.0")
    )
    min_sharpe: Decimal = field(default_factory=lambda: _env_decimal("TRADE_MIN_SHARPE", "0.5"))
    max_single_loss_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_MAX_SINGLE_LOSS_PCT", "10.0")
    )
    max_consecutive_losses: int = field(
        default_factory=lambda: _env_int("TRADE_MAX_CONSECUTIVE_LOSSES", 5)
    )


@dataclass(frozen=True)
class BrokerageConfig:
    """Section 3 of the build document — oversight and allocation."""

    # Score bands. Above `good` gets more capital, below `poor` gets less,
    # below `kill` stops trading entirely.
    good_score: Decimal = field(default_factory=lambda: _env_decimal("TRADE_GOOD_SCORE", "60.0"))
    poor_score: Decimal = field(default_factory=lambda: _env_decimal("TRADE_POOR_SCORE", "40.0"))
    kill_score: Decimal = field(default_factory=lambda: _env_decimal("TRADE_KILL_SCORE", "20.0"))

    increase_step: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_ALLOCATION_INCREASE", "1.10")
    )
    decrease_step: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_ALLOCATION_DECREASE", "0.90")
    )
    max_allocation_multiple: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_MAX_ALLOCATION_MULTIPLE", "3.0")
    )
    min_allocation: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_MIN_ALLOCATION", "1000.00")
    )
    # Raising an allocation is starting the bleeding. It needs a human.
    allocation_increase_needs_approval: bool = field(
        default_factory=lambda: _env_bool("TRADE_APPROVE_INCREASES", True)
    )
    # Total capital the brokerage is allowed to have deployed across all firms.
    #
    # Deliberately larger than the sum of the firms in config/firm_config.yaml.
    # When the cap equals what is already deployed there is no headroom, and a
    # cap with no headroom silently disables two things that are supposed to
    # work: the allocator can never raise a winner, and no recruited bot can
    # ever be funded. The gap is the room the village has to grow into.
    total_capital: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_TOTAL_CAPITAL", "1000000.00")
    )
    # Reconciliation tolerance. Not zero because prices carry 8 decimals and
    # cash carries 2; one cent of quantisation per fill is expected.
    reconcile_tolerance: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_RECONCILE_TOLERANCE", "0.05")
    )
    # KILL_ALL: every firm dead, or the whole ecosystem underwater by this much.
    ecosystem_max_drawdown_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_ECOSYSTEM_MAX_DRAWDOWN_PCT", "25.0")
    )


@dataclass(frozen=True)
class AutonomyConfig:
    """How much the village decides for itself.

    ``human``   — every gated decision waits for you. The original behaviour,
                  and still what you get if you set nothing.
    ``council`` — the council rules on each pending decision from the ledger.
                  It grants, refuses, or *defers*: a decision the evidence does
                  not settle still waits for a human, so autonomy narrows what
                  you are asked about rather than removing you.

    ``live_trading_needs_human`` is not a normal setting. Sending an order to a
    real broker is the one act that leaves this system — irreversible, outside
    the ledger, real money. The council has no panel for it, so turning this
    off does not make it autonomous; it exists so the answer to "can it trade
    my real money on its own?" is a line of code you can read rather than a
    promise.
    """

    mode: str = field(default_factory=lambda: os.environ.get("TRADE_AUTONOMY", "human"))
    # How near a run thing has to be before the council declines to call it.
    margin: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_COUNCIL_MARGIN", "20")
    )
    # Ticks between rulings on the same firm and action. The council may reach
    # the same conclusion twice; it may not compound its way there in a minute.
    cooldown_ticks: int = field(
        default_factory=lambda: _env_int("TRADE_COUNCIL_COOLDOWN", 3)
    )
    live_trading_needs_human: bool = True

    @property
    def council_decides(self) -> bool:
        return self.mode.strip().lower() == "council"


@dataclass(frozen=True)
class BrainConfig:
    """Section 4.1/4.2 — evolution and memory."""

    population: int = field(default_factory=lambda: _env_int("TRADE_EVO_POPULATION", 8))
    mutation_rate: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_EVO_MUTATION_RATE", "0.25")
    )
    mutation_scale: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_EVO_MUTATION_SCALE", "0.20")
    )
    survivors: int = field(default_factory=lambda: _env_int("TRADE_EVO_SURVIVORS", 3))
    seed: int = field(default_factory=lambda: _env_int("TRADE_EVO_SEED", 20260808))
    # An evolved genome is a proposal, not a deployment: it is written to
    # strategy_genomes and only promoted when this is true and it beat the
    # incumbent on the same data.
    promote_winners: bool = field(default_factory=lambda: _env_bool("TRADE_EVO_PROMOTE", True))
    memory_recall_limit: int = field(default_factory=lambda: _env_int("TRADE_MEMORY_RECALL", 20))


@dataclass(frozen=True)
class HeartConfig:
    """Section 4.3 — the conscience.

    ``block_on_warn`` is off by default: a warn is recorded on the proposal
    and the trade proceeds. Turn it on and every flagged trade dies.
    """

    block_on_warn: bool = field(default_factory=lambda: _env_bool("TRADE_ETHICS_STRICT", False))
    # Concentration above this fraction of a firm's equity in one name reads
    # as a fairness/care problem, not just a risk one.
    concentration_warn_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_ETHICS_CONCENTRATION_PCT", "40.0")
    )
    # Symbols the operator has declared off-limits, for any reason.
    restricted_symbols: tuple = field(
        default_factory=lambda: tuple(
            s.strip().upper()
            for s in os.environ.get("TRADE_RESTRICTED_SYMBOLS", "").split(",")
            if s.strip()
        )
    )
    # Whole categories the operator will not hold. Enforced only for symbols
    # the operator has themselves classified in `restrictions_file` — this
    # system ships no opinion about what any company does.
    restricted_categories: tuple = field(
        default_factory=lambda: tuple(
            c.strip().lower()
            for c in os.environ.get("TRADE_RESTRICTED_CATEGORIES", "").split(",")
            if c.strip()
        )
    )
    restrictions_file: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "TRADE_RESTRICTIONS_FILE", str(REPO_ROOT / "config" / "restricted_assets.yaml")
            )
        )
    )
    # Round-trips in the same name inside one session that start to look like
    # churn rather than trading.
    max_round_trips_per_session: int = field(
        default_factory=lambda: _env_int("TRADE_MAX_ROUND_TRIPS", 4)
    )


@dataclass(frozen=True)
class GatewayConfig:
    """Section 4.4 — OmniRoute, or nothing at all.

    ``enabled`` defaults to False. Every agent in this package produces its
    reading from the price series deterministically; the gateway only ever
    adds narration. A trading system that cannot decide without a network call
    is a trading system that stops deciding when the network does.
    """

    enabled: bool = field(default_factory=lambda: _env_bool("TRADE_GATEWAY_ENABLED", False))
    base_url: str = field(
        default_factory=lambda: os.environ.get("TRADE_GATEWAY_URL", "http://localhost:20128/v1")
    )
    api_key: str = field(default_factory=lambda: os.environ.get("TRADE_GATEWAY_KEY", ""))
    model: str = field(
        default_factory=lambda: os.environ.get("TRADE_GATEWAY_MODEL", "claude-sonnet-5")
    )
    timeout_s: int = field(default_factory=lambda: _env_int("TRADE_GATEWAY_TIMEOUT_S", 30))
    max_tokens: int = field(default_factory=lambda: _env_int("TRADE_GATEWAY_MAX_TOKENS", 600))


@dataclass(frozen=True)
class DataConfig:
    """Section 6 — market data."""

    source: str = field(default_factory=lambda: os.environ.get("TRADE_DATA_SOURCE", "synthetic"))
    csv_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TRADE_DATA_DIR", str(REPO_ROOT / "data" / "market"))
        )
    )
    seed: int = field(default_factory=lambda: _env_int("TRADE_DATA_SEED", 20260808))
    history_days: int = field(default_factory=lambda: _env_int("TRADE_HISTORY_DAYS", 180))
    # Paper fills cross the spread and pay a fee, because a backtest that
    # fills at mid for free is a backtest that always wins.
    slippage_bps: Decimal = field(default_factory=lambda: _env_decimal("TRADE_SLIPPAGE_BPS", "5"))
    fee_bps: Decimal = field(default_factory=lambda: _env_decimal("TRADE_FEE_BPS", "2"))


@dataclass(frozen=True)
class TradingConfig:
    firms_config: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TRADE_FIRMS_CONFIG", str(REPO_ROOT / "config" / "firm_config.yaml"))
        )
    )
    audit_vault: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TRADE_AUDIT_VAULT", str(REPO_ROOT / "vault"))
        )
    )
    vendor_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TRADE_VENDOR_DIR", str(REPO_ROOT / "vendor"))
        )
    )
    tick_interval_s: int = field(default_factory=lambda: _env_int("TRADE_TICK_INTERVAL_S", 3600))

    firm: FirmDefaults = field(default_factory=FirmDefaults)
    kill: FirmKillConfig = field(default_factory=FirmKillConfig)
    brokerage: BrokerageConfig = field(default_factory=BrokerageConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    living: "LivingConfig" = field(default_factory=lambda: _living_config())
    brain: BrainConfig = field(default_factory=BrainConfig)
    heart: HeartConfig = field(default_factory=HeartConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    data: DataConfig = field(default_factory=DataConfig)


def _living_config():
    """Imported late: living.py reads TradingConfig, so this cannot be a cycle."""
    from .living import LivingConfig

    return LivingConfig()


def load_trading_config() -> TradingConfig:
    """Read configuration from the environment (call per process, not per use)."""
    return TradingConfig()


# Kept here so both the allocator and the CLI agree on what a percentage means.
ZERO_PCT = D("0")
