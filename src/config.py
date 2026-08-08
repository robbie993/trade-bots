"""Configuration for the Minimum Viable Village.

All tunables live here so an auditor can read the entire risk surface of the
system in one file. Everything is overridable by environment variable; nothing
that spends money has a permissive default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .money import D

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = REPO_ROOT / "data" / "mvv.db"


def _env_decimal(name: str, default: str) -> Decimal:
    return D(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CashConfig:
    """Spec section 7 — working capital."""

    initial_budget: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_INITIAL_BUDGET", "1000.00")
    )
    emergency_buffer: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_EMERGENCY_BUFFER", "150.00")
    )
    # Stripe/Shopify style rolling reserve on new accounts.
    processor_hold_pct: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_PROCESSOR_HOLD_PCT", "7.5")
    )
    processor_hold_days: int = field(
        default_factory=lambda: _env_int("MVV_PROCESSOR_HOLD_DAYS", 90)
    )
    settlement_days: int = field(
        default_factory=lambda: _env_int("MVV_SETTLEMENT_DAYS", 7)
    )
    processor_fee_pct: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_PROCESSOR_FEE_PCT", "2.9")
    )
    processor_fee_fixed: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_PROCESSOR_FEE_FIXED", "0.30")
    )


@dataclass(frozen=True)
class GateConfig:
    """Spec section 6 — hard boundaries on spend."""

    # Ad spend above this daily figure needs a *separate* approval even if the
    # experiment is already approved and running (spec section 6).
    scale_approval_daily_threshold: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_SCALE_APPROVAL_THRESHOLD", "50.00")
    )
    # Platforms the operator has approved. Adding one is a human decision.
    approved_platforms: tuple = field(
        default_factory=lambda: tuple(
            p.strip()
            for p in os.environ.get("MVV_APPROVED_PLATFORMS", "aliexpress,temu").split(",")
            if p.strip()
        )
    )
    # If true, `tick` blocks waiting for a decision (spec's skeleton).
    # Default false: a pending kill pauses ads and moves on, so one unanswered
    # notification cannot freeze the whole loop.
    block_on_approval: bool = field(
        default_factory=lambda: _env_bool("MVV_BLOCK_ON_APPROVAL", False)
    )
    approval_wait_timeout_s: int = field(
        default_factory=lambda: _env_int("MVV_APPROVAL_TIMEOUT_S", 3600)
    )


@dataclass(frozen=True)
class DiscoveryConfig:
    """Spec section 11.2 — minimum estimated margin to bother testing."""

    min_margin_pct: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_MIN_MARGIN_PCT", "5.0")
    )
    target_markup_multiple: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_TARGET_MARKUP", "3.0")
    )
    # Assumed ad cost per order when pricing a candidate we have no data for.
    assumed_cac: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_ASSUMED_CAC", "10.00")
    )
    max_new_experiments_per_tick: int = field(
        default_factory=lambda: _env_int("MVV_MAX_NEW_EXPERIMENTS_PER_TICK", 5)
    )


@dataclass(frozen=True)
class StopConfig:
    """Spec sections 1.3 and 14 — the system must be able to stop itself.

    Four independent KILL_ALL conditions. Any one of them ends Phase 1.
    """

    # 1. After N experiments, not one has a positive contribution margin.
    kill_all_after_experiments: int = field(
        default_factory=lambda: _env_int("MVV_KILL_ALL_AFTER_EXPERIMENTS", 20)
    )
    # 2. Cash flow negative for this many consecutive days.
    cash_negative_days: int = field(
        default_factory=lambda: _env_int("MVV_CASH_NEGATIVE_DAYS", 7)
    )
    # 3. A kill trigger fired and nobody answered within this many hours.
    #    An unanswered kill is the gate failing, not the operator being busy.
    kill_approval_timeout_hours: int = field(
        default_factory=lambda: _env_int("MVV_KILL_APPROVAL_TIMEOUT_HOURS", 72)
    )
    # 4. Ad costs exceeded revenue by this multiple over the trailing window.
    ad_revenue_ratio_limit: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_AD_REVENUE_RATIO_LIMIT", "2.0")
    )
    ad_revenue_window_days: int = field(
        default_factory=lambda: _env_int("MVV_AD_REVENUE_WINDOW_DAYS", 30)
    )
    scale_min_contribution_margin_pct: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_SCALE_MIN_CM_PCT", "20.0")
    )
    early_warning_orders: int = field(
        default_factory=lambda: _env_int("MVV_EARLY_WARNING_ORDERS", 20)
    )


@dataclass(frozen=True)
class CourtConfig:
    """The File Court — escalation thresholds and reviewer wiring.

    The bands come from the deployment spec (>70 GUILTY, >40 MIXED). They are
    Decimal for the same reason the money is: these numbers decide an outcome,
    and a verdict that flips on binary rounding is not auditable.
    """

    # Where the CodeTribunal clone lives. It is a Hugging Face *Space*:
    #   git clone https://huggingface.co/spaces/amine-yagoub/CodeTribunal
    codetribunal_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MVV_CODETRIBUNAL_PATH", str(REPO_ROOT / "CodeTribunal"))
        )
    )
    # Empty = autodetect (src/code_tribunal/app.py, else main.py). Set this
    # only to force a root-level script the detector would not pick.
    codetribunal_entrypoint: str = field(
        default_factory=lambda: os.environ.get("MVV_CODETRIBUNAL_ENTRYPOINT", "")
    )
    # Arguments after `--file <path>`. The Space's CLI may not accept
    # `--output json`; blank the variable if it rejects them.
    codetribunal_args: tuple = field(
        default_factory=lambda: tuple(
            a for a in os.environ.get(
                "MVV_CODETRIBUNAL_ARGS", "--output json"
            ).split() if a
        )
    )
    uploads_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MVV_UPLOADS_DIR", str(REPO_ROOT / "uploads"))
        )
    )
    processed_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MVV_PROCESSED_DIR", str(REPO_ROOT / "processed"))
        )
    )
    log_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MVV_COURT_LOG", str(REPO_ROOT / "logs" / "village.log"))
        )
    )
    watch_extensions: tuple = field(
        default_factory=lambda: tuple(
            e.strip() if e.strip().startswith(".") else f".{e.strip()}"
            for e in os.environ.get("MVV_COURT_EXTENSIONS", ".zip,.py,.js,.md").split(",")
            if e.strip()
        )
    )
    watch_interval_s: int = field(
        default_factory=lambda: _env_int("MVV_COURT_INTERVAL_S", 60)
    )
    backend_timeout_s: int = field(
        default_factory=lambda: _env_int("MVV_COURT_TIMEOUT_S", 600)
    )
    # Escalation: run the adversarial reviewer above this score, the full
    # panel above the next one.
    tribunal_above: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_COURT_TRIBUNAL_ABOVE", "30")
    )
    panel_above: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_COURT_PANEL_ABOVE", "50")
    )
    mixed_above: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_COURT_MIXED_ABOVE", "40")
    )
    guilty_above: Decimal = field(
        default_factory=lambda: _env_decimal("MVV_COURT_GUILTY_ABOVE", "70")
    )
    # Yama (npm install -g @juspay/yama) is off until the operator opts in.
    yama_enabled: bool = field(
        default_factory=lambda: _env_bool("MVV_COURT_YAMA", False)
    )


@dataclass(frozen=True)
class Config:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}"
        )
    )
    operator_email: str = field(
        default_factory=lambda: os.environ.get("MVV_OPERATOR_EMAIL", "")
    )
    slack_webhook_url: str = field(
        default_factory=lambda: os.environ.get("MVV_SLACK_WEBHOOK_URL", "")
    )
    notification_log: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MVV_NOTIFICATION_LOG", str(REPO_ROOT / "data" / "notifications.log"))
        )
    )
    smtp_host: str = field(default_factory=lambda: os.environ.get("MVV_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: _env_int("MVV_SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: os.environ.get("MVV_SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("MVV_SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: os.environ.get("MVV_SMTP_FROM", ""))

    tick_interval_s: int = field(default_factory=lambda: _env_int("MVV_TICK_INTERVAL_S", 3600))
    scout_source: str = field(
        default_factory=lambda: os.environ.get("MVV_SCOUT_SOURCE", "fixture")
    )
    scout_fixture_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MVV_SCOUT_FIXTURE", str(REPO_ROOT / "data" / "products.sample.json"))
        )
    )

    cash: CashConfig = field(default_factory=CashConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    stop: StopConfig = field(default_factory=StopConfig)
    court: CourtConfig = field(default_factory=CourtConfig)


def load_config() -> Config:
    """Read configuration from the environment (call per process, not per use)."""
    return Config()
