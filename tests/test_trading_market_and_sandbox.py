"""The black market and the sandbox — the two features that had to be fenced.

The market's fence: capital never settles inside it. A capital sale is a cut
plus a raise, and a raise needs a human, so the market requests one and stops.
Without that it would be a route around the only gate in the system.

The sandbox's fence: it holds a read-only view of the ledger and can write
exactly two tables. Sabotage moves a shadow number, never a firm's books,
because a drawdown has to stay a fact about that firm's own strategy or no
kill reason means anything.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.agents.human_gate import ApprovalAction
from src.trading.black_market import BlackMarket, MarketError
from src.trading.brokerage.reconciliation import Reconciler
from src.trading.competition import TokenLedger
from src.trading.models import Fill, FirmRecord, Side
from src.trading.sandbox import Sandbox, SandboxViolation


@pytest.fixture
def firms(store):
    return [
        store.upsert_firm(
            FirmRecord(
                firm_key=key,
                allocation=Decimal("100000"),
                initial_allocation=Decimal("100000"),
                cash=Decimal("100000"),
                universe=["SPY", "QQQ"],
                genome={"fast_window": 10, "slow_window": 30, "trend_bias": 60},
            )
        )
        for key in ("alpha", "beta", "gamma")
    ]


@pytest.fixture
def tokens(db, firms):
    ledger = TokenLedger(db)
    for key in ("alpha", "beta", "gamma"):
        ledger.award(key, 1000, "seed")
    return ledger


@pytest.fixture
def market(store, trading_config, tokens, gate):
    return BlackMarket(store, trading_config, tokens, gate)


@pytest.fixture
def sandbox(store, tokens, firms):
    return Sandbox(store, tokens)


# =========================================================================
# the market: token listings
# =========================================================================
def test_a_genome_licence_always_mutates(market, tokens):
    """A licence buys a starting point, never a copy.

    If firms could buy exact copies they would converge, three pods would run
    one strategy under three names, and the leaderboard would stop measuring
    three things.
    """
    for index in range(5):
        listing = market.list_asset("alpha", "genome", 50, title=f"v{index}")
        delivery = market.buy("beta", listing.id)["delivery"]
        assert delivery["delivered_variant"] != delivery["seller_genome"]


def test_a_licensed_genome_arrives_unselected(market, store):
    listing = market.list_asset("alpha", "genome", 50)
    genome_id = market.buy("beta", listing.id)["delivery"]["genome_id"]
    row = store.db.query_one("SELECT * FROM strategy_genomes WHERE id = ?", (genome_id,))
    assert not bool(row["selected"])
    assert "licensed from alpha" in row["notes"]
    # And the buyer's own genome is untouched.
    assert store.get_firm("beta").genome == {
        "fast_window": 10, "slow_window": 30, "trend_bias": 60
    }


def test_tokens_move_from_buyer_to_seller(market, tokens):
    listing = market.list_asset("alpha", "genome", 200)
    market.buy("beta", listing.id)
    assert tokens.balance("alpha").balance == Decimal("1200.00")
    assert tokens.balance("beta").balance == Decimal("800.00")


def test_a_buyer_without_tokens_is_refused(market, tokens):
    tokens.deduct("beta", 1000, "broke")
    listing = market.list_asset("alpha", "genome", 200)
    with pytest.raises(MarketError):
        market.buy("beta", listing.id)
    assert market.listing(listing.id).status == "active"


def test_a_failed_delivery_refunds_the_escrow(market, tokens, store):
    listing = market.list_asset("alpha", "genome", 300)
    # Remove the genome from the listing after it was posted.
    store.db.update("market_listings", listing.id, {"details": "{}"})
    before = tokens.balance("beta").balance
    with pytest.raises(MarketError):
        market.buy("beta", listing.id)
    assert tokens.balance("beta").balance == before
    assert tokens.balance("alpha").reputation < Decimal("100")


def test_data_and_compute_listings_say_what_they_are(market):
    listing = market.list_asset("alpha", "compute", 40, details={"cores": 8})
    delivery = market.buy("beta", listing.id)["delivery"]
    assert "no resource is transferred by this system" in delivery["note"]


def test_a_firm_cannot_buy_its_own_listing(market):
    listing = market.list_asset("alpha", "genome", 10)
    with pytest.raises(MarketError):
        market.buy("alpha", listing.id)


def test_unknown_asset_types_are_refused(market):
    with pytest.raises(MarketError):
        market.list_asset("alpha", "insider_tips", 10)


# =========================================================================
# the market: capital
# =========================================================================
def test_a_capital_sale_moves_nothing_until_a_human_approves(market, store, gate):
    listing = market.list_asset("alpha", "capital", 20000)
    result = market.buy("beta", listing.id)
    assert result["settled"] is False
    assert store.get_firm("alpha").allocation == Decimal("100000.00")
    assert store.get_firm("beta").allocation == Decimal("100000.00")
    approval = gate.get(result["approval_id"])
    assert approval.action == ApprovalAction.ALLOCATE_CAPITAL.value
    assert approval.is_pending


def test_settling_before_approval_is_refused(market, gate):
    listing = market.list_asset("alpha", "capital", 20000)
    result = market.buy("beta", listing.id)
    with pytest.raises(MarketError) as exc:
        market.settle_capital_transfer(result["transaction"])
    assert "until a human approves it" in str(exc.value)


def test_an_approved_transfer_applies_both_legs(market, store, gate):
    listing = market.list_asset("alpha", "capital", 20000)
    result = market.buy("beta", listing.id)
    gate.approve(result["approval_id"], approved_by="robbie")
    market.settle_capital_transfer(result["transaction"])
    assert store.get_firm("alpha").allocation == Decimal("80000.00")
    assert store.get_firm("alpha").cash == Decimal("80000.00")
    assert store.get_firm("beta").allocation == Decimal("120000.00")
    assert store.get_firm("beta").cash == Decimal("120000.00")


def test_a_transfer_leaves_the_books_reconciled(market, store, gate, market_data, trading_config):
    listing = market.list_asset("alpha", "capital", 20000)
    result = market.buy("beta", listing.id)
    gate.approve(result["approval_id"], approved_by="robbie")
    market.settle_capital_transfer(result["transaction"])
    report = Reconciler(store, trading_config.brokerage).check(store.firms(), market_data)
    assert report.ok, report.summary()


def test_a_transfer_settles_only_once(market, gate):
    listing = market.list_asset("alpha", "capital", 20000)
    result = market.buy("beta", listing.id)
    gate.approve(result["approval_id"], approved_by="robbie")
    market.settle_capital_transfer(result["transaction"])
    with pytest.raises(MarketError):
        market.settle_capital_transfer(result["transaction"])


def test_capital_that_is_invested_cannot_be_listed(market, store):
    firm = store.get_firm("alpha")
    store.settle(
        firm,
        Fill(firm_id=firm.id, symbol="SPY", side=Side.BUY.value, quantity="950",
             price="100", fee="1"),
    )
    with pytest.raises(MarketError) as exc:
        market.list_asset("alpha", "capital", 50000)
    assert "uninvested" in str(exc.value)


# =========================================================================
# the sandbox guard
# =========================================================================
def test_the_sandbox_cannot_write_the_ledger(sandbox):
    for call in (
        lambda: sandbox.reader.settle(None, None),
        lambda: sandbox.reader.set_allocation(1, 1, 1),
        lambda: sandbox.reader.set_firm_status(1, "killed"),
        lambda: sandbox.reader.update_firm_fields(1, cash=0),
        lambda: sandbox.reader.record_proposal(None),
    ):
        with pytest.raises(SandboxViolation):
            call()


def test_the_sandbox_cannot_reach_raw_sql(sandbox):
    """`db` is not on the allow-list, so there is no way around the wrapper."""
    with pytest.raises(SandboxViolation):
        sandbox.reader.db


def test_the_sandbox_writer_is_limited_to_two_tables(sandbox):
    for table in ("firms", "fills", "positions", "strategy_genomes", "human_approvals"):
        with pytest.raises(SandboxViolation):
            sandbox.writer.insert(table, {"x": 1})
    # Its own tables are fine.
    sandbox.writer.insert("sandbox_events", {"event_type": "test", "actor": "alpha"})


def test_the_sandbox_can_read_everything(sandbox):
    assert len(sandbox.reader.firms()) == 3
    assert sandbox.reader.get_firm("alpha").firm_key == "alpha"
    assert sandbox.reader.positions(1) == []


# =========================================================================
# alliances and intrigue
# =========================================================================
def test_forming_an_alliance_needs_two_known_firms(sandbox):
    with pytest.raises(SandboxViolation):
        sandbox.form("Solo", "alpha", [])
    with pytest.raises(SandboxViolation):
        sandbox.form("Ghosts", "alpha", ["nobody"])


def test_betrayal_pays_tokens_and_costs_reputation(sandbox, tokens):
    sandbox.form("The Pact", "alpha", ["beta", "gamma"])
    before = tokens.balance("alpha")
    sandbox.betray("alpha", "The Pact")
    after = tokens.balance("alpha")
    assert after.balance == before.balance + Decimal("100.00")
    assert after.reputation < before.reputation
    assert sandbox.alliances.require("The Pact").status == "broken"


def test_only_a_member_can_betray(sandbox):
    sandbox.form("The Pact", "alpha", ["beta"])
    with pytest.raises(SandboxViolation):
        sandbox.betray("gamma", "The Pact")


def test_espionage_records_the_genome_but_installs_nothing(sandbox, store):
    before = store.get_firm("beta").genome
    event = sandbox.spy("beta", "alpha")
    assert event.payload["stolen_genome"] == store.get_firm("alpha").genome
    assert "strategy court" in event.detail
    # The thief's own genome is unchanged, and no candidate was created.
    assert store.get_firm("beta").genome == before
    assert store.db.query("SELECT * FROM strategy_genomes") == []


def test_sabotage_never_touches_the_real_ledger(sandbox, store):
    before = {
        f.firm_key: (f.allocation, f.cash, f.status, f.high_water_mark)
        for f in store.firms()
    }
    sandbox.sabotage("alpha", "beta")
    after = {
        f.firm_key: (f.allocation, f.cash, f.status, f.high_water_mark)
        for f in store.firms()
    }
    assert before == after
    assert store.fills() == []


def test_sabotage_moves_the_shadow_scoreboard(sandbox):
    before = sandbox.intrigue.shadow_equity("beta")
    sandbox.sabotage("alpha", "beta")
    assert sandbox.intrigue.shadow_equity("beta") < before


def test_every_sandbox_event_records_no_real_effect(sandbox):
    sandbox.form("The Pact", "alpha", ["beta", "gamma"])
    sandbox.spy("beta", "gamma")
    sandbox.betray("alpha", "The Pact")
    sandbox.sabotage("gamma", "beta")
    events = sandbox.events(50)
    assert len(events) >= 4
    assert all(not bool(e["real_effect"]) for e in events)


def test_intrigue_costs_tokens_and_can_be_refused(sandbox, tokens):
    tokens.deduct("alpha", 1000, "broke")
    with pytest.raises(Exception):
        sandbox.spy("alpha", "beta")


def test_a_firm_cannot_target_itself(sandbox):
    with pytest.raises(SandboxViolation):
        sandbox.spy("alpha", "alpha")


def test_intrigue_is_deterministic(store, tokens, firms):
    """Same seed, same outcome — the game replays like everything else."""
    outcomes = []
    for _ in range(2):
        box = Sandbox(store, tokens, seed=4242)
        outcomes.append(box.intrigue._roll("spy:alpha:beta"))
    assert outcomes[0] == outcomes[1]


def test_the_scoreboard_is_labelled_as_shadow(sandbox):
    assert "never the real ledger" in sandbox.render()
