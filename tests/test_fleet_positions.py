"""Each fleet bot's holdings become a named vote; a split fleet says nothing;
a bot whose sync stopped is left out without silencing the rest.

The payloads below are the shapes the live bots write, copied from their state
files on 2026-09-25 and trimmed."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod(tmp_path):
    spec = importlib.util.spec_from_file_location("fleet_positions_ut", ROOT / "bots" / "fleet_positions.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.FLEET = tmp_path
    return m


def _snap(tmp_path, bot, payload, hours_old=0.1):
    at = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    (tmp_path / f"{bot}.json").write_text(json.dumps({"fetched_at": at.isoformat(), "payload": payload}))


def test_each_bot_shape_is_read(tmp_path):
    m = _mod(tmp_path)
    assert m.holdings("btcc", {"APT": {"side": "LONG"}, "LTC": {"side": "SHORT"}}) == {"APT-USD": 1, "LTC-USD": -1}
    assert m.holdings("coinbase", {"BTC-USD": {"qty": 1}}) == {"BTC-USD": 1}
    assert m.holdings("atlas", {"weights": {"SOL-USD": 0.13, "DOGE-USD": -0.16, "X-USD": 0.0}}) == {"SOL-USD": 1, "DOGE-USD": -1}
    assert m.holdings("picks_trader", {"positions": {"gme": {"qty": 42}}}) == {"GME": 1}


def test_agreement_votes_and_a_split_says_nothing(tmp_path):
    m = _mod(tmp_path)
    _snap(tmp_path, "atlas", {"weights": {"SOL-USD": 0.1, "ETH-USD": 0.17}})
    _snap(tmp_path, "supercrypto", {"weights": {"SOL-USD": 0.005, "ETH-USD": -0.017}})
    out = {r["symbol"]: r for r in m.scan(None)}
    assert out["SOL-USD"]["score"] == 40.0 and out["SOL-USD"]["confidence"] == 40
    assert "atlas long" in out["SOL-USD"]["note"] and "supercrypto long" in out["SOL-USD"]["note"]
    assert "ETH-USD" not in out


def test_a_bot_whose_sync_stopped_is_left_out(tmp_path):
    m = _mod(tmp_path)
    _snap(tmp_path, "atlas", {"weights": {"SOL-USD": 0.1}}, hours_old=10)
    _snap(tmp_path, "coinbase", {"BTC-USD": {"qty": 1}})
    assert [r["symbol"] for r in m.scan(None)] == ["BTC-USD"]
