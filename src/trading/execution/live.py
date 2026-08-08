"""Live venues — Alpaca, Webull, Binance.

These adapters exist so that pointing the ecosystem at a real broker is a
config change rather than a rewrite. They are also the sharpest edge in the
repository, so they behave accordingly:

* Every live venue refuses to send an order unless a ``live_trading``
  approval for *that venue* has been approved by a human. No approval, no
  order — and the refusal is an exception, never a silent no-op, because a
  trading loop that quietly stops trading is its own kind of disaster.
* An approval covers one venue. Approving Alpaca does not approve Binance.
* Credentials come from the environment and are never logged, stored in the
  ledger, or included in an audit entry.

Nothing in this file is exercised by the test suite against a real broker.
What the tests do assert is the refusal: that an unapproved venue raises, and
that no fill row is written when it does.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Optional
from urllib import error, request

from ...money import money
from ..models import Fill, TradeProposal, price, qty


class LiveTradingNotApproved(RuntimeError):
    """No approved ``live_trading`` row for this venue."""


class VenueNotConfigured(RuntimeError):
    """Credentials or endpoint missing for a live venue."""


class LiveVenue:
    """Shared refusal logic. Subclasses only implement ``_send``."""

    name = "live"
    is_live = True
    credential_env: tuple = ()

    def __init__(self, gate=None, paper_endpoint: bool = True):
        self.gate = gate
        self.paper_endpoint = paper_endpoint

    # -- the gate ---------------------------------------------------------
    def approval(self):
        """The approved live-trading row for this venue, or None."""
        if self.gate is None:
            return None
        from ...agents.human_gate import ApprovalAction, ApprovalStatus

        rows = self.gate.db.query(
            "SELECT * FROM human_approvals WHERE action = ? AND status = ? ORDER BY id DESC",
            (ApprovalAction.LIVE_TRADING.value, ApprovalStatus.APPROVED.value),
        )
        from ...agents.human_gate import Approval

        for row in rows:
            approval = Approval.from_row(row)
            if approval.details.get("venue") == self.name:
                return approval
        return None

    def request_approval(self, reason: str = ""):
        """Ask for permission to trade live on this venue. Sends no order."""
        if self.gate is None:
            raise LiveTradingNotApproved(
                f"{self.name}: live trading needs a human gate; none was configured"
            )
        from ...agents.human_gate import ApprovalAction

        missing = [name for name in self.credential_env if not os.environ.get(name)]
        return self.gate.request(
            ApprovalAction.LIVE_TRADING.value,
            reason or f"Enable LIVE trading on {self.name} with real money",
            details={
                "venue": self.name,
                "endpoint": "paper" if self.paper_endpoint else "live",
                "credentials_present": not missing,
                "missing_env": missing,
            },
            dedupe_key=f"live:{self.name}",
        )

    def require_approval(self):
        approval = self.approval()
        if approval is None:
            raise LiveTradingNotApproved(
                f"{self.name}: no approved live_trading request. Run "
                f"`python -m src.main trade live-request --venue {self.name}`, then approve it."
            )
        return approval

    def credentials(self) -> dict:
        missing = [name for name in self.credential_env if not os.environ.get(name)]
        if missing:
            raise VenueNotConfigured(f"{self.name}: missing {', '.join(missing)}")
        return {name: os.environ[name] for name in self.credential_env}

    # -- execution --------------------------------------------------------
    def execute(self, proposal: TradeProposal, reference_price: Optional[Decimal] = None) -> Fill:
        self.require_approval()
        creds = self.credentials()
        response = self._send(proposal, creds)
        filled_qty = qty(response.get("quantity", proposal.quantity))
        filled_price = price(response.get("price", reference_price or proposal.reference_price))
        return Fill(
            firm_id=proposal.firm_id,
            symbol=proposal.symbol,
            side=proposal.side,
            quantity=filled_qty,
            price=filled_price,
            fee=money(response.get("fee", 0)),
            venue=self.name,
            proposal_id=proposal.id,
            as_of=proposal.as_of,
        )

    def _send(self, proposal: TradeProposal, credentials: dict) -> dict:  # pragma: no cover
        raise NotImplementedError


class AlpacaVenue(LiveVenue):
    """Stocks and ETFs. Defaults to Alpaca's paper endpoint."""

    name = "alpaca"
    credential_env = ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY")

    @property
    def base_url(self) -> str:
        if os.environ.get("ALPACA_BASE_URL"):
            return os.environ["ALPACA_BASE_URL"].rstrip("/")
        return (
            "https://paper-api.alpaca.markets"
            if self.paper_endpoint
            else "https://api.alpaca.markets"
        )

    def _send(self, proposal: TradeProposal, credentials: dict) -> dict:  # pragma: no cover
        payload = {
            "symbol": proposal.symbol,
            "qty": str(proposal.quantity),
            "side": proposal.side,
            "type": "market",
            "time_in_force": "day",
        }
        return _post_json(
            f"{self.base_url}/v2/orders",
            payload,
            {
                "APCA-API-KEY-ID": credentials["ALPACA_API_KEY_ID"],
                "APCA-API-SECRET-KEY": credentials["ALPACA_API_SECRET_KEY"],
            },
        )


