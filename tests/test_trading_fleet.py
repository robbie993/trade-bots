"""The live fleet, carried into the ledger and back out to the scanners.

Written when the village moved to Railway and every fleet scanner went silent:
they read `data/fleet/*.json`, and the worker's container never had those files.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.trading import fleet

BOOK = Path(__file__).resolve().parents[1] / "bots" / "fleet_book.py"


def _load_book(snapshot_path):
    spec = importlib.util.spec_from_file_location("fleet_book_under_test", BOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SNAPSHOT = snapshot_path
    return mod


def _account(positions=(), equity="111000.00", last="110000.00"):
    return {"account": "PA-TEST", "equity": equity, "last_equity": last,
            "cash": "5000", "positions": list(positions)}


def test_record_keeps_only_the_recent_history(db, monkeypatch):
    monkeypatch.setattr(fleet, "KEEP_PER_SOURCE", 3)
    for i in range(5):
        fleet.record(db, "scanner", {"n": i})
    rows = db.query("SELECT payload FROM fleet_snapshots WHERE source = 'scanner'")
    assert [json.loads(r["payload"])["n"] for r in rows] == [2, 3, 4]


def test_latest_is_the_newest_row_per_source(db):
    fleet.record(db, "scanner", {"n": 1})
    fleet.record(db, "form4", {"n": 7})
    fleet.record(db, "scanner", {"n": 2})
    snaps = fleet.latest(db)
    assert snaps["scanner"]["payload"] == {"n": 2}
    assert snaps["form4"]["payload"] == {"n": 7}


def test_materialize_writes_what_the_scanners_read(db, tmp_path):
    fleet.record(db, "scanner", {"day": "2026-09-25", "picks": []},
                 fetched_at="2026-09-25T14:00:00+00:00")
    out = tmp_path / "fleet"
    assert fleet.materialize(db, out) == ["scanner"]
    on_disk = json.loads((out / "scanner.json").read_text())
    assert on_disk["fetched_at"] == "2026-09-25T14:00:00+00:00"
    assert on_disk["payload"]["day"] == "2026-09-25"


def test_materialize_never_replaces_a_newer_file_with_an_older_row(db, tmp_path):
    """A Mac that synced to its own disk a minute ago must not be rolled back."""
    out = tmp_path / "fleet"
    out.mkdir()
    (out / "scanner.json").write_text(json.dumps(
        {"fetched_at": "2026-09-25T15:00:00+00:00", "payload": {"fresh": True}}))
    fleet.record(db, "scanner", {"fresh": False}, fetched_at="2026-09-25T14:00:00+00:00")
    assert fleet.materialize(db, out) == []
    assert json.loads((out / "scanner.json").read_text())["payload"] == {"fresh": True}


def test_materialize_on_an_unmigrated_ledger_does_nothing(tmp_path):
    from src.db.connection import Database

    bare = Database.from_url(f"sqlite:///{tmp_path / 'bare.db'}")
    assert fleet.materialize(bare, tmp_path / "fleet") == []
    bare.close()


def test_no_credentials_means_no_account_read(monkeypatch, db):
    for name in ("ALPACA_API_KEY_ID", "APCA_API_KEY_ID",
                 "ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)

    def boom(*a, **k):
        raise AssertionError("must not reach the network without keys")

    assert fleet.snapshot_account(db, get_json=boom) is None


def test_the_account_is_read_and_stored(monkeypatch, db):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    calls = []

    def fake(url, params, headers=None):
        calls.append(url)
        assert headers["APCA-API-KEY-ID"] == "k"
        if url.endswith("/v2/account"):
            return {"account_number": "PA-TEST", "equity": "111000", "last_equity": "110000",
                    "cash": "1"}
        return [{"symbol": "NVDA", "side": "long", "qty": "3", "asset_class": "us_equity",
                 "market_value": "500", "cost_basis": "450", "unrealized_pl": "50",
                 "unrealized_plpc": "0.11"}]

    note = fleet.snapshot_account(db, get_json=fake)
    assert "PA-TEST" in note and "1 position" in note
    assert all(u.startswith("https://paper-api.alpaca.markets") for u in calls)
    stored = fleet.latest(db)[fleet.ACCOUNT_SOURCE]["payload"]
    assert stored["positions"][0]["symbol"] == "NVDA"


def test_comparison_shows_both_returns_on_their_own_capital(db):
    fleet.record(db, fleet.ACCOUNT_SOURCE, _account(
        [{"unrealized_pl": "100"}, {"unrealized_pl": "-40"}]))
    c = fleet.comparison(db, village_capital=200_000, village_equity=202_000)
    assert round(c["fleet_return_pct"], 2) == 11.0
    assert round(c["village_return_pct"], 2) == 1.0
    assert c["fleet_positions"] == 2 and c["fleet_unrealized"] == 60.0


def test_comparison_with_no_account_is_none(db):
    assert fleet.comparison(db, 1, 1) is None


def _write_account_snapshot(path, positions, fetched_at=None):
    path.write_text(json.dumps({
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "payload": _account(positions),
    }))


def test_fleet_book_votes_the_direction_the_fleet_holds(tmp_path):
    snap = tmp_path / "alpaca_account.json"
    _write_account_snapshot(snap, [
        {"symbol": "NVDA", "side": "long", "asset_class": "us_equity", "unrealized_plpc": "0.2"},
        {"symbol": "TSLA", "side": "short", "asset_class": "us_equity", "unrealized_plpc": "-0.1"},
        {"symbol": "BTCUSD", "side": "long", "asset_class": "crypto", "unrealized_plpc": "0"},
        {"symbol": "AAPL261016C00200000", "side": "long", "asset_class": "us_option"},
    ])
    readings = {r["symbol"]: r for r in _load_book(snap).scan(None)}
    assert readings["NVDA"]["score"] > 0
    assert readings["TSLA"]["score"] < 0
    assert "BTC-USD" in readings
    assert not any(k.startswith("AAPL2") for k in readings)


def test_fleet_book_does_not_turn_an_open_gain_into_conviction(tmp_path):
    snap = tmp_path / "alpaca_account.json"
    _write_account_snapshot(snap, [
        {"symbol": "A", "side": "long", "asset_class": "us_equity", "unrealized_plpc": "3.0"},
        {"symbol": "B", "side": "long", "asset_class": "us_equity", "unrealized_plpc": "0.0"},
    ])
    r = {x["symbol"]: x for x in _load_book(snap).scan(None)}
    assert r["A"]["score"] == r["B"]["score"]
    assert r["A"]["confidence"] == r["B"]["confidence"]


def test_fleet_book_is_silent_on_a_stale_or_missing_snapshot(tmp_path):
    snap = tmp_path / "alpaca_account.json"
    assert _load_book(snap).scan(None) == {}
    old = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    _write_account_snapshot(snap, [{"symbol": "NVDA", "side": "long",
                                    "asset_class": "us_equity"}], fetched_at=old)
    assert _load_book(snap).scan(None) == {}


def test_a_synthetic_village_never_reads_the_brokerage(ecosystem, monkeypatch):
    """The operator's shell may hold real keys; a test tick must not use them."""
    monkeypatch.setenv("ALPACA_API_KEY_ID", "real-looking")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "real-looking")

    def boom(*a, **k):
        raise AssertionError("a synthetic village reached the brokerage")

    monkeypatch.setattr(fleet, "read_account", boom)
    ecosystem.tick()
