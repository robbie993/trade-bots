"""The crypto pulse: the four signals the fleet's crypto bots read that the
village's own analysts did not.

Lifted from `btcc_bot.py` in the live fleet (Railway, `supportive-benevolence`),
formula for formula, rather than invented here. The village's technical seat
already computes trend, momentum, MACD and volume from prices; what btcc adds is
positioning and mood, which no price series carries:

* **funding** — Hyperliquid perpetual funding, scaled to an 8-hour rate. Longs
  paying a lot to stay in is a crowded trade, so it is read contrarian:
  +0.05%/8h is a full bearish lean. (btcc moved to Hyperliquid because Bybit is
  geo-blocked in the US and was silently returning zero funding.)
* **sentiment** — the alternative.me Fear & Greed index, contrarian: extreme
  greed leans short, extreme fear leans long.
* **macro** — CoinGecko's 24h change in total crypto market cap: ±4% is ±1,
  risk-on or risk-off for the whole asset class.
* **open interest** — rising OI in the direction of the last ~4h of price is
  conviction, falling OI is unwinding. Neutral until 30 minutes of history
  exist, exactly as btcc does, so it never fires on its first reading.

Each is in [-1, 1]. The reading is their plain mean times 100, with a fixed
confidence of 35, and every component is in the note so a debate can see why.
btcc weights these by its own learned weights; the village does not have those
yet, and an equal weighting is the honest default until a test says otherwise.

Off unless `TRADE_CRYPTO_PULSE_ENABLED` is set.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Callable, Optional

from .signals import Reading

HYPERLIQUID = "https://api.hyperliquid.xyz/info"
FEAR_GREED = "https://api.alternative.me/fng/"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
HEADERS = {"User-Agent": "village-crypto-pulse", "Accept": "application/json"}

#: The village's spelling -> Hyperliquid's. The k-prefixed contracts are
#: priced per thousand tokens, which changes the mark but not the funding rate
#: or the direction of open interest, the only two things read here.
HL_NAME = {
    "BTC-USD": "BTC", "ETH-USD": "ETH", "SOL-USD": "SOL", "DOGE-USD": "DOGE",
    "SHIB-USD": "kSHIB", "PEPE-USD": "kPEPE", "WIF-USD": "WIF",
}

FUNDING_HRS = 8          # btcc scales hourly funding to the 8h convention
CONFIDENCE = Decimal("35")
OI_WINDOW_S = 4 * 3600
OI_MIN_AGE_S = 1800


def funding_score(hourly: float) -> float:
    return max(-1.0, min(1.0, -(hourly * FUNDING_HRS) / 0.0005))


def sentiment_score(fng: int) -> float:
    if fng >= 80:
        return -0.6
    if fng <= 20:
        return 0.6
    return (50 - fng) / 50 * 0.4


def macro_score(mcap_change_pct: float) -> float:
    return max(-1.0, min(1.0, mcap_change_pct / 4.0))


def _post_json(url: str, body: dict, timeout: int = 8):
    import urllib.request

    from .data.feeds import _ssl_context

    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**HEADERS, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout,   # noqa: S310 - fixed host
                                context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


class CryptoPulse:
    name = "crypto_pulse"

    def __init__(self, board, get_json: Optional[Callable] = None,
                 post_json: Optional[Callable] = None, clock=time.time):
        self.board = board
        self._clock = clock
        if get_json is None:
            from .data.feeds import _get_json

            def get_json(url, params, headers=None):
                return _get_json(url, params, headers=headers, timeout=8)
        self._get = get_json
        self._post = post_json or _post_json
        #: symbol -> [(t, open interest, mark)], the last few hours. In memory
        #: on purpose, like btcc: a restart costs thirty minutes of neutral OI.
        self._oi: dict = {}

    def _oi_score(self, symbol: str, oi: float, mark: float) -> Optional[float]:
        now = self._clock()
        hist = self._oi.setdefault(symbol, [])
        hist.append((now, oi, mark))
        while hist and hist[0][0] < now - OI_WINDOW_S:
            hist.pop(0)
        ref = next((h for h in hist if now - h[0] >= OI_MIN_AGE_S), None)
        if ref is None or not ref[1] or not ref[2]:
            return None
        price_dir = 1 if mark >= ref[2] else -1
        return max(-1.0, min(1.0, (oi - ref[1]) / ref[1] * 20)) * price_dir

    def run(self, market, as_of=None) -> list:
        as_of = as_of if as_of is not None else market.as_of()
        if as_of is None or self.board.published(self.name, as_of):
            return []
        traded = [s for s in (str(x).upper() for x in getattr(market, "symbols", []))
                  if s in HL_NAME]
        if not traded:
            return []
        failed = []
        ctx = {}
        try:
            meta, ctxs = self._post(HYPERLIQUID, {"type": "metaAndAssetCtxs"})
            ctx = {u["name"]: c for u, c in zip(meta["universe"], ctxs)}
        except Exception as exc:  # noqa: BLE001
            failed.append(f"hyperliquid: {str(exc)[:80]}")
        fng = macro = None
        try:
            fng = int(self._get(FEAR_GREED, {}, headers=HEADERS)["data"][0]["value"])
        except Exception as exc:  # noqa: BLE001
            failed.append(f"fear & greed: {str(exc)[:80]}")
        try:
            macro = float(self._get(COINGECKO_GLOBAL, {}, headers=HEADERS)
                          ["data"]["market_cap_change_percentage_24h_usd"])
        except Exception as exc:  # noqa: BLE001
            failed.append(f"coingecko: {str(exc)[:80]}")

        readings = []
        for symbol in traded:
            parts = {}
            c = ctx.get(HL_NAME[symbol])
            if c:
                hourly = float(c.get("funding") or 0)
                parts["funding"] = funding_score(hourly)
                oi = self._oi_score(symbol, float(c.get("openInterest") or 0),
                                    float(c.get("markPx") or 0))
                if oi is not None:
                    parts["oi"] = oi
            if fng is not None:
                parts["sentiment"] = sentiment_score(fng)
            if macro is not None:
                parts["macro"] = macro_score(macro)
            if not parts:
                continue
            score = sum(parts.values()) / len(parts) * 100
            note = " ".join(f"{k} {v:+.2f}" for k, v in parts.items())
            extra = []
            if c:
                extra.append(f"funding {float(c.get('funding') or 0) * FUNDING_HRS * 100:+.4f}%/8h")
            if fng is not None:
                extra.append(f"F&G {fng}")
            if macro is not None:
                extra.append(f"mcap {macro:+.2f}%/24h")
            readings.append(Reading(symbol, Decimal(str(round(score, 2))), CONFIDENCE,
                                    f"{note} ({', '.join(extra)})"[:240]))

        if readings:
            self.board.publish(self.name, readings, as_of)
        else:
            self.board.mark_silent(self.name, as_of)
        notes = []
        if readings:
            notes.append("crypto pulse: " + ", ".join(f"{r.symbol} {r.score:+}" for r in readings))
        notes += [f"crypto pulse source FAILING — {f}" for f in failed]
        return notes


__all__ = ["CryptoPulse", "HL_NAME", "funding_score", "macro_score", "sentiment_score"]
