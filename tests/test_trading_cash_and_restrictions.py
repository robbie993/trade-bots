"""Two behaviours ported from a reviewer's alternative implementation:

* the cash ledger's buckets, named rather than recomputed per call site
* asset restrictions by operator-declared category

Both are covered here because both touch a refusal. The cash buckets decide
what a firm may spend and what the brokerage may take back — the two numbers
whose conflation drove cash negative in the first long simulation — and the
categories decide what the conscience blocks.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.money import D
from src.trading.config import FirmDefaults, HeartConfig
from src.trading.heart.conscience import Conscience
from src.trading.heart.restrictions import AssetRestrictions
from src.trading.models import CashView, FirmRecord, Side, TradeProposal


# =========================================================================
# cash buckets
# =========================================================================
def test_available_excludes_the_reserve_but_withdrawable_does_not():
    """The distinction the whole class exists for.

    The risk manager may not spend the reserve. The brokerage may take it
    back, because a withdrawal lowers the allocation the floor is a fraction
    of, so handing capital back cannot breach it.
    """
    purse = CashView(cash=D("10000"), reserve=D("5000"), in_positions=D("40000"))
    assert purse.available == Decimal("5000.00")
    assert purse.withdrawable == Decimal("10000.00")
    assert purse.equity == Decimal("50000.00")


def test_available_never_goes_negative():
    purse = CashView(cash=D("100"), reserve=D("5000"))
    assert purse.available == Decimal("0")
    assert purse.at_floor is True
    assert purse.withdrawable == Decimal("100.00")


def test_the_store_builds_the_view_from_the_ledger(store, firm_record, market):
    market.seek(150)
    purse = store.cash_view(firm_record, market, cash_floor_pct=D("0.05"))
    assert purse.cash == Decimal("100000.00")
    assert purse.reserve == Decimal("5000.00")
    assert purse.in_positions == Decimal("0.00")
    assert purse.available == Decimal("95000.00")


def test_the_view_marks_open_positions(store, firm_record, market):
    from src.trading.models import Fill

    market.seek(150)
    store.settle(
        firm_record,
        Fill(firm_id=firm_record.id, symbol="SPY", side="buy", quantity="10", price="100", fee="1"),
    )
    firm = store.require_firm_by_id(firm_record.id)
    purse = store.cash_view(firm, market)
    # Quantised after multiplying, not before: rounding the mark first would
    # lose up to a cent per share.
    assert purse.in_positions == (Decimal("10") * market.mark("SPY")).quantize(Decimal("0.01"))
    assert purse.cash == firm.cash


def test_the_risk_manager_sizes_against_available(store):
    """Same numbers as the CashView, reached through the risk manager."""
    from src.trading.firms.risk_manager import RiskManager

    manager = RiskManager(FirmDefaults(cash_floor_pct=D("0.05")))
    firm = FirmRecord(
        firm_key="f",
        allocation=Decimal("100000"),
        initial_allocation=Decimal("100000"),
        cash=Decimal("6000"),  # reserve is 5000, so 1000 is spendable
        risk_limit=Decimal("0.50"),
        universe=["SPY"],
        id=1,
    )
    decision = manager.review(firm, "SPY", Side.BUY, D(50), D(100), [], D(100000))
    assert decision.quantity == Decimal("10")  # 1000 / 100
    assert "cash floor" in decision.reason


def test_the_allocator_withdraws_against_withdrawable(store, gate, trading_config):
    """A firm at its reserve floor can still hand capital back."""
    from src.trading.brokerage.allocator import Allocator
    from src.trading.brokerage.evaluator import Scorecard

    firm = store.upsert_firm(
        FirmRecord(
            firm_key="floored",
            allocation=Decimal("100000"),
            initial_allocation=Decimal("100000"),
            cash=Decimal("4000"),  # below the 5% reserve
            universe=["SPY"],
        )
    )
    allocator = Allocator(store, trading_config.brokerage, gate)
    card = Scorecard(firm_key="floored", firm_id=firm.id, score=D("10"), sufficient_data=True)
    changes = allocator.apply(allocator.plan([card], [firm]))
    assert changes and changes[0].applied
    assert changes[0].delta == Decimal("-4000.00")  # all of it, reserve included
    assert store.require_firm_by_id(firm.id).cash == Decimal("0.00")


# =========================================================================
# asset restrictions
# =========================================================================
@pytest.fixture
def restrictions_file(tmp_path):
    path = tmp_path / "restricted_assets.yaml"
    path.write_text(
        "categories:\n"
        "  weapons:\n"
        "    - LMT\n"
        "    - RTX\n"
        "  tobacco:\n"
        "    - MO\n"
        "  fossil_fuels: [XOM, LMT]\n"
    )
    return path


def test_restrictions_load_into_a_symbol_index(restrictions_file):
    restrictions = AssetRestrictions.load(restrictions_file)
    assert restrictions.categories_for("LMT") == ["fossil_fuels", "weapons"]
    assert restrictions.categories_for("mo") == ["tobacco"]
    assert restrictions.categories_for("SPY") == []
    assert restrictions.categories == {"weapons", "tobacco", "fossil_fuels"}


def test_a_missing_file_restricts_nothing(tmp_path):
    restrictions = AssetRestrictions.load(tmp_path / "absent.yaml")
    assert restrictions.is_empty
    assert restrictions.violations("LMT", ("weapons",)) == []


def test_the_shipped_file_ships_no_classifications():
    """This system has no bundled opinion about what any company does."""
    from src.trading.config import REPO_ROOT

    restrictions = AssetRestrictions.load(REPO_ROOT / "config" / "restricted_assets.yaml")
    assert restrictions.is_empty


def test_violations_are_only_the_enabled_categories(restrictions_file):
    restrictions = AssetRestrictions.load(restrictions_file)
    assert restrictions.violations("LMT", ("weapons",)) == ["weapons"]
    assert restrictions.violations("LMT", ("tobacco",)) == []
    assert sorted(restrictions.violations("LMT", ("weapons", "fossil_fuels"))) == [
        "fossil_fuels",
        "weapons",
    ]


def proposal(symbol="SPY"):
    return TradeProposal(
        firm_id=1, symbol=symbol, side=Side.BUY.value, quantity="1", reference_price="100"
    )


def firm(universe=("SPY", "LMT", "MO")):
    return FirmRecord(
        firm_key="f",
        allocation=Decimal("100000"),
        initial_allocation=Decimal("100000"),
        cash=Decimal("100000"),
        universe=list(universe),
        id=1,
    )


def test_sanctity_blocks_a_restricted_category(market, restrictions_file):
    market.seek(150)
    conscience = Conscience(
        HeartConfig(restricted_categories=("weapons",)),
        AssetRestrictions.load(restrictions_file),
    )
    review = conscience.review(proposal("LMT"), firm(), [], market)
    finding = next(f for f in review.findings if f.foundation == "sanctity")
    assert finding.blocks
    assert "classified weapons" in finding.reason


def test_a_category_listed_but_not_enabled_does_not_block(market, restrictions_file):
    market.seek(150)
    conscience = Conscience(HeartConfig(), AssetRestrictions.load(restrictions_file))
    review = conscience.review(proposal("LMT"), firm(), [], market)
    assert not next(f for f in review.findings if f.foundation == "sanctity").blocks


def test_an_unclassified_symbol_is_reported_as_unclassified(market, restrictions_file):
    """Not 'checked and clear' — the gap has to be visible in the audit trail."""
    market.seek(150)
    conscience = Conscience(
        HeartConfig(restricted_categories=("weapons",)),
        AssetRestrictions.load(restrictions_file),
    )
    review = conscience.review(proposal("SPY"), firm(), [], market)
    finding = next(f for f in review.findings if f.foundation == "sanctity")
    assert not finding.blocks
    assert "unclassified" in finding.reason


def test_a_classified_but_permitted_symbol_says_which_categories(market, restrictions_file):
    market.seek(150)
    conscience = Conscience(
        HeartConfig(restricted_categories=("weapons",)),
        AssetRestrictions.load(restrictions_file),
    )
    review = conscience.review(proposal("MO"), firm(), [], market)
    finding = next(f for f in review.findings if f.foundation == "sanctity")
    assert not finding.blocks
    assert "classified tobacco" in finding.reason


def test_the_symbol_list_still_wins_on_its_own(market):
    market.seek(150)
    conscience = Conscience(HeartConfig(restricted_symbols=("SPY",)), AssetRestrictions())
    review = conscience.review(proposal("SPY"), firm(), [], market)
    assert next(f for f in review.findings if f.foundation == "sanctity").blocks


def test_a_category_block_stops_the_trade_end_to_end(store, firm_record, market, trading_config):
    """The verdict reaches the proposal row, which is what an audit reads."""
    from dataclasses import replace

    from src.trading.heart.ethics import Heart
    from src.trading.models import ProposalStatus

    market.seek(150)
    path = trading_config.audit_vault.parent / "restricted.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("categories:\n  weapons:\n    - SPY\n")
    config = replace(
        trading_config,
        heart=HeartConfig(restricted_categories=("weapons",), restrictions_file=path),
    )
    heart = Heart(store, config)
    p = TradeProposal(
        firm_id=firm_record.id, symbol="SPY", side="buy", quantity="1", reference_price="100"
    )
    heart.consider(p, firm_record, [], market)
    assert p.status == ProposalStatus.BLOCKED.value
    assert "weapons" in p.ethics_reason
