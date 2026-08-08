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
from ..models import FirmRecord, Position, RiskVerdict, Side, qty


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
        if held > 0 and side is Side.SELL:
            allowed = min(quantity, held)
            if allowed < quantity:
                return RiskDecision(
                    RiskVerdict.RESIZE.value,
                    f"cut to the {allowed} held (this system does not sell short)",
                    allowed,
                )
            return RiskDecision(RiskVerdict.ALLOW.value, "reducing an open position", allowed)

        # -- opening or increasing: every limit applies --------------------
        if side is Side.SELL:
            # No short selling. A firm may only sell what it owns, so a bear
            # verdict on a name it does not hold is simply "do nothing".
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

        notional = money(quantity * reference_price)
        reasons: list[str] = []

        # 1. Risk limit: fraction of allocation exposed to one new position.
        risk_cap = money(D(firm.risk_limit) * firm.allocation)
        if risk_cap <= 0:
            return RiskDecision(RiskVerdict.BLOCK.value, "firm has no allocation", ZERO)
        if notional > risk_cap:
            quantity = qty(risk_cap / reference_price)
            reasons.append(f"risk limit {percent(D(firm.risk_limit) * 100)}% of allocation")

        # 2. Position cap: total holding in one name against equity.
        existing_value = money(held * reference_price)
        position_cap = money(self.limits.max_position_pct * D(equity))
        room = position_cap - existing_value
        if room <= 0:
            return RiskDecision(
                RiskVerdict.BLOCK.value,
                f"{symbol} is already at the "
                f"{percent(self.limits.max_position_pct * 100)}% position cap",
                ZERO,
            )
        if money(quantity * reference_price) > room:
            quantity = qty(room / reference_price)
            reasons.append(f"position cap {percent(self.limits.max_position_pct * 100)}% of equity")

        # 3. Cash floor: never spend the firm's last reserve.
        floor = money(self.limits.cash_floor_pct * firm.allocation)
        spendable = money(firm.cash - floor)
        if spendable <= 0:
            return RiskDecision(
                RiskVerdict.BLOCK.value,
                f"cash {money(firm.cash)} is at or below the reserve floor {floor}",
                ZERO,
            )
        if money(quantity * reference_price) > spendable:
            quantity = qty(spendable / reference_price)
            reasons.append("cash floor")

        quantity = qty(quantity)
        if quantity <= 0 or money(quantity * reference_price) <= 0:
            return RiskDecision(
                RiskVerdict.BLOCK.value,
                "size fell to zero after limits: " + ("; ".join(reasons) or "no room"),
                ZERO,
            )
        if reasons:
            return RiskDecision(RiskVerdict.RESIZE.value, "; ".join(reasons), quantity)
        return RiskDecision(RiskVerdict.ALLOW.value, "within all limits", quantity)


__all__ = ["RiskDecision", "RiskManager"]
