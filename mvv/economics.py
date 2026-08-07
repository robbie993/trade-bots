"""Economic Calculator — spec component 2.

Pure math over Decimals. No AI, no I/O, no opinions: given a product's costs
it returns landed cost, a suggested price, and the margin an experiment would
start with. Anything it cannot know (real CAC, real refund rate) is an
explicit assumption from config, never a guess buried in a formula.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .config import CashConfig, DiscoveryConfig
from .money import D, ZERO, money, percent, ratio_pct


@dataclass(frozen=True)
class UnitEconomics:
    """One unit, all costs, no hand-waving."""

    unit_cost: Decimal  # supplier price
    shipping_cost: Decimal
    landed_cost: Decimal  # unit + shipping
    selling_price: Decimal
    processor_fee: Decimal
    assumed_ad_cost: Decimal  # assumed CAC for a not-yet-tested product
    gross_margin_abs: Decimal  # price - landed cost
    gross_margin_pct: Decimal
    net_margin_abs: Decimal  # after processor fee and assumed ad cost
    net_margin_pct: Decimal
    breakeven_cac: Decimal  # ad cost per order at which contribution hits zero

    @property
    def viable(self) -> bool:
        return self.net_margin_abs > 0


class EconomicCalculator:
    def __init__(
        self,
        cash: Optional[CashConfig] = None,
        discovery: Optional[DiscoveryConfig] = None,
    ):
        self.cash = cash or CashConfig()
        self.discovery = discovery or DiscoveryConfig()

    # -- pricing ----------------------------------------------------------
    def landed_cost(self, unit_cost, shipping_cost=ZERO) -> Decimal:
        return money(D(unit_cost) + D(shipping_cost))

    def processor_fee(self, selling_price) -> Decimal:
        """Stripe-style: percentage plus a fixed per-transaction charge."""
        price = D(selling_price)
        return money(price * self.cash.processor_fee_pct / D(100) + self.cash.processor_fee_fixed)

    def suggest_price(self, unit_cost, shipping_cost=ZERO) -> Decimal:
        """Markup on landed cost, then charm-priced to .99.

        A suggestion only — listing at any price needs human approval
        (spec section 6).
        """
        target = self.landed_cost(unit_cost, shipping_cost) * self.discovery.target_markup_multiple
        whole = int(target)
        candidate = D(whole) + D("0.99")
        if candidate < target:
            candidate += D(1)
        return money(candidate)

    # -- evaluation -------------------------------------------------------
    def unit_economics(
        self,
        unit_cost,
        selling_price,
        shipping_cost=ZERO,
        assumed_ad_cost: Optional[Decimal] = None,
    ) -> UnitEconomics:
        unit_cost = D(unit_cost)
        shipping_cost = D(shipping_cost)
        selling_price = D(selling_price)
        landed = self.landed_cost(unit_cost, shipping_cost)
        fee = self.processor_fee(selling_price)
        ad = D(self.discovery.assumed_cac if assumed_ad_cost is None else assumed_ad_cost)

        gross_abs = money(selling_price - landed)
        net_abs = money(selling_price - landed - fee - ad)
        return UnitEconomics(
            unit_cost=money(unit_cost),
            shipping_cost=money(shipping_cost),
            landed_cost=landed,
            selling_price=money(selling_price),
            processor_fee=fee,
            assumed_ad_cost=money(ad),
            gross_margin_abs=gross_abs,
            gross_margin_pct=ratio_pct(selling_price - landed, selling_price),
            net_margin_abs=net_abs,
            net_margin_pct=ratio_pct(selling_price - landed - fee - ad, selling_price),
            breakeven_cac=money(selling_price - landed - fee),
        )

    def calculate_margin(self, candidate) -> Decimal:
        """Estimated *gross* margin percentage for a scouted candidate.

        Matches the spec's `econ.calculate_margin(p)` in section 11.2. The
        orchestrator compares this against `discovery.min_margin_pct`.
        """
        price = D(getattr(candidate, "selling_price", None) or ZERO)
        if price <= 0:
            price = self.suggest_price(
                getattr(candidate, "unit_cost", ZERO), getattr(candidate, "shipping_cost", ZERO)
            )
        econ = self.unit_economics(
            getattr(candidate, "unit_cost", ZERO),
            price,
            getattr(candidate, "shipping_cost", ZERO),
        )
        return econ.gross_margin_pct

    def is_worth_testing(self, candidate) -> tuple[bool, str]:
        """Gate at discovery time — cheap filter before any money is at risk."""
        margin = self.calculate_margin(candidate)
        if margin < self.discovery.min_margin_pct:
            return False, f"Estimated margin {percent(margin)}% below minimum {self.discovery.min_margin_pct}%"
        price = D(getattr(candidate, "selling_price", None) or ZERO) or self.suggest_price(
            getattr(candidate, "unit_cost", ZERO), getattr(candidate, "shipping_cost", ZERO)
        )
        econ = self.unit_economics(
            getattr(candidate, "unit_cost", ZERO), price, getattr(candidate, "shipping_cost", ZERO)
        )
        if econ.breakeven_cac <= 0:
            return False, "Breakeven CAC is zero or negative — no room for any ad spend"
        return True, f"Estimated margin {percent(margin)}%, breakeven CAC ${econ.breakeven_cac}"

    # -- portfolio --------------------------------------------------------
    def store_pnl(self, experiments) -> dict:
        """Roll experiments up into a store-level P&L (spec section 9)."""
        experiments = list(experiments)
        revenue = sum((D(e.revenue) for e in experiments), ZERO)
        cogs = sum((D(e.unit_cost) * e.orders for e in experiments), ZERO)
        ad_spend = sum((D(e.ad_spend) for e in experiments), ZERO)
        orders = sum(e.orders for e in experiments)
        contribution = money(revenue - cogs - ad_spend)
        return {
            "revenue": money(revenue),
            "cogs": money(cogs),
            "ad_spend": money(ad_spend),
            "orders": orders,
            "contribution_margin": contribution,
            "contribution_margin_pct": ratio_pct(contribution, revenue),
            "experiments": len(experiments),
        }
