"""Four more ideas from other trading villages and the scam tests (2026-10-05):
the scoreboard against SPY, the too-good-to-be-true alarm, a cap on new
positions a day, and the bull and bear case put in front of each review."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.trading import alarms


@dataclass
class Card:
    firm_key: str
    closed_trades: int
    win_rate_pct: Optional[Decimal]
    return_pct: Decimal
    spy_pct: Optional[Decimal]


def test_a_90_percent_win_rate_on_enough_trades_is_flagged():
    found = alarms.firm_alarms([Card("grid", 40, Decimal("97.5"), Decimal("1"), Decimal("1")),
                                Card("honest", 40, Decimal("55"), Decimal("1"), Decimal("1")),
                                Card("new", 3, Decimal("100"), Decimal("1"), Decimal("1"))])
    assert [a.who for a in found] == ["grid"] and "open positions" in found[0].check


def test_a_big_lead_on_few_trades_is_flagged_and_a_long_record_is_not():
    found = alarms.firm_alarms([Card("lucky", 8, Decimal("60"), Decimal("14"), Decimal("1")),
                                Card("proven", 80, Decimal("60"), Decimal("14"), Decimal("1"))])
    assert [a.who for a in found] == ["lucky"] and "ahead of SPY" in found[0].what


def test_a_scanner_that_almost_never_loses_is_flagged():
    @dataclass
    class Rec:
        publisher: str
        horizon: str
        outside: bool
        closed: int
        won: int

    found = alarms.scanner_alarms([Rec("too_good", "1d", False, 40, 38),
                                   Rec("normal", "1d", False, 40, 20),
                                   Rec("thin", "1h", False, 5, 5)])
    assert [a.who for a in found] == ["too_good (1d)"]


def _firm(opened):
    from src.trading.config import FirmDefaults
    from src.trading.firms.firm import Firm
    from src.trading.models import FirmRecord

    firm = Firm(record=FirmRecord(id=1, firm_key="f", allocation=Decimal("10000"),
                                  cash=Decimal("10000")), analysts=[], limits=FirmDefaults())
    firm.opens_today = (lambda: opened) if opened is not None else None
    return firm


def test_the_trade_cap_stops_new_positions_but_not_exits():
    from src.trading.models import TradeProposal

    firm = _firm(12)
    buy = TradeProposal(firm_id=1, symbol="SPY", side="buy", quantity=Decimal("1"))
    out = firm._review(buy, Decimal("100"), [], Decimal("10000"))
    assert out.status == "rejected" and out.risk_reason.startswith("trade cap")
    sell = TradeProposal(firm_id=1, symbol="SPY", side="sell", quantity=Decimal("1"))
    assert "trade cap" not in (firm._review(sell, Decimal("100"), [], Decimal("10000"))
                               .risk_reason or "")
    assert _firm(11)._opens_cap() == "" and _firm(None)._opens_cap() == ""


def test_a_review_carries_the_case_each_loser_was_bought_on(db):
    from src.trading.ask import _recent_losses
    from src.trading.models import FirmRecord

    firm_id = db.insert("firms", {"firm_key": "f", "name": "f"})
    pid = db.insert("trade_proposals", {"firm_id": firm_id, "symbol": "JNJ", "side": "buy",
                                        "bull_case": "RSI oversold", "bear_case": "trend down",
                                        "debate_winner": "bull"})
    sell_id = db.insert("trade_proposals", {"firm_id": firm_id, "symbol": "JNJ", "side": "sell"})
    db.insert("fills", {"firm_id": firm_id, "symbol": "JNJ", "side": "sell",
                        "realized_pnl": "-42.10", "proposal_id": sell_id,
                        "as_of": "2026-10-05T14:00:00Z"})
    (loss,) = _recent_losses(db, FirmRecord(firm_key="f", id=firm_id))
    assert loss["symbol"] == "JNJ" and loss["bull_case"] == "RSI oversold"
    assert loss["bear_case"] == "trend down" and loss["debate_winner"] == "bull"
