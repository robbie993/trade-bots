"""Watch what the traders you follow on fomo.family are buying — read only.

    python scripts/fomo_watch.py --dry-run
    python scripts/fomo_watch.py --to-railway      # the scheduled run

The fomo bot (fleet, currently not running) copied these traders with real SOL.
This does none of that. It reads, through the village browser where the
operator is logged in to fomo.family, using the page's own session: the request
is made from inside the logged-in page, so no token is copied, stored or seen by
this script. It never swaps and never touches a wallet.

For each followed trader it reads their latest swaps, keeps the Solana buys and
sells, and writes each to `intel` (source `fomo`). Then it looks for
**consensus**: a token bought by `consensus_traders` or more followed traders
inside `window_hours`. Each consensus token is looked up on DexScreener (name,
symbol, liquidity, age) and recorded as `fomo_consensus`. That is Pump.fun-era
intel the village cannot trade — Alpaca lists none of it — kept for research
and for the fomo desk's operator.

The one bridge into the board: WIF is the only one of the village's four meme
coins that lives on Solana. If followed traders are buying or selling WIF, their
net direction becomes the `fomo_calls` snapshot, heard through the social
scanner like any crowd seat. One trader is one voice.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from scripts.insta_watch import browser  # noqa: E402
from src.trading import intel  # noqa: E402

FOMO_API = "https://prod-api.fomo.family"
#: The operator's fomo.family user id, as the fomo bot has always used it. An id,
#: not a secret: it is in every profile URL.
FOMO_USER_ID = "badb7f58-3e17-5759-9d1e-f4d3dc534b96"
SOLANA = 1399811149
BASE_MINTS = {  # SOL and the stablecoins: a swap from these is a buy
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
WIF_MINT = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
SOURCE = "fomo"
SNAPSHOT = "fomo_calls"

CONSENSUS_TRADERS = 2
WINDOW_HOURS = 6
SWAPS_PER_TRADER = 10


class NotSignedIn(RuntimeError):
    pass


def api(page, path: str):
    """GET from fomo's API inside the logged-in page, with the page's own session."""
    result = page.evaluate("""async (url) => {
        const raw = localStorage.getItem('privy:token');
        if (!raw) return {error: 'no session'};
        let token = raw; try { token = JSON.parse(raw); } catch (e) {}
        const r = await fetch(url, {headers: {Authorization: 'Bearer ' + token,
                                              Accept: 'application/json'}});
        if (!r.ok) return {error: 'HTTP ' + r.status};
        return await r.json();
    }""", FOMO_API + path)
    if isinstance(result, dict) and result.get("error"):
        if result["error"] in ("no session", "HTTP 401"):
            raise NotSignedIn("the village browser is not signed in to fomo.family")
        raise RuntimeError(result["error"])
    return result


def classify(s: dict):
    if s.get("inNetworkId") != SOLANA or s.get("outNetworkId") != SOLANA:
        return None
    tin, tout = s.get("inTokenAddress"), s.get("outTokenAddress")
    if not tin or not tout:
        return None
    usd = float(s.get("humanUsdAmountIn") or 0)
    if tin in BASE_MINTS and tout not in BASE_MINTS:
        return "buy", tout, usd
    if tout in BASE_MINTS and tin not in BASE_MINTS:
        return "sell", tin, usd
    return None


def _when(s: dict):
    for key in ("createdAt", "timestamp", "blockTime", "created_at"):
        v = s.get(key)
        if v:
            try:
                if isinstance(v, (int, float)):
                    return datetime.fromtimestamp(v / (1000 if v > 1e12 else 1), tz=timezone.utc)
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def consensus(trades: list, now: datetime, traders: int = CONSENSUS_TRADERS,
              hours: float = WINDOW_HOURS) -> dict:
    """mint -> set of traders who bought it inside the window."""
    cutoff = now - timedelta(hours=hours)
    buyers = defaultdict(set)
    for t in trades:
        if t["action"] == "buy" and t["at"] and t["at"] >= cutoff:
            buyers[t["mint"]].add(t["trader"])
    return {m: b for m, b in buyers.items() if len(b) >= traders}


def wif_reading(trades: list, now: datetime, hours: float = 24) -> list:
    """Net direction of followed traders on WIF, one trader one voice."""
    cutoff = now - timedelta(hours=hours)
    last = {}
    for t in sorted(trades, key=lambda t: t["at"] or now):
        if t["mint"] == WIF_MINT and t["at"] and t["at"] >= cutoff:
            last[t["trader"]] = 1 if t["action"] == "buy" else -1
    if not last:
        return []
    bulls = sum(1 for d in last.values() if d > 0)
    bears = len(last) - bulls
    if bulls == bears:
        return []
    return [{"symbol": "WIF-USD", "score": round(100 * (bulls - bears) / len(last), 2),
             "confidence": min(60, 12 * abs(bulls - bears)),
             "note": f"fomo.family: {bulls} followed trader(s) buying WIF, {bears} selling"}]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-railway", action="store_true")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    db = None
    if not args.dry_run:
        from scripts.fleet_sync import railway_database_url
        from src.db.connection import Database

        db = Database.from_url(args.database_url or railway_database_url())
        db.init_schema()

    now = datetime.now(timezone.utc)
    trades, failed = [], []
    with sync_playwright() as p:
        page = browser(p).contexts[0].new_page()
        try:
            page.goto("https://fomo.family/", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            users = (api(page, f"/v2/users/{FOMO_USER_ID}/followingPaginate?limit=200")
                     .get("responseObject", {}).get("users", []))
            print(f"following {len(users)} trader(s) on fomo.family")
            for u in users:
                name = u.get("userHandle") or u.get("displayName") or u.get("id")
                try:
                    swaps = (api(page, f"/v2/users/{u['id']}/swaps?limit={SWAPS_PER_TRADER}")
                             .get("responseObject", {}).get("swaps", []))
                except RuntimeError as exc:
                    failed.append(f"{name}: {exc}")
                    continue
                for s in swaps:
                    c = classify(s)
                    if c:
                        action, mint, usd = c
                        trades.append({"trader": name, "action": action, "mint": mint,
                                       "usd": usd, "at": _when(s),
                                       "id": str(s.get("id") or s.get("txSignature")
                                                 or f"{u['id']}:{mint}:{_when(s)}")})
                time.sleep(0.4)
        except NotSignedIn as exc:
            print(f"STOPPED: {exc}. Log in to fomo.family in the village browser.")
            return 1
        finally:
            page.close()

    agreed = consensus(trades, now)
    info = {}
    if agreed:
        from src.trading.data.feeds import _get_json

        for mint in agreed:
            try:
                pairs = _get_json(f"https://api.dexscreener.com/tokens/v1/solana/{mint}", {},
                                  headers={"User-Agent": "village-fomo-watch"}, timeout=10)
                best = max(pairs, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
                info[mint] = {"name": best["baseToken"].get("name"),
                              "symbol": best["baseToken"].get("symbol"),
                              "liquidity": (best.get("liquidity") or {}).get("usd"),
                              "created": best.get("pairCreatedAt"), "url": best.get("url")}
            except Exception:  # noqa: BLE001 - a token DexScreener has not seen yet
                info[mint] = {}
    readings = wif_reading(trades, now)

    if db is not None:
        for t in trades:
            intel.upsert(db, SOURCE, t["id"][:255],
                         title=f"{t['trader']} {t['action']} {t['mint'][:8]}… ${t['usd']:,.0f}",
                         url=f"https://dexscreener.com/solana/{t['mint']}",
                         score=t["usd"],
                         detail={"trader": t["trader"], "action": t["action"], "mint": t["mint"],
                                 "at": t["at"].isoformat() if t["at"] else None})
        for mint, who in agreed.items():
            meta = info.get(mint) or {}
            intel.upsert(db, "fomo_consensus", mint,
                         title=f"{meta.get('name') or mint[:8]} (${meta.get('symbol') or '?'}): "
                               f"{len(who)} followed traders bought within {WINDOW_HOURS}h",
                         url=meta.get("url") or f"https://dexscreener.com/solana/{mint}",
                         score=len(who), detail={"traders": sorted(who), **meta})
        from src.trading import fleet

        fleet.record(db, SNAPSHOT, {"readings": readings, "trades_read": len(trades)},
                     service="fomo.family", remote_path="followingPaginate + swaps")

    print(f"{len(trades)} swap(s) read, {len(agreed)} consensus token(s), "
          f"{len(readings)} WIF reading(s)" + (" (dry run)" if args.dry_run else ""))
    for mint, who in sorted(agreed.items(), key=lambda kv: -len(kv[1]))[:10]:
        meta = info.get(mint) or {}
        print(f"  {len(who)} traders: {meta.get('name') or mint[:10]} (${meta.get('symbol') or '?'}) "
              f"liq ${float(meta.get('liquidity') or 0):,.0f}")
    for f in failed[:5]:
        print(f"  FAILED {f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
