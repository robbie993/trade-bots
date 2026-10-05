"""The congress desk turns disclosed trades into calls, point in time.

Rows are shaped like `political_trade_events` in the Hugging Face dataset
`austin-starks/congressional-stock-trades`, as read on 2026-10-04."""

from datetime import datetime, timedelta, timezone

import pytest

from src.trading.congress import CongressDesk, live_events, loudness, readings_from

NOW = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)


def row(ticker, action, low, high, available, **kw):
    r = {"ticker": ticker, "action": action, "assetTypeCode": "ST",
         "amountLow": low, "amountHigh": high, "availableAt": available,
         "supersededAt": None, "displayName": kw.pop("who", "A Member")}
    r.update(kw)
    return r


FRESH = datetime(2026, 10, 2, 3, 59, 59)          # the dataset's naive UTC stamp


def test_only_fresh_public_unamended_stock_trades_are_live():
    rows = [
        row("HD", "purchase", 1001, 15000, FRESH),
        row("OLD", "purchase", 1001, 15000, FRESH - timedelta(days=5)),
        row("SOON", "purchase", 1001, 15000, NOW + timedelta(hours=1)),
        row("AMND", "purchase", 1001, 15000, FRESH, supersededAt=FRESH + timedelta(hours=1)),
        row("OPT", "purchase", 1001, 15000, FRESH, assetTypeCode="OP"),
        row(None, "purchase", 1001, 15000, FRESH),
        row("XCH", "exchange", 1001, 15000, FRESH),
        row("SEN", "sale", 1001, 15000, FRESH, assetTypeCode=None),
    ]
    assert sorted(r["ticker"] for r in live_events(rows, NOW)) == ["HD", "SEN"]


def test_buys_and_sales_net_with_sales_at_half_weight():
    rows = [
        row("JPM", "purchase", 15001, 50000, FRESH, who="Buyer"),
        row("JPM", "sale", 15001, 50000, FRESH, who="Seller"),
        row("V", "sale", 1001, 15000, FRESH),
    ]
    by = {r.symbol: r for r in readings_from(live_events(rows, NOW))}
    assert by["JPM"].score > 0 and "bought by Buyer" in by["JPM"].note
    assert "sold by Seller" in by["JPM"].note
    assert by["V"].score < 0 and by["V"].confidence == abs(by["V"].score)


def test_loudness_runs_from_floor_to_ceiling():
    assert loudness(500) == 20 and loudness(5_000_000) == 90
    assert 20 < loudness(32_500) < loudness(175_000) < 90


class Board:
    def __init__(self):
        self.published_rows, self.silent = [], 0

    def published(self, name, as_of):
        return False

    def publish(self, name, readings, as_of):
        self.published_rows += readings
        return len(readings)

    def mark_silent(self, name, as_of):
        self.silent += 1


def test_the_desk_publishes_and_survives_a_failing_source(monkeypatch):
    import src.trading.congress as c

    monkeypatch.setattr(c, "read_shard", lambda raw: [row("HD", "purchase", 1001, 15000, FRESH)])
    board = Board()
    notes = CongressDesk(board, get_bytes=lambda url: b"", clock=lambda: 10**6).run(None, as_of=NOW)
    assert [r.symbol for r in board.published_rows] == ["HD"]
    assert any("congress desk" in n for n in notes)

    def boom(url):
        raise OSError("down")

    board = Board()
    notes = CongressDesk(board, get_bytes=boom, clock=lambda: 10**6).run(None, as_of=NOW)
    assert board.published_rows == [] and any("FAILING" in n for n in notes)


def test_a_real_shard_is_read_when_pyarrow_is_there(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import io

    import pyarrow.parquet as pq

    from src.trading.congress import read_shard

    table = pa.table({k: [v] for k, v in row("HD", "purchase", 1001.0, 15000.0, FRESH,
                                             filerLast="X", transactionDate="2026-09-22").items()})
    buf = io.BytesIO()
    pq.write_table(table, buf)
    (r,) = read_shard(buf.getvalue())
    assert r["ticker"] == "HD" and live_events([r], NOW)
