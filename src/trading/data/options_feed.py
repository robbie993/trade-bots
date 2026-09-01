"""Option chains, and the spread that decides whether an option trade is real.

`options.py` can parse a contract, size it and settle it at expiry. Nothing
could ever *price* one — the feed layer serves equity and crypto bars and has
no notion of a chain. So the village could model an option and never trade one.

This is the missing half, and it is deliberately built around the cost rather
than the price.

**Why the spread is the headline and the mid is a lie.** The sibling repo's
2026-07-31 insider-options study found a real underlying signal of roughly
+0.5% per 21 days, and round-trip option spreads of about **52% of premium** on
the names an insider screen actually surfaces. The edge was real and completely
hopeless, because nothing in the pipeline measured cost. A backtest that fills
at the mid would have reported that study as a success.

So `Quote.buy_at` is the **ask** and `Quote.sell_at` is the **bid**, always,
and `round_trip_pct` is stated on every quote whether anyone asked or not. A
caller has to work to avoid seeing what a trade costs.

**Quotes outside regular hours are not tradable reality.** Market-maker spreads
widen enormously when the underlying is closed, and a strategy evaluated on
03:00 quotes is being evaluated on prices nobody would have filled. `is_fresh`
says so rather than leaving the caller to notice.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from ...money import D, ZERO

SNAPSHOTS = "https://data.alpaca.markets/v1beta1/options/snapshots/{underlying}"

#: A round trip costing more than this share of the premium cannot be traded
#: profitably by anything this village would put on. Stated as a constant
#: rather than a magic number in a branch: it is the single most important
#: threshold in options, and the insider study died on the wrong side of it.
MAX_ROUND_TRIP_PCT = D(os.environ.get("TRADE_OPTION_MAX_SPREAD_PCT", "15"))


@dataclass
class Quote:
    """One contract, priced the way a trade would actually fill."""

    symbol: str
    bid: Decimal
    ask: Decimal
    as_of: Optional[datetime] = None

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / D(2)

    @property
    def buy_at(self) -> Decimal:
        """You pay the ask. There is no version of this where you pay the mid."""
        return self.ask

    @property
    def sell_at(self) -> Decimal:
        return self.bid

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def round_trip_pct(self) -> Decimal:
        """Buying and selling immediately, as a percentage of the mid.

        This is the number that killed the insider-options study at ~52%, and
        it is a property rather than a helper so that no code path can price a
        contract without it being one attribute away.
        """
        if self.mid <= 0:
            return D(100)
        return (self.spread / self.mid) * D(100)

    @property
    def tradable(self) -> bool:
        return (self.bid > 0 and self.ask > 0
                and self.round_trip_pct <= MAX_ROUND_TRIP_PCT)

    def is_fresh(self, now: Optional[datetime] = None, max_age_s: float = 900) -> bool:
        if self.as_of is None:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - self.as_of).total_seconds() <= max_age_s


def _stamp(value) -> Optional[datetime]:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:26] + "+00:00"
                                        if "+" not in text else text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class AlpacaOptionFeed:
    """Snapshots for one underlying's chain. Refuses rather than degrades."""

    name = "alpaca-options"

    def __init__(self, timeout_s: int = 20, limit: int = 100):
        self.timeout_s = timeout_s
        self.limit = int(limit)
        self.last_error: dict = {}

    @staticmethod
    def credentials() -> tuple:
        key = (os.environ.get("ALPACA_API_KEY_ID")
               or os.environ.get("APCA_API_KEY_ID") or "").strip()
        secret = (os.environ.get("ALPACA_API_SECRET_KEY")
                  or os.environ.get("APCA_API_SECRET_KEY") or "").strip()
        return key, secret

    def chain(self, underlying: str, expiry_gte: str = "",
              expiry_lte: str = "", right: str = "",
              strike_gte=None, strike_lte=None) -> list:
        """Every quoted contract on this underlying. `[]` when it cannot say.

        Never raises: an underlying with no chain is a normal thing — most
        symbols this village trades have none at all — and it is the caller's
        business to have no opinion rather than the feed's to crash.
        """
        from .feeds import _ssl_context

        key, secret = self.credentials()
        if not key or not secret:
            self.last_error[underlying] = "no alpaca credentials"
            return []

        # **Ask for the side you want.** The API returns contracts in symbol
        # order and "C" sorts before "P", so any limit short of the whole chain
        # comes back calls-only. Measured on SPY: limit=100 returned 100 calls
        # and zero puts, which meant a desk asking for a put could never be
        # offered one — silently, and on the side that had made $2,469 of the
        # account's $2,931. A truncation that changes what a strategy is
        # allowed to do is not a performance detail.
        params = {"limit": self.limit}
        if right:
            params["type"] = "call" if right.upper().startswith("C") else "put"
        if strike_gte is not None:
            params["strike_price_gte"] = float(strike_gte)
        if strike_lte is not None:
            params["strike_price_lte"] = float(strike_lte)
        if expiry_gte:
            params["expiration_date_gte"] = expiry_gte
        if expiry_lte:
            params["expiration_date_lte"] = expiry_lte
        url = (SNAPSHOTS.format(underlying=underlying.upper())
               + "?" + urllib.parse.urlencode(params))
        request = urllib.request.Request(url, headers={
            "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
        })
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed host
                request, timeout=self.timeout_s, context=_ssl_context()
            ) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            self.last_error[underlying] = f"HTTP {exc.code}"
            return []
        except Exception as exc:  # noqa: BLE001 - no chain is not a crash
            self.last_error[underlying] = f"{type(exc).__name__}: {str(exc)[:60]}"
            return []

        self.last_error.pop(underlying, None)
        out = []
        for symbol, snap in (payload.get("snapshots") or {}).items():
            quote = snap.get("latestQuote") or {}
            bid, ask = quote.get("bp"), quote.get("ap")
            if bid is None or ask is None:
                continue
            out.append(Quote(symbol=symbol, bid=D(str(bid)), ask=D(str(ask)),
                             as_of=_stamp(quote.get("t"))))
        return out


__all__ = ["AlpacaOptionFeed", "Quote", "MAX_ROUND_TRIP_PCT", "SNAPSHOTS"]
