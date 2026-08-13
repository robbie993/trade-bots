"""Deciding whether a firm has earned real money.

Everywhere else in this repository the hard question is *when to stop*. This is
the only place that asks when to start, which makes it the one piece of code
where the house rule points the other way:

    The system may always stop the bleeding, and may never start it.

So nothing here promotes anything. It produces **evidence**, in a table, and a
human reads it and decides. The checks are deterministic and every one is
reported — pass or fail — for the same reason ``kill_check_table`` reports all
of them: a decision you can only see the first failing reason for is a decision
you cannot audit, and this is the decision that spends money.

**Why per firm, and not one switch.** The village's entire job is measuring
firms separately. Sending all nine live together discards that measurement and
teaches you only whether nine averaged strategies beat zero. If one of them is
good, the other eight are how you find out after they have paid for it.

**Why the criteria are what they are.**

``min_closed_trades``
    Well above the paper sample gate. Twenty trades settles "is this obviously
    broken"; it does not settle "is this edge real".

``min_bars_on_feed``
    The one that matters most, and the one a track record cannot fake. A
    strategy measured against a seeded random walk has demonstrated that it can
    trade a random walk — no gaps, no halts, no earnings, no correlation, no
    liquidity. Migration 020 records which feed each scorecard was written
    under, so this is read rather than assumed.

``min_expectancy_t``
    Not "did it make money" — *is the mean trade distinguishable from zero*. A
    t-statistic over the closed trades, so a firm that made a little money over
    forty coin-flips fails and a firm that made a little money reliably passes.
    This is the check that most often refuses, and it is the one doing the most
    work.

``max_drawdown_pct``
    Deliberately tighter than the kill switch's. The level at which you would
    shut a firm down is not the level at which you would hand it money.

``max_start_capital``
    The first live run is not for profit. It is for discovering what paper
    cannot show you: real slippage, partial fills, rejects, halts, an order
    that sits unfilled for an hour. Those are a category of bug the paper venue
    is structurally incapable of producing, and finding them with a few hundred
    dollars is enormously cheaper than finding them with a mandate.

**What this module will never do.** Grant anything. Change a venue. Move
capital. It reads and it reports. Promotion goes through the gate like every
other increase in risk, and the council may not rule on it — it has no panel
for live trading, by construction, and this does not give it one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..money import D, ZERO, money, percent

# Feeds that are not evidence about the world, whatever the numbers say.
INVENTED_FEEDS = ("synthetic", "blind")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_decimal(name: str, default: str) -> Decimal:
    try:
        return D(os.environ.get(name, default))
    except Exception:  # noqa: BLE001
        return D(default)


@dataclass(frozen=True)
class LiveReadiness:
    """What a firm has to show before a human is even asked.

    Every one is env-overridable, and every one is a judgement call rather than
    a law. These are the defaults I would start from; they are meant to be
    argued with, and the table prints the threshold next to the value so an
    argument is possible.
    """

    min_closed_trades: int = field(
        default_factory=lambda: _env_int("TRADE_LIVE_MIN_TRADES", 50))
    min_bars_on_feed: int = field(
        default_factory=lambda: _env_int("TRADE_LIVE_MIN_BARS", 30))
    min_expectancy_t: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_LIVE_MIN_T", "2.0"))
    max_drawdown_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_LIVE_MAX_DRAWDOWN_PCT", "10.0"))
    min_win_rate_pct: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_LIVE_MIN_WIN_RATE_PCT", "40.0"))
    max_start_capital: Decimal = field(
        default_factory=lambda: _env_decimal("TRADE_LIVE_START_CAPITAL", "500"))


# =========================================================================
# the arithmetic
# =========================================================================
def expectancy_t(trades) -> Optional[Decimal]:
    """How many standard errors the mean trade sits above zero.

    Returns None below three trades, where the statistic is noise wearing a
    number's clothes. Above ~2.0 the confidence interval on the mean excludes
    zero at roughly 95%, which is the difference between "this made money" and
    "this makes money".

    Computed in Decimal end to end, like every other figure that informs a
    decision about capital.
    """
    values = [D(t) for t in trades]
    n = len(values)
    if n < 3:
        return None
    mean = sum(values, ZERO) / D(n)
    variance = sum(((v - mean) ** 2 for v in values), ZERO) / D(n - 1)
    if variance <= 0:
        # Every trade identical. Real for a fixed-payoff strategy and otherwise
        # a sign of synthetic data; either way the sign of the mean decides.
        return D("99") if mean > 0 else (D("-99") if mean < 0 else ZERO)
    std_error = variance.sqrt() / D(n).sqrt()
    if std_error <= 0:
        return None
    return percent(mean / std_error)


def closed_trades(store, firm_id: int) -> list:
    """Realised P&L per closed trade, oldest first."""
    return [D(f.realized_pnl) for f in store.fills(firm_id) if D(f.realized_pnl) != 0]


def bars_on(store, firm_id: int, feed_name: str) -> int:
    """Scorecards written for this firm while that feed was in use."""
    try:
        row = store.db.query_one(
            "SELECT bars FROM firm_feeds WHERE firm_id = ? AND priced_by = ?",
            (firm_id, str(feed_name)),
        )
    except Exception:  # noqa: BLE001 - an un-migrated database has no history
        return 0
    return int((row or {}).get("bars") or 0)


def record_bar(store, firm_id: int, feed_name: str) -> None:
    """Count one scorecard against the feed that priced it. See migration 020."""
    if not firm_id or not feed_name:
        return
    try:
        existing = store.db.query_one(
            "SELECT bars FROM firm_feeds WHERE firm_id = ? AND priced_by = ?",
            (firm_id, str(feed_name)),
        )
        if existing is None:
            store.db.insert(
                "firm_feeds",
                {"firm_id": firm_id, "priced_by": str(feed_name), "bars": 1},
            )
        else:
            store.db.execute(
                "UPDATE firm_feeds SET bars = bars + 1 WHERE firm_id = ? AND priced_by = ?",
                (firm_id, str(feed_name)),
            )
    except Exception:  # noqa: BLE001 - never break a tick to keep a statistic
        pass


# =========================================================================
# the verdict
# =========================================================================
@dataclass
class Check:
    name: str
    value: object
    needs: str
    passed: bool
    why: str = ""


@dataclass
class Readiness:
    firm_key: str
    checks: list = field(default_factory=list)
    start_capital: Decimal = ZERO

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failures(self) -> list:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        if self.ready:
            return (f"{self.firm_key}: every criterion met — a human may now be "
                    f"asked, for up to {money(self.start_capital)}")
        first = self.failures[0]
        return (f"{self.firm_key}: not ready ({len(self.failures)} of "
                f"{len(self.checks)} unmet, first: {first.name} {first.why})")


def assess(store, firm, card, feed_name: str,
           config: Optional[LiveReadiness] = None,
           reconciled: bool = True) -> Readiness:
    """Every criterion, with its actual value. Grants nothing.

    `card` is the firm's current scorecard and `feed_name` the feed it would
    trade on — which is deliberately the same feed the evidence must have been
    gathered on, since that is the whole question.
    """
    cfg = config or LiveReadiness()
    out = Readiness(firm_key=firm.firm_key)

    # 0. Is this even a firm that could go live?
    out.checks.append(Check(
        "Status", firm.status, "active",
        str(firm.status) == "active",
        f"is {firm.status}",
    ))

    # 1. Can we believe the numbers at all? Every check below is computed from
    #    them, so this one gates the meaning of the rest.
    measurable = card is None or card.can_be_valued
    out.checks.append(Check(
        "Book is measurable", "yes" if measurable else "no", "yes",
        bool(measurable),
        "holds something that cannot be priced, or was priced on another feed",
    ))
    out.checks.append(Check(
        "Books reconcile", "yes" if reconciled else "NO", "yes",
        bool(reconciled),
        "the village does not reconcile",
    ))

    # 2. Real prices, and enough of them. The check a record cannot fake.
    invented = any(bad in str(feed_name).lower() for bad in INVENTED_FEEDS)
    out.checks.append(Check(
        "Feed is real", feed_name, "not synthetic",
        not invented,
        f"{feed_name} is invented data; a record against it is not evidence",
    ))
    bars = bars_on(store, firm.id, feed_name)
    out.checks.append(Check(
        f"Bars on {feed_name}", bars, f">= {cfg.min_bars_on_feed}",
        bars >= cfg.min_bars_on_feed,
        f"only {bars} measured on the feed it would trade",
    ))

    # 3. Enough trades to say anything, and an edge distinguishable from luck.
    trades = closed_trades(store, firm.id)
    out.checks.append(Check(
        "Closed trades", len(trades), f">= {cfg.min_closed_trades}",
        len(trades) >= cfg.min_closed_trades,
        f"only {len(trades)} closed",
    ))
    t_stat = expectancy_t(trades)
    out.checks.append(Check(
        "Expectancy (t)", "—" if t_stat is None else t_stat,
        f">= {cfg.min_expectancy_t}",
        t_stat is not None and t_stat >= cfg.min_expectancy_t,
        "the mean trade is not distinguishable from zero",
    ))

    # 4. And it did not have to take the house apart to get there.
    drawdown = D(card.drawdown_pct) if card else ZERO
    out.checks.append(Check(
        "Drawdown", drawdown, f"<= {cfg.max_drawdown_pct}%",
        drawdown <= cfg.max_drawdown_pct,
        f"{drawdown}% is past the promotion limit (tighter than the kill limit)",
    ))
    win_rate = D(card.win_rate_pct) if (card and card.win_rate_pct is not None) else None
    out.checks.append(Check(
        "Win rate", "—" if win_rate is None else win_rate,
        f">= {cfg.min_win_rate_pct}%",
        win_rate is not None and win_rate >= cfg.min_win_rate_pct,
        "below the promotion floor",
    ))

    # The first live mandate is small on purpose, and never larger than what
    # the firm is already running on paper.
    out.start_capital = money(min(D(cfg.max_start_capital), D(firm.allocation))) \
        if D(firm.allocation) > 0 else money(cfg.max_start_capital)
    return out


def table(readiness: Readiness) -> list:
    """The audit view — every criterion, in order, pass or fail."""
    return [
        {
            "criterion": c.name,
            "value": c.value,
            "needs": c.needs,
            "met": "yes" if c.passed else "NO",
        }
        for c in readiness.checks
    ]


__all__ = [
    "Check", "INVENTED_FEEDS", "LiveReadiness", "Readiness", "assess",
    "bars_on", "closed_trades", "expectancy_t", "record_bar", "table",
]