class BinanceVenue(LiveVenue):
    """Crypto. Signed REST orders; defaults to the Binance testnet."""

    name = "binance"
    credential_env = ("BINANCE_API_KEY", "BINANCE_API_SECRET")

    @property
    def base_url(self) -> str:
        if os.environ.get("BINANCE_BASE_URL"):
            return os.environ["BINANCE_BASE_URL"].rstrip("/")
        return (
            "https://testnet.binance.vision"
            if self.paper_endpoint
            else "https://api.binance.com"
        )

    def _send(self, proposal: TradeProposal, credentials: dict) -> dict:  # pragma: no cover
        import hashlib
        import hmac
        import time
        from urllib.parse import urlencode

        query = urlencode(
            {
                "symbol": proposal.symbol.replace("-", ""),
                "side": proposal.side.upper(),
                "type": "MARKET",
                "quantity": str(proposal.quantity),
                "timestamp": int(time.time() * 1000),
            }
        )
        signature = hmac.new(
            credentials["BINANCE_API_SECRET"].encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return _post_json(
            f"{self.base_url}/api/v3/order?{query}&signature={signature}",
            None,
            {"X-MBX-APIKEY": credentials["BINANCE_API_KEY"]},
        )


class WebullVenue(LiveVenue):
    """Webull has no first-party public trading API.

    The adapter is here because the build document lists it, and it fails
    with an explanation rather than pretending to route an order through an
    unofficial endpoint that can change without notice.
    """

    name = "webull"
    credential_env = ("WEBULL_ACCESS_TOKEN", "WEBULL_ACCOUNT_ID")

    def _send(self, proposal: TradeProposal, credentials: dict) -> dict:
        raise VenueNotConfigured(
            "webull has no supported public trading API. Use alpaca for equities, or "
            "implement Webull's endpoint yourself in WebullVenue._send."
        )


def _post_json(url: str, payload: Optional[dict], headers: dict) -> dict:  # pragma: no cover
    body = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode() or "{}")
    except error.HTTPError as exc:
        raise VenueNotConfigured(f"{url} returned {exc.code}: {exc.read().decode()[:200]}") from exc
    except error.URLError as exc:
        raise VenueNotConfigured(f"{url} unreachable: {exc.reason}") from exc


LIVE_VENUES = {
    "alpaca": AlpacaVenue,
    "binance": BinanceVenue,
    "webull": WebullVenue,
}


__all__ = [
    "AlpacaVenue",
    "BinanceVenue",
    "LIVE_VENUES",
    "LiveTradingNotApproved",
    "LiveVenue",
    "VenueNotConfigured",
    "WebullVenue",
]
