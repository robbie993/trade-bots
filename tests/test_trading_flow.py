"""The flow diagram's event stream.

Two properties matter more than the animation:

1. The dots are real. Every event is written by the tick as it happens, so a
   still diagram means a still village — not a broken page.
2. Telemetry can never break a tick. Emission swallows its own errors, and a
   database with no `flow_events` table must not stop anything trading.
"""

from __future__ import annotations

import pytest

from src.trading.flow import (
    EDGE_IDS,
    NODE_IDS,
    FlowRecorder,
    NullRecorder,
    diagram,
)


@pytest.fixture
def recorder(db):
    return FlowRecorder(db)


# =========================================================================
# the recorder
# =========================================================================
def test_an_event_is_recorded_with_its_run(recorder):
    recorder.emit("market", "bars in")
    events = recorder.recent()
    assert len(events) == 1
    assert events[0].node == "market"
    assert events[0].run_id == recorder.run_id


def test_a_move_records_the_edge(recorder):
    recorder.move("market", "firms", "buy SPY", firm="alpha")
    event = recorder.recent()[0]
    assert event.edge == "market>firms"
    assert event.node == "firms"
    assert event.firm == "alpha"


def test_unknown_nodes_are_dropped_rather_than_stored(recorder):
    recorder.emit("atlantis", "somewhere else")
    assert recorder.recent() == []


def test_an_unknown_edge_is_dropped_but_the_node_still_lights(recorder):
    recorder.emit("firms", "x", edge="market>halt")
    event = recorder.recent()[0]
    assert event.edge is None
    assert event.node == "firms"


def test_an_unknown_kind_falls_back_to_ok(recorder):
    recorder.emit("firms", "x", kind="catastrophic")
    assert recorder.recent()[0].kind == "ok"


def test_since_returns_only_what_is_new(recorder):
    recorder.emit("market", "one")
    first = recorder.latest_id()
    recorder.emit("firms", "two")
    fresh = recorder.since(first)
    assert [ev.label for ev in fresh] == ["two"]


def test_emission_never_raises_even_without_a_table(tmp_path):
    """A tick must not fail because a diagram was watching."""
    from src.db.connection import Database

    bare = Database.from_url(f"sqlite:///{tmp_path / 'bare.db'}")
    recorder = FlowRecorder(bare)
    recorder.emit("market", "no table here")       # must not raise
    recorder.move("market", "firms", "nor here")   # must not raise
    assert recorder.recent() == []
    assert recorder.since(0) == []
    assert recorder.latest_id() == 0
    bare.close()


def test_the_ring_buffer_is_pruned(recorder):
    from src.trading.flow import KEEP

    for index in range(KEEP + 120):
        recorder.emit("market", f"event {index}")
    remaining = recorder.db.query_one("SELECT COUNT(*) AS n FROM flow_events")["n"]
    assert remaining <= KEEP + 50  # pruned every 50 writes


def test_the_null_recorder_does_nothing_quietly():
    null = NullRecorder()
    null.emit("market", "x")
    null.move("market", "firms", "x")
    assert null.recent() == [] and null.since(0) == [] and null.latest_id() == 0


# =========================================================================
# the diagram
# =========================================================================
def test_every_edge_joins_two_real_nodes():
    shape = diagram()
    ids = {n["id"] for n in shape["nodes"]}
    assert ids == set(NODE_IDS)
    for edge in shape["edges"]:
        assert edge["from"] in ids and edge["to"] in ids
    assert {edge["id"] for edge in shape["edges"]} == set(EDGE_IDS)


def test_the_stages_match_the_documented_tick_order():
    ids = [n["id"] for n in diagram()["nodes"]]
    order = ["market", "firms", "heart", "venue", "ledger", "brain"]
    assert [i for i in ids if i in order] == order


# =========================================================================
# a real tick
# =========================================================================
def test_a_tick_narrates_itself(ecosystem):
    ecosystem.tick(ecosystem.market().seek(150))
    events = ecosystem.flow.recent(100)
    assert events, "a tick emitted nothing"
    assert events[0].node == "market"
    edges = {ev.edge for ev in events if ev.edge}
    assert "market>firms" in edges
    assert "brokerage>audit" in edges


def test_one_tick_is_one_run(ecosystem):
    ecosystem.tick(ecosystem.market().seek(150))
    first = {ev.run_id for ev in ecosystem.flow.recent(100)}
    ecosystem.tick(ecosystem.market().seek(151))
    runs = {ev.run_id for ev in ecosystem.flow.recent(200)}
    assert len(first) == 1
    assert len(runs) == 2


def test_a_blocked_proposal_leaves_the_flow(ecosystem):
    """It travels to `blocked` — it does not glide on in a different colour."""
    ecosystem.tick(ecosystem.market().seek(150))
    events = ecosystem.flow.recent(200)
    blocked = [ev for ev in events if ev.kind == "blocked"]
    if not blocked:
        pytest.skip("nothing was blocked on this bar")
    for event in blocked:
        assert event.node == "blocked"
        assert event.edge in ("heart>blocked", "venue>blocked")


def test_a_failed_reconciliation_is_shown_as_an_alarm(ecosystem):
    from decimal import Decimal

    ecosystem.tick(ecosystem.market().seek(150))
    firm = ecosystem.store.get_firm("alpha")
    ecosystem.db.update("firms", firm.id, {"cash": Decimal("123456.00")})
    ecosystem.tick(ecosystem.market().seek(151))
    alarms = [ev for ev in ecosystem.flow.recent(200) if ev.kind == "alarm"]
    assert any("reconcile" in ev.label for ev in alarms)


def test_the_tick_still_works_when_telemetry_cannot_write(ecosystem):
    ecosystem.db.execute("DROP TABLE flow_events")
    report = ecosystem.tick(ecosystem.market().seek(150))
    assert report.oversight is not None
    assert report.errors == []
