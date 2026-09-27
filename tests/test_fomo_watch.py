"""The fomo watcher: buys and sells told apart, consensus found, WIF voted."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("fomo_watch_ut", Path(__file__).resolve().parents[1] / "scripts" / "fomo_watch.py")
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)

SOL = "So11111111111111111111111111111111111111112"
NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def test_a_swap_from_sol_is_a_buy_and_to_sol_a_sell():
    base = {"inNetworkId": fw.SOLANA, "outNetworkId": fw.SOLANA, "humanUsdAmountIn": "250"}
    assert fw.classify({**base, "inTokenAddress": SOL, "outTokenAddress": "Coin1"}) == ("buy", "Coin1", 250.0)
    assert fw.classify({**base, "inTokenAddress": "Coin1", "outTokenAddress": SOL}) == ("sell", "Coin1", 250.0)
    assert fw.classify({**base, "inTokenAddress": "A", "outTokenAddress": "B"}) is None


def _t(trader, action, mint, hours_ago):
    return {"trader": trader, "action": action, "mint": mint, "usd": 100,
            "at": NOW - timedelta(hours=hours_ago), "id": f"{trader}{mint}{hours_ago}"}


def test_consensus_needs_distinct_traders_inside_the_window():
    trades = [_t("a", "buy", "X", 1), _t("a", "buy", "X", 2), _t("b", "buy", "X", 3),
              _t("c", "buy", "Y", 1), _t("d", "buy", "Y", 20)]
    assert fw.consensus(trades, NOW) == {"X": {"a", "b"}}


def test_followed_traders_on_wif_become_one_reading():
    trades = [_t("a", "buy", fw.WIF_MINT, 1), _t("b", "buy", fw.WIF_MINT, 2),
              _t("c", "sell", fw.WIF_MINT, 3)]
    (r,) = fw.wif_reading(trades, NOW)
    assert r["symbol"] == "WIF-USD" and r["score"] > 0 and r["confidence"] == 12
