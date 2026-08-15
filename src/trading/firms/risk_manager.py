"""The Risk Manager — the firm's own brake.

Three verdicts, and only one of them is a refusal:

``allow``   the trade is within every limit
``resize``  the intent is fine, the size is not; the quantity is cut
``block``   the trade may not happen at this size or any smaller one

The asymmetry that runs through the whole village applies here too. A trade
that *reduces* exposure — selling into an existing long, closing a position —
is checked for solvency and nothing else. Stopping the bleeding is always
allowed. Opening or increasing exposure has to clear every limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from ...money import D, ZERO, money, percent
from ..config import FirmDefaults
from ..models import CashView, FirmRecord, Position, RiskVerdict, Side, qty
from ..options import contract_size


@dataclass(frozen=True)
class RiskDecision:
    verdict: str
    reason: str
    quantity: Decimal

    @property
    def blocked(self) -> bool:
        return self.verdict == RiskVerdict.BLOCK.value


class RiskManager:
    def __init__(self, limits: Optional[FirmDefaults] = None):
        self.limits = limits or FirmDefaults()

    def review(
        self,
        firm: FirmRecord,
        symbol: str,
        side: Side,
        quantity: Decimal,
        reference_price: Decimal,
        positions: Sequence[Position],
        equity: Decimal,
    ) -> RiskDecision:
        quantity = qty(quantity)
        reference_price = D(reference_price)
        if quantity <= 0:
            return RiskDecision(RiskVerdict.BLOCK.value, "zero quantity", ZERO)
        if reference_price <= 0:
            return RiskDecision(RiskVerdict.BLOCK.value, "no price to trade against", ZERO)

        existing = next((p for p in positions if p.symbol == symbol), None)
        held = existing.quantity if existing else ZERO

        # -- reducing exposure: solvency check only ------------------------
        # Closing is always allowed and never sized down: the system may stop
        # the bleeding whatever the limits say. `held` is signed, so this
        # covers buying back a short as well as selling a long.
        reducing = (held > 0 and side is Side.SELL) or (held < 0 and side is Side.BUY)
        if reducing:
            allowed = min(quantity, abs(held))
            if allowed < quantity and not self.limits.allow_short:
                return RiskDecision(
                    RiskVerdict.RESIZE.value,
                    f"cut to the {allowed} held (this system does not sell short)",
                    allowed,
                )
            if allowed >= quantity:
                return RiskDecision(
                    RiskVerdict.ALLOW.value, "reducing an open position", allowed
                )
            # Shorting is on and this sell goes through the position and out
            # the other side. The part that closes is free; the part that opens
            # a short meets every limit below, like any other new risk.

        # -- opening or increasing: every limit applies --------------------
        # Writing an option comes first, and does not depend on `allow_short`,
        # because it is the one risk none of the limits below can bound. They
        # are all fractions of capital, and they work because the most a
        # position can lose is knowable in advance. For a written option it is
        # not: `options.max_loss` returns None for a short precisely so that no
        # caller can be handed a comforting number for an unbounded loss, and a
        # cap computed from the premium would size a naked call as though the
        # few hundred dollars collected were the exposure.
        #
        # So this blocks rather than guesses, and says which fact stopped it —
        # turning shorting on must not quietly turn option writing on with it.
        # Buying options is unaffected: the premium is the maximum loss, and
        # the caps below measure it correctly.
        if side is Side.SELL and contract_size(symbol) > 1 and held <= 0:
            return RiskDecision(
                RiskVerdict.BLOCK.value,
                f"{symbol} would be a written option, whose loss is unbounded — "
                "no capital limit here can size that, so this village does not "
                "write options it does not hold",
                ZERO,
            )

        if side is Side.SELL and not self.limits.allow_short:
            # A firm may only sell what it owns, so a bear verdict on a name it
            # does not hold is simply "do nothing".
            return RiskDecision(
                RiskVerdict.BLOCK.value, "no position to sell and shorting is disabled", ZERO
            )

        open_positions = [p for p in positions if p.is_open]
        if existing is None or not existing.is_open:
            if len(open_positions) >= self.limits.max_positions:
                return RiskDecision(
                    RiskVerdict.BLOCK.value,
                    f"already holding {len(open_positions)} positions "
                    f"(limit {self.limits.max_positions})",
                    ZERO,
                )

        # Contract size, so every cap below is denominated in the dollars that
        # actually leave the account. An option is quoted per share and traded
        # per contract: sizing a $3.20 quote as $3.20 of capital lets a firm
        # open a hundred times the position every limit here intends.
        size = D(contract_size(symbol))
        unit_price = money(reference_price * size)
        notional = money(quantity * unit_price)
        reasons: list[str] = []

        # 1. Risk limit: fraction of allocation exposed to one new position.
        risk_cap = money(D(firm.risk_limit) * firm.allocation)
        if risk_cap <= 0:
            return RiskDecision(RiskVerdict.BLOCK.value, "firm has no allocation", ZERO)
        if notional > risk_cap:
            quantity = qty(risk_cap / unit_price)
            reasons.append(f"risk limit {percent(D(firm.risk_limit) * 100)}% of allocation")

        # 2. Position cap: total holding in one name against equity.
        # `abs`, because `held` is signed. Without it a short position makes
        # its own cap bigger — existing_value goes negative, room grows, and
        # the more short you are the more you may short.
        existing_value = money(abs(held) * unit_price)
        position_cap = money(self.limits.max_position_pct * D(equity))
        room = position_cap - existing_value
        if room <= 0:
            return RiskDecision(
                RiskVerdict.BLOCK.value,
                f"{symbol} is already at the "
                f"{percent(self.limits.max_position_pct * 100)}% position cap",
                ZERO,
            )
        if money(quantity * unit_price) > room:
            quantity = qty(room / unit_price)
            reasons.append(f"position cap {percent(self.limits.max_position_pct * 100)}% of equity")

        # 2b. Gross exposure: longs plus the absolute value of shorts.
        # The cash floor below cannot restrain a short at all — selling adds
        # cash — so without this a firm with shorting on has no ceiling on
        # how much risk it can open. Net exposure would not do either: long
        # 100 and short 100 is flat on paper and can lose on both legs.
        if self.limits.allow_short:
            # At cost, not at mark: the risk manager is handed one symbol's
            # price and the other positions carry no mark, so pricing them all
            # at this symbol's price would be arithmetic dressed as a limit.
            gross = sum(
                (money(abs(p.quantity) * p.avg_price * D(p.multiplier))
                 for p in positions if p.is_open),
                ZERO,
            )
            gross_cap = money(self.limits.max_gross_exposure * firm.allocation)
            gross_room = gross_cap - gross
            if gross_room <= 0:
                return RiskDecision(
                    RiskVerdict.BLOCK.value,
                    f"gross exposure is at the "
                    f"{percent(self.limits.max_gross_exposure * 100)}% cap",
                    ZERO,
                )
            if money(quantity * unit_price) > gross_room:
                quantity = qty(gross_room / unit_price)
                reasons.append(
                    f"gross exposure cap "
                    f"{percent(self.limits.max_gross_exposure * 100)}% of allocation"
                )

        # 3. Cash floor: never spend the firm's last reserve. `available` is
        # the shared definition (models.CashView) — the allocator withdraws
        # against `withdrawable`, which deliberately differs.
        #
        # This applies to shorts too, which looks odd — selling adds cash — and
        # is deliberate. A short needs collateral, and requiring the firm to
        # hold the notional in cash is the margin rule, at 100% rather than the
        # 50% a broker would ask. That is the conservative direction, which is
        # the one this system errs in everywhere else.
        purse = CashView(
            cash=money(firm.cash),
            reserve=money(self.limits.cash_floor_pct * firm.allocation),
        )
        spendable = purse.available
        if purse.at_floor:
            return RiskDecision(
                RiskVerdict.BLOCK.value,
                f"cash {money(firm.cash)} is at or below the reserve floor {purse.reserve}",
                ZERO,
            )
        if money(quantity * unit_price) > spendable:
            quantity = qty(spendable / unit_price)
            reasons.append("cash floor")

        quantity = qty(quantity)
        if quantity <= 0 or money(quantity * unit_price) <= 0:
            return RiskDecision(
                RiskVerdict.BLOCK.value,
                "size fell to zero after limits: " + ("; ".join(reasons) or "no room"),
                ZERO,
            )
        if reasons:
            return RiskDecision(RiskVerdict.RESIZE.value, "; ".join(reasons), quantity)
        return RiskDecision(RiskVerdict.ALLOW.value, "within all limits", quantity)


__all__ = ["RiskDecision", "RiskManager"]
