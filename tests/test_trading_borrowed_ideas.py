"""Two ideas taken from repositories the repo scout found, re-written here.

* the daily loss halt (warrenduffer, kairos): a firm down a set fraction on the
  day opens nothing new until tomorrow, and can still exit;
* the look-ahead lint (peekproof): the strategy court names the pandas idioms
  that hand a backtest tomorrow's price.
"""

from decimal import Decimal

from src.trading.court.evidence import gather


def _firm(day_open):
    from src.trading.config import FirmDefaults
    from src.trading.firms.firm import Firm
    from src.trading.models import FirmRecord

    record = FirmRecord(id=1, firm_key="f", name="f", allocation=Decimal("10000"),
                        cash=Decimal("10000"))
    firm = Firm(record=record, analysts=[], limits=FirmDefaults())
    firm.day_open_equity = (lambda: day_open) if day_open is not None else None
    return firm


def test_the_halt_trips_past_the_limit_and_not_before():
    firm = _firm(Decimal("10000"))
    assert firm._daily_loss_halt(Decimal("9800")) == ""            # down 2%
    reason = firm._daily_loss_halt(Decimal("9650"))                 # down 3.5%
    assert reason.startswith("daily loss halt") and "exits only" in reason


def test_no_history_means_no_halt():
    assert _firm(None)._daily_loss_halt(Decimal("1")) == ""
    assert _firm(Decimal("0"))._daily_loss_halt(Decimal("1")) == ""


def test_a_halted_firm_still_exits_and_does_not_open():
    from src.trading.models import TradeProposal

    firm = _firm(Decimal("10000"))
    buy = TradeProposal(firm_id=1, symbol="SPY", side="buy", quantity=Decimal("1"))
    sell = TradeProposal(firm_id=1, symbol="SPY", side="sell", quantity=Decimal("1"))
    blocked = firm._review(buy, Decimal("100"), [], Decimal("9000"))
    assert blocked.status == "rejected" and "daily loss halt" in blocked.risk_reason
    out = firm._review(sell, Decimal("100"), [], Decimal("9000"))
    assert "daily loss halt" not in (out.risk_reason or "")


def test_the_court_names_look_ahead(tmp_path):
    f = tmp_path / "peeky.py"
    f.write_text(
        "GENOME = {'rsi_entry': 30}\n"
        "def scan(df):\n"
        "    df['next'] = df.close.shift(-1)\n"
        "    df['smooth'] = df.close.rolling(5, center=True).mean()\n"
        "    df = df.bfill()\n"
        "    x = df.close.iloc[i + 1]\n"
        "    ok = df.close.shift(1)\n"
    )
    ev = gather(f)
    assert len(ev.lookahead) == 4 and all("line" in s for s in ev.lookahead)
    assert any("reads the future" in n for n in ev.notes)


def test_honest_code_is_not_flagged(tmp_path):
    f = tmp_path / "honest.py"
    f.write_text("GENOME = {'rsi_entry': 30}\n"
                 "def scan(df):\n    return df.close.shift(1).rolling(5).mean().iloc[-1]\n")
    assert gather(f).lookahead == []
