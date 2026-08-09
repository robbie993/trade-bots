"""The black market — firms trading with each other.

Two kinds of listing, and the difference between them is the whole safety
argument of this module:

**Token listings** (genome, data, compute) settle here. Tokens are points, so
nothing that happens on this side of the market can reach the capital.

**Capital listings** do *not* settle here. A capital sale is a cut on the
seller and a raise on the buyer, and a raise needs a human — so the market
requests one and stops. When the approval comes back, both legs are applied
together through the allocator, in one transaction, so:

* the reconciler's identity (cash = allocation + Σ fill.cash_delta) still
  holds on both firms, and
* the market cannot become a route around the one gate in the system that
  stops capital growing without a person signing for it.

Doing it the other way — moving cash between firms directly — is the single
change that would quietly disable the human gate for the entire ecosystem.

**Licensed genomes are forced to mutate.** A buyer receives a *variant* of what
they bought, not a copy. Otherwise firms converge, three pods run one strategy
under three names, their drawdowns arrive on the same day, and a leaderboard
that was measuring three strategies is measuring one. Diversity is what the
buyer pays with, on top of the tokens.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ...db.connection import Database, utcnow_iso
from ...money import D, ZERO, fmt_money, money
from ..brain.evolver import Evolver
from ..config import TradingConfig
from ..store import TradingStore
from ..competition.tokens import InsufficientTokens, TokenLedger

TOKEN_ASSETS = ("genome", "data", "compute")
CASH_ASSETS = ("capital",)
ASSET_TYPES = TOKEN_ASSETS + CASH_ASSETS


class MarketError(RuntimeError):
    """The trade cannot happen as asked."""


@dataclass
class Listing:
    id: Optional[int]
    seller: str
    asset_type: str
    title: str = ""
    details: dict = field(default_factory=dict)
    price: Decimal = ZERO
    currency: str = "tokens"
    status: str = "active"
    buyer: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> "Listing":
        try:
            details = json.loads(row.get("details") or "{}")
        except (TypeError, ValueError):
            details = {}
        return cls(
            id=row.get("id"),
            seller=row.get("seller") or "",
            asset_type=row.get("asset_type") or "",
            title=row.get("title") or "",
            details=details,
            price=D(row.get("price")),
            currency=row.get("currency") or "tokens",
            status=row.get("status") or "active",
            buyer=row.get("buyer"),
        )

    def __str__(self) -> str:
        unit = "tokens" if self.currency == "tokens" else ""
        price = fmt_money(self.price) if self.currency == "cash" else f"{self.price} {unit}"
        return f"#{self.id} {self.seller} sells {self.asset_type}: {self.title} — {price} [{self.status}]"


class BlackMarket:
    def __init__(
        self,
        store: TradingStore,
        config: Optional[TradingConfig] = None,
        tokens: Optional[TokenLedger] = None,
        gate=None,
    ):
        self.store = store
        self.db: Database = store.db
        self.config = config or TradingConfig()
        self.tokens = tokens or TokenLedger(self.db)
        self.gate = gate
        self.evolver = Evolver(store, self.config)

    # =====================================================================
    # listing
    # =====================================================================
    def list_asset(
        self,
        seller: str,
        asset_type: str,
        price,
        title: str = "",
        details: Optional[dict] = None,
    ) -> Listing:
        if asset_type not in ASSET_TYPES:
            raise MarketError(
                f"unknown asset type {asset_type!r}; expected {', '.join(ASSET_TYPES)}"
            )
        firm = self.store.get_firm(seller)
        if firm is None:
            raise MarketError(f"unknown firm {seller}")
        price = money(price)
        if price <= 0:
            raise MarketError("a listing needs a positive price")

        currency = "cash" if asset_type in CASH_ASSETS else "tokens"
        details = dict(details or {})

        if asset_type == "genome":
            details.setdefault("genome", firm.genome)
            details.setdefault("universe", list(firm.universe))
            if not details["genome"]:
                raise MarketError(f"{seller} has no genome to sell")
        if asset_type == "capital":
            # You can only sell capital you are not currently using.
            purse = self.store.cash_view(firm, cash_floor_pct=self.config.firm.cash_floor_pct)
            if price > purse.withdrawable:
                raise MarketError(
                    f"{seller} has {fmt_money(purse.withdrawable)} uninvested; "
                    f"cannot list {fmt_money(price)}"
                )

        listing_id = self.db.insert(
            "market_listings",
            {
                "seller": seller,
                "asset_type": asset_type,
                "title": title or f"{asset_type} from {seller}",
                "details": json.dumps(details, default=str),
                "price": price,
                "currency": currency,
                "status": "active",
                "listed_at": utcnow_iso(),
            },
        )
        return self.listing(listing_id)

    def listing(self, listing_id: int) -> Listing:
        row = self.db.query_one("SELECT * FROM market_listings WHERE id = ?", (listing_id,))
        if row is None:
            raise MarketError(f"no listing {listing_id}")
        return Listing.from_row(row)

    def listings(self, status: str = "active", limit: int = 50) -> list:
        rows = self.db.query(
            "SELECT * FROM market_listings WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        )
        return [Listing.from_row(r) for r in rows]

    def withdraw(self, listing_id: int, seller: str) -> Listing:
        listing = self.listing(listing_id)
        if listing.seller != seller:
            raise MarketError(f"{seller} does not own listing {listing_id}")
        self.db.update("market_listings", listing_id, {"status": "withdrawn"})
        return self.listing(listing_id)

    # =====================================================================
    # buying
    # =====================================================================
    def buy(self, buyer: str, listing_id: int) -> dict:
        listing = self.listing(listing_id)
        if listing.status != "active":
            raise MarketError(f"listing {listing_id} is {listing.status}")
        if listing.seller == buyer:
            raise MarketError("a firm cannot buy its own listing")
        if self.store.get_firm(buyer) is None:
            raise MarketError(f"unknown firm {buyer}")

        if listing.asset_type in CASH_ASSETS:
            return self._buy_capital(buyer, listing)
        return self._buy_with_tokens(buyer, listing)

    def _buy_with_tokens(self, buyer: str, listing: Listing) -> dict:
        """Escrow the tokens, deliver, release. All or nothing."""
        try:
            self.tokens.spend(
                buyer,
                listing.price,
                f"escrow for listing #{listing.id} ({listing.asset_type} from {listing.seller})",
                category="market",
                payload={"listing": listing.id},
            )
        except InsufficientTokens as exc:
            raise MarketError(str(exc)) from exc

        transaction_id = self.db.insert(
            "market_transactions",
            {
                "listing_id": listing.id,
                "buyer": buyer,
                "seller": listing.seller,
                "asset_type": listing.asset_type,
                "price": listing.price,
                "currency": "tokens",
                "escrow_state": "held",
                "delivered": False,
                "settled": False,
                "created_at": utcnow_iso(),
            },
        )

        try:
            delivery = self._deliver(buyer, listing)
        except Exception as exc:  # noqa: BLE001 - a failed delivery refunds, never keeps
            self.tokens.award(
                buyer, listing.price, f"refund for listing #{listing.id}", category="market"
            )
            self.db.update(
                "market_transactions",
                transaction_id,
                {"escrow_state": "refunded", "notes": str(exc)[:200]},
            )
            self.tokens.adjust_reputation(
                listing.seller, -15, f"failed to deliver listing #{listing.id}"
            )
            raise MarketError(f"delivery failed, tokens refunded: {exc}") from exc

        self.tokens.award(
            listing.seller,
            listing.price,
            f"sold {listing.asset_type} to {buyer} (listing #{listing.id})",
            category="market",
            payload={"listing": listing.id},
        )
        self.tokens.adjust_reputation(listing.seller, 5, f"delivered listing #{listing.id}")
        with self.db.transaction():
            self.db.update(
                "market_transactions",
                transaction_id,
                {
                    "escrow_state": "released",
                    "delivered": True,
                    "settled": True,
                    "delivery": json.dumps(delivery, default=str),
                },
            )
            self.db.update(
                "market_listings",
                listing.id,
                {"status": "sold", "buyer": buyer, "settled_at": utcnow_iso()},
            )
        return {
            "transaction": transaction_id,
            "listing": listing.id,
            "settled": True,
            "delivery": delivery,
        }

    def _deliver(self, buyer: str, listing: Listing) -> dict:
        if listing.asset_type == "genome":
            return self._deliver_genome(buyer, listing)
        # data and compute are agreements, recorded as such. Nothing in this
        # repository actually meters CPU or resells a price feed, and the
        # delivery record says exactly what was promised rather than
        # pretending a resource moved.
        return {
            "kind": listing.asset_type,
            "promised": listing.details,
            "note": (
                f"{listing.asset_type} listings record an agreement between firms; "
                "no resource is transferred by this system"
            ),
        }

    def _deliver_genome(self, buyer: str, listing: Listing) -> dict:
        """Hand over a *variant*, and write it as an unselected candidate.

        Two costs, both deliberate. The mutation stops the pods converging on
        one strategy with three names; the candidate status means a bought
        genome earns its place exactly like every other one — by beating the
        incumbent, or by a human deploying it.
        """
        source = listing.details.get("genome") or {}
        if not source:
            raise MarketError("the listing has no genome attached")
        rng = random.Random(f"{self.config.brain.seed}:{listing.id}:{buyer}")
        base = self.evolver.normalise(source)
        variant = self.evolver.mutate(base, rng)
        # `mutate` touches each gene with probability `mutation_rate`, so it can
        # legitimately return the genome unchanged. A licence that happens to
        # deliver an exact copy would quietly waive the diversity cost, so keep
        # rolling until at least one gene actually moves.
        attempts = 0
        while variant == base and attempts < 20:
            variant = self.evolver.mutate(base, rng)
            attempts += 1
        if variant == base:
            # Every roll came back identical — force one gene rather than ship
            # a copy. Deterministic given the seed, like everything else here.
            from ..brain.evolver import GENES

            gene = sorted(GENES)[rng.randrange(len(GENES))]
            low, high, is_int = GENES[gene]
            nudged = D(base[gene]) + (high - low) / D(10)
            if nudged > high:
                nudged = D(base[gene]) - (high - low) / D(10)
            value = max(low, min(high, nudged))
            variant = self.evolver.normalise(
                {**base, gene: int(value) if is_int else str(value.quantize(D("0.01")))}
            )
        firm = self.store.get_firm(buyer)
        genome_id = self.db.insert(
            "strategy_genomes",
            {
                "firm_id": firm.id if firm else None,
                "generation": 0,
                "genome": json.dumps(variant, sort_keys=True),
                "fitness": None,
                "trades": 0,
                "selected": False,
                "notes": f"licensed from {listing.seller} (listing #{listing.id}), mutated",
                "created_at": utcnow_iso(),
            },
        )
        return {
            "kind": "genome",
            "genome_id": genome_id,
            "seller_genome": source,
            "delivered_variant": variant,
            "note": (
                "delivered as a mutated variant and stored unselected: a licence buys a "
                "starting point, not a deployment, and not a copy"
            ),
        }

    # =====================================================================
    # capital: requested here, applied only once a human agrees
    # =====================================================================
    def _buy_capital(self, buyer: str, listing: Listing) -> dict:
        """Record the intent and ask. Moves nothing."""
        seller = self.store.get_firm(listing.seller)
        purse = self.store.cash_view(seller, cash_floor_pct=self.config.firm.cash_floor_pct)
        if listing.price > purse.withdrawable:
            raise MarketError(
                f"{listing.seller} no longer has {fmt_money(listing.price)} uninvested "
                f"(only {fmt_money(purse.withdrawable)})"
            )

        approval_id = None
        if self.gate is not None:
            from ...agents.human_gate import ApprovalAction

            approval = self.gate.request(
                ApprovalAction.ALLOCATE_CAPITAL.value,
                f"Transfer {fmt_money(listing.price)} of trading capital from "
                f"{listing.seller} to {buyer} (black market listing #{listing.id})",
                amount=listing.price,
                details={
                    "kind": "capital_transfer",
                    "firm": buyer,
                    "from_firm": listing.seller,
                    "listing": listing.id,
                    "amount": str(listing.price),
                },
                dedupe_key=f"transfer:{listing.id}",
            )
            approval_id = approval.id

        transaction_id = self.db.insert(
            "market_transactions",
            {
                "listing_id": listing.id,
                "buyer": buyer,
                "seller": listing.seller,
                "asset_type": "capital",
                "price": listing.price,
                "currency": "cash",
                "escrow_state": "held",
                "delivered": False,
                "settled": False,
                "approval_id": approval_id,
                "notes": "awaiting human approval; no capital has moved",
                "created_at": utcnow_iso(),
            },
        )
        self.db.update("market_listings", listing.id, {"status": "pending", "buyer": buyer})
        return {
            "transaction": transaction_id,
            "listing": listing.id,
            "settled": False,
            "approval_id": approval_id,
            "note": (
                "capital transfers do not settle in the market. Approve the request, then "
                "run `trade market settle` — a raise needs a human, wherever it comes from"
            ),
        }

    def settle_capital_transfer(self, transaction_id: int) -> dict:
        """Apply an approved transfer: seller's cut and buyer's raise, together."""
        row = self.db.query_one(
            "SELECT * FROM market_transactions WHERE id = ?", (transaction_id,)
        )
        if row is None:
            raise MarketError(f"no transaction {transaction_id}")
        if row["asset_type"] != "capital":
            raise MarketError("only capital transfers settle through this path")
        if bool(row["settled"]):
            raise MarketError(f"transaction {transaction_id} is already settled")

        approval_id = row.get("approval_id")
        if approval_id is None or self.gate is None:
            raise MarketError("this transfer has no approval attached; it cannot settle")
        approval = self.gate.get(approval_id)
        if not approval.is_approved:
            raise MarketError(
                f"approval #{approval_id} is {approval.status}; capital does not move until "
                "a human approves it"
            )

        amount = D(row["price"])
        seller = self.store.get_firm(row["seller"])
        buyer = self.store.get_firm(row["buyer"])
        if seller is None or buyer is None:
            raise MarketError("one of the firms no longer exists")

        purse = self.store.cash_view(seller, cash_floor_pct=self.config.firm.cash_floor_pct)
        if amount > purse.withdrawable:
            raise MarketError(
                f"{seller.firm_key} now has only {fmt_money(purse.withdrawable)} uninvested"
            )

        # Both legs together. set_allocation refuses to leave cash negative, so
        # a half-applied transfer cannot survive this block.
        with self.db.transaction():
            self.store.set_allocation(seller.id, D(seller.allocation) - amount, -amount)
            self.store.set_allocation(buyer.id, D(buyer.allocation) + amount, amount)
            self.db.update(
                "market_transactions",
                transaction_id,
                {
                    "escrow_state": "released",
                    "delivered": True,
                    "settled": True,
                    "notes": f"approved by {approval.approved_by or 'human'}",
                    "delivery": json.dumps(
                        {"kind": "capital", "amount": str(amount), "approval": approval_id}
                    ),
                },
            )
            self.db.update(
                "market_listings",
                row["listing_id"],
                {"status": "sold", "settled_at": utcnow_iso()},
            )
        self.store.record_event(
            "capital_transfer",
            f"{seller.firm_key} -> {buyer.firm_key}: {fmt_money(amount)} "
            f"(listing #{row['listing_id']}, approval #{approval_id})",
            firm_id=buyer.id,
            payload={"from": seller.firm_key, "to": buyer.firm_key, "amount": str(amount)},
        )
        return {
            "transaction": transaction_id,
            "settled": True,
            "from": seller.firm_key,
            "to": buyer.firm_key,
            "amount": str(amount),
        }

    # =====================================================================
    # reads
    # =====================================================================
    def transactions(self, limit: int = 30) -> list:
        return self.db.query(
            "SELECT * FROM market_transactions ORDER BY id DESC LIMIT ?", (limit,)
        )

    def pending_transfers(self) -> list:
        return self.db.query(
            "SELECT * FROM market_transactions WHERE asset_type = ? AND settled = ?",
            ("capital", False),
        )

    def reputation(self) -> list:
        return [
            {"firm": b.firm_key, "reputation": b.reputation, "tokens": b.balance}
            for b in self.tokens.balances()
        ]


__all__ = ["ASSET_TYPES", "BlackMarket", "CASH_ASSETS", "Listing", "MarketError", "TOKEN_ASSETS"]
