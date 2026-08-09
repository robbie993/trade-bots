"""Tokens, titles and bouts — a scoreboard that cannot reach the capital."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.money import D
from src.trading.brokerage.evaluator import Scorecard
from src.trading.competition import (
    Arena,
    InsufficientTokens,
    TITLES,
    TokenLedger,
    title_for,
)
from src.trading.models import FirmRecord


@pytest.fixture
def firms(store):
    return [
        store.upsert_firm(
            FirmRecord(
                firm_key=key,
                allocation=Decimal("100000"),
                initial_allocation=Decimal("100000"),
                cash=Decimal("100000"),
                universe=["SPY"],
            )
        )
        for key in ("alpha", "beta")
    ]


@pytest.fixture
def tokens(db):
    return TokenLedger(db)


@pytest.fixture
def arena(db, tokens):
    return Arena(db, tokens=tokens)


def card(firm_key, firm_id=None, **overrides):
    defaults = dict(
        firm_key=firm_key,
        firm_id=firm_id,
        score=D(50),
        return_pct=D(0),
        drawdown_pct=D(0),
        closed_trades=30,
        sufficient_data=True,
    )
    defaults.update(overrides)
    return Scorecard(**defaults)


# =========================================================================
# the token ledger
# =========================================================================
def test_nothing_converts_tokens_into_capital(firms, tokens, store):
    """The property the whole module exists to preserve."""
    tokens.award("alpha", 100_000, "a fortune in points")
    assert store.get_firm("alpha").allocation == Decimal("100000.00")
    assert store.get_firm("alpha").cash == Decimal("100000.00")
    # There is no method that would do it, either.
    assert not [name for name in dir(tokens) if "alloc" in name or "capital" in name]


def test_awards_and_deductions_are_explainable_line_by_line(firms, tokens):
    tokens.award("alpha", 100, "won a bout")
    tokens.deduct("alpha", 30, "lost a bout")
    balance = tokens.balance("alpha")
    assert balance.balance == Decimal("70.00")
    assert balance.lifetime_earned == Decimal("100.00")
    events = tokens.events("alpha")
    assert [e["reason"] for e in events] == ["lost a bout", "won a bout"]
    assert [Decimal(str(e["amount"])) for e in events] == [Decimal("-30"), Decimal("100")]


def test_a_balance_floors_at_zero(firms, tokens):
    tokens.award("alpha", 10, "a little")
    tokens.deduct("alpha", 500, "a lot")
    assert tokens.balance("alpha").balance == Decimal("0.00")


def test_spending_more_than_you_have_is_refused(firms, tokens):
    tokens.award("alpha", 10, "a little")
    with pytest.raises(InsufficientTokens):
        tokens.spend("alpha", 500, "something expensive")
    assert tokens.balance("alpha").balance == Decimal("10.00")


def test_titles_follow_lifetime_earnings_not_balance(firms, tokens):
    tokens.award("alpha", 800, "a good season")
    tokens.deduct("alpha", 800, "a bad one")
    balance = tokens.balance("alpha")
    assert balance.balance == Decimal("0.00")
    assert balance.title == "Senior Trader"  # earned is what counts


def test_the_title_ladder():
    assert title_for(0) == "Novice"
    assert title_for(D("99.99")) == "Novice"
    assert title_for(100) == "Apprentice"
    assert title_for(999_999) == "Trading God"
    assert [name for _, name in TITLES][0] == "Novice"


def test_reputation_is_bounded(firms, tokens):
    tokens.adjust_reputation("alpha", -500, "disgrace")
    assert tokens.balance("alpha").reputation == Decimal("0.00")
    tokens.adjust_reputation("alpha", 5000, "redemption")
    assert tokens.balance("alpha").reputation == Decimal("200.00")


# =========================================================================
# bouts
# =========================================================================
def test_a_bout_awards_the_winner_and_docks_the_loser(firms, arena, tokens):
    cards = [card("alpha", firms[0].id, score=D(70)), card("beta", firms[1].id, score=D(40))]
    fight = arena.bout("alpha", "beta", cards)
    assert fight.winner == "alpha" and fight.decided
    assert tokens.balance("alpha").balance == Decimal("20.00")
    assert tokens.balance("beta").balance == Decimal("0.00")  # floored, was 0


def test_a_bout_below_the_sample_gate_is_no_contest(firms, arena, tokens):
    cards = [
        card("alpha", firms[0].id, score=D(70)),
        card("beta", firms[1].id, score=D(40), sufficient_data=False),
    ]
    fight = arena.bout("alpha", "beta", cards)
    assert not fight.decided
    assert "sample gate" in fight.reason
    assert tokens.balance("alpha").balance == Decimal("0.00")


def test_drawdown_bouts_are_won_by_the_lower_number(firms, arena):
    cards = [
        card("alpha", firms[0].id, drawdown_pct=D(15)),
        card("beta", firms[1].id, drawdown_pct=D(3)),
    ]
    assert arena.bout("alpha", "beta", cards, "drawdown").winner == "beta"


def test_an_unknown_metric_is_refused(firms, arena):
    with pytest.raises(ValueError):
        arena.bout("alpha", "beta", [], "vibes")


def test_a_missing_metric_is_no_contest(firms, arena):
    cards = [
        card("alpha", firms[0].id, sharpe=None),
        card("beta", firms[1].id, sharpe=D(1)),
    ]
    assert not arena.bout("alpha", "beta", cards, "sharpe").decided


def test_a_draw_is_not_a_win(firms, arena, tokens):
    cards = [card("alpha", firms[0].id, score=D(50)), card("beta", firms[1].id, score=D(50))]
    fight = arena.bout("alpha", "beta", cards)
    assert not fight.decided and fight.reason == "drawn"


def test_round_robin_pairs_everyone_once(firms, arena):
    cards = [card("alpha", firms[0].id, score=D(70)), card("beta", firms[1].id, score=D(40))]
    assert len(arena.round_robin(cards)) == 1
    assert len(arena.bouts()) == 1


def test_bouts_record_the_numbers_that_decided_them(firms, arena):
    cards = [card("alpha", firms[0].id, score=D(70)), card("beta", firms[1].id, score=D(40))]
    arena.bout("alpha", "beta", cards)
    row = arena.bouts()[0]
    assert Decimal(str(row["challenger_score"])) == Decimal("70")
    assert Decimal(str(row["opponent_score"])) == Decimal("40")
    assert row["metric"] == "score"


# =========================================================================
# milestones
# =========================================================================
def test_milestones_are_awarded_once(firms, arena, tokens):
    cards = [card("alpha", firms[0].id, return_pct=D(12), drawdown_pct=D(2), sharpe=D(2))]
    first = arena.award_milestones(cards)
    assert first
    assert arena.award_milestones(cards) == []
    assert tokens.balance("alpha").balance > 0


def test_milestones_skip_firms_below_the_gate(firms, arena):
    cards = [card("alpha", firms[0].id, return_pct=D(50), sufficient_data=False)]
    assert arena.award_milestones(cards) == []


def test_the_standings_say_tokens_are_not_capital(firms, arena, tokens):
    tokens.award("alpha", 100, "x")
    rendered = arena.render()
    assert "alpha" in rendered
    assert "not capital" in rendered
