"""The meme radar: on-chain crowd flow for the meme coins the village can trade,
and a log of the ones it cannot.

**What it publishes.** For DOGE, SHIB, PEPE and WIF — the Meme Desk's universe —
it reads the deepest DEX pool on DexScreener and votes the direction of the
last six hours' order flow: more buys than sells is up, more sells than buys is
down. The size of the vote is the imbalance; the confidence grows with the
number of trades behind it, so forty trades cannot shout as loudly as four
thousand.

That is crowd *flow*, not crowd *talk*, and it is a different thing from what
`news.py` and `crowd.py` measure. It has not been tested against returns. Like
every seat on the board it moves one vote in one debate, and a firm's
`signal_trust` gene decides how much that vote is worth to it.

**Why each coin is pinned to a contract address.** Searching DexScreener for
"WIF" returned an unrelated token called World is Flat on another chain. A
symbol is a name anyone can register; an address is the coin. DOGE has no
canonical DEX pool — it trades on exchanges — so it is read from wrapped DOGE
on BSC and its confidence is capped low, because that pool is a sliver of
where DOGE actually changes hands.

**What it only records.** DexScreener's most-boosted tokens and Pump.fun's
biggest and live launches go to `intel` (migration 027). The village cannot
buy any of them — Alpaca lists none — so they get no reading and no direction.
They are there for research, and for the fomo bot's operator.

Off unless `TRADE_MEME_RADAR_ENABLED` is set, for the same reason as the news
desk: it reaches the open internet on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional

from . import intel
from .signals import Reading

DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/{chain}/{address}"
DEX_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"
PUMP_TOP = "https://frontend-api-v3.pump.fun/coins"
PUMP_LIVE = "https://frontend-api-v3.pump.fun/coins/currently-live"

HEADERS = {"User-Agent": "Mozilla/5.0 (village meme radar)", "Accept": "application/json"}


@dataclass(frozen=True)
class Coin:
    symbol: str          # the village's spelling
    chain: str
    address: str
    max_confidence: int  # how loud this pool is allowed to be about the coin


COINS = (
    Coin("DOGE-USD", "bsc", "0xbA2aE424d960c26247Dd6c32edC70B295c744C43", 25),
    Coin("SHIB-USD", "ethereum", "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE", 60),
    Coin("PEPE-USD", "ethereum", "0x6982508145454Ce325dDbE47a25d4ec3d2311933", 60),
    Coin("WIF-USD", "solana", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", 60),
)

#: Trades at which confidence reaches half its cap. A pool with this many buys
#: and sells in six hours is a crowd; one with a tenth of it is a few wallets.
HALF_CONFIDENCE_TRADES = 300


def flow_reading(coin: Coin, pairs: list) -> Optional[Reading]:
    """The deepest pool's six-hour flow as a vote, or None if there is no crowd."""
    pools = [p for p in pairs or [] if isinstance(p, dict)]
    if not pools:
        return None
    pool = max(pools, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
    tx = (pool.get("txns") or {}).get("h6") or {}
    buys, sells = int(tx.get("buys") or 0), int(tx.get("sells") or 0)
    trades = buys + sells
    if trades == 0:
        return None
    imbalance = (buys - sells) / trades
    score = round(100 * imbalance, 2)
    confidence = round(coin.max_confidence * trades / (trades + HALF_CONFIDENCE_TRADES), 2)
    change = (pool.get("priceChange") or {}).get("h6")
    volume = (pool.get("volume") or {}).get("h24") or 0
    note = (f"DEX 6h on {coin.chain}: {buys:,} buys / {sells:,} sells"
            + (f", price {float(change):+.2f}%" if change is not None else "")
            + f", ${float(volume):,.0f} 24h volume")
    return Reading(coin.symbol, Decimal(str(score)), Decimal(str(confidence)), note)


class MemeRadar:
    name = "meme_radar"

    def __init__(self, board, db, get_json: Optional[Callable] = None, coins=COINS):
        self.board = board
        self.db = db
        self.coins = tuple(coins)
        if get_json is None:
            from .data.feeds import _get_json

            # Seven requests a bar, inside the tick: each one gets eight
            # seconds, not the feed's twenty, so a slow site costs the tick a
            # little rather than a minute.
            def get_json(url, params, headers=None):
                return _get_json(url, params, headers=headers, timeout=8)
        self._get = get_json

    def run(self, market, as_of=None) -> list:
        as_of = as_of if as_of is not None else market.as_of()
        if as_of is None or self.board.published(self.name, as_of):
            return []
        traded = {str(s).upper() for s in getattr(market, "symbols", [])}
        readings, failed = [], []
        for coin in self.coins:
            if traded and coin.symbol not in traded:
                continue
            try:
                pairs = self._get(DEX_TOKENS.format(chain=coin.chain, address=coin.address),
                                  {}, headers=HEADERS)
            except Exception as exc:  # noqa: BLE001 - one coin is not the radar
                failed.append(f"{coin.symbol}: {str(exc)[:80]}")
                continue
            reading = flow_reading(coin, pairs)
            if reading is not None:
                readings.append(reading)

        if readings:
            self.board.publish(self.name, readings, as_of)
        else:
            self.board.mark_silent(self.name, as_of)

        logged = self._log_launches(failed)
        notes = []
        if readings:
            notes.append("meme radar: " + ", ".join(
                f"{r.symbol} {r.score:+} (conf {r.confidence})" for r in readings))
        if logged:
            notes.append(f"meme radar: logged {logged} trending token(s) it cannot trade")
        for f in failed[:3]:
            notes.append(f"meme radar source FAILING — {f}")
        return notes

    def _log_launches(self, failed: list) -> int:
        n = 0
        try:
            for b in self._get(DEX_BOOSTS, {}, headers=HEADERS)[:30]:
                key = f"{b.get('chainId')}:{b.get('tokenAddress')}"
                intel.upsert(self.db, "dexscreener_boosts", key,
                             title=(b.get("description") or "")[:200] or key,
                             url=b.get("url") or "",
                             score=b.get("totalAmount"),
                             detail={"chain": b.get("chainId"),
                                     "links": [x.get("url") for x in b.get("links") or []][:5]})
                n += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"dexscreener boosts: {str(exc)[:80]}")
        for label, url, params in (
            ("pumpfun_top", PUMP_TOP, {"offset": 0, "limit": 20, "sort": "market_cap",
                                       "order": "DESC", "includeNsfw": "false"}),
            ("pumpfun_live", PUMP_LIVE, {"offset": 0, "limit": 20, "includeNsfw": "false"}),
        ):
            try:
                for c in self._get(url, params, headers=HEADERS)[:20]:
                    mint = c.get("mint")
                    if not mint:
                        continue
                    intel.upsert(self.db, label, mint,
                                 title=f"{c.get('name', '')} (${c.get('symbol', '')})",
                                 url=f"https://pump.fun/coin/{mint}",
                                 score=c.get("usd_market_cap") or c.get("market_cap"),
                                 detail={"graduated": bool(c.get("complete")),
                                         "created": c.get("created_timestamp"),
                                         "twitter": c.get("twitter"),
                                         "replies": c.get("reply_count")})
                    n += 1
            except Exception as exc:  # noqa: BLE001 - pump.fun blocks bots some days
                failed.append(f"{label}: {str(exc)[:80]}")
        return n


__all__ = ["COINS", "Coin", "MemeRadar", "flow_reading"]
