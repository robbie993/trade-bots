"""One decision per bar, however often the loop runs.

The loop ticks every sixty seconds and the bar changes every hour. Without a
guard a firm re-derives the same conclusion from the same prices up to sixty
times and acts on it every time — which is not sixty decisions, it is one
decision executed until the money runs out.

634 of the village's first 666 fills were repeats of the same firm, symbol,
side and bar. The commodities desk decided once to buy USO and bought it seven
times in nine minutes: a $2,101 position became $13,867, and no rule was
breached, because each of the seven was individually inside every limit.
"""

from decimal import Decimal

import pytest

from src.money import D
from src.trading.models import FirmStatus


def _tick_twice(eco):
    first = eco.tick()
    second = eco.tick()
    return first, second


def test_a_second_tick_on_the_same_bar_proposes_nothing(ecosystem):
    """The cadence guard, stated as the thing it prevents."""
    eco = ecosystem
    first, second = _tick_twice(eco)
    assert first.proposals > 0, "fixture should produce a first opinion"
    assert second.proposals == 0, (
        f"the same bar produced {second.proposals} more proposal(s) — "
        "one decision is being executed twice"
    )


def test_the_guard_counts_blocked_proposals_too(ecosystem):
    """A firm that was refused has still had its say.

    Blocked proposals were most of the churn — 46,513 proposals produced 666
    fills — so a guard that only looked at fills would let a blocked firm
    re-ask sixty times an hour and change nothing about the cost.
    """
    eco = ecosystem
    eco.tick()
    resolution = eco.config.data.resolution
    market = eco.market()
    bar = resolution.bar_key(market.as_of())
    for record in eco.store.firms():
        rows = eco.db.query(
            "SELECT as_of, risk_verdict FROM trade_proposals WHERE firm_id = ?",
            (record.id,),
        )
        if not rows:
            continue
        assert eco._already_deliberated(record, bar, resolution), (
            f"{record.firm_key} proposed on this bar but the guard did not see it"
        )


def test_a_new_bar_lets_every_firm_speak_again(ecosystem):
    """The guard must gate on the bar, not silence the firm permanently."""
    eco = ecosystem
    first, second = _tick_twice(eco)
    assert second.proposals == 0

    # Advance the world by one bar: the guard compares bar keys, so a market
    # standing on a later bar is a market every firm may speak to again.
    market = eco.market()
    market.seek(market.length() - 1)
    resolution = eco.config.data.resolution
    moved = eco.market()
    moved.seek(max(0, moved.length() - 2))
    earlier = resolution.bar_key(moved.as_of())
    later = resolution.bar_key(market.as_of())
    assert earlier != later, "fixture data should span more than one bar"

    for record in eco.store.firms():
        if record.status != FirmStatus.ACTIVE.value:
            continue
        assert not eco._already_deliberated(record, "1970-01-01T00", resolution), (
            "a bar the firm has never seen must not read as already spoken for"
        )


def test_no_bar_does_not_silence_the_village(ecosystem):
    """An unknown bar is the blind-feed case, handled elsewhere, not here.

    Returning True for an empty bar key would make a feed outage look exactly
    like a village that had already decided, and nothing would ever trade
    again once the feed hiccuped.
    """
    eco = ecosystem
    record = eco.store.firms()[0]
    resolution = eco.config.data.resolution
    assert eco._already_deliberated(record, "", resolution) is False
