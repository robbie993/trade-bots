"""What the village chose, why, and how it turned out.

The one idea worth taking from the memory research: MindCache's taxonomy
(user / decisions / episodic / knowledge) is better structure than one flat
table. Two of those kinds already existed here under other names. `decision`
is the one that was missing, and it is the interesting one — this village
kills firms, revives them, promotes them to real money and adopts genomes,
and remembered none of it in a form anything could recall.

The library itself is deliberately not used: its premise is a model that
rewrites what it stores, and this village's premise is that the ledger is the
only source of truth. The shape is the good idea; the mechanism is not.
"""

from __future__ import annotations



def test_the_four_kinds_exist_and_trade_and_lesson_are_not_renamed(store):
    """Two of them already existed. Renaming those would rewrite history for
    nothing."""
    from src.trading.brain.memory import AgentMemory

    m = AgentMemory(store)
    assert set(AgentMemory.KINDS) == {"trade", "lesson", "decision", "preference"}
    assert m.TRADE == "trade" and m.LESSON == "lesson"


def test_a_decision_records_its_reason_before_the_outcome(store, firm_record):
    """A rationale written after the result is known is a rationalisation, and
    this system has enough ways to fool itself."""
    from src.trading.brain.memory import AgentMemory

    m = AgentMemory(store)
    mid = m.remember_decision("killed alpha", "drawdown 27% exceeds 20%",
                              firm_id=firm_record.id)
    [noted] = m.by_kind("decision", firm_id=firm_record.id)
    assert noted.id == mid
    assert noted.outcome == "pending", "the result is not known yet and must not be faked"
    assert "drawdown 27%" in noted.summary


def test_settling_closes_the_loop(store, firm_record):
    from src.trading.brain.memory import AgentMemory

    m = AgentMemory(store)
    mid = m.remember_decision("put alpha on real money", "approved by robbie",
                              firm_id=firm_record.id)
    assert m.settle_decision(mid, "pulled back: its book cannot be valued") is True

    [noted] = m.by_kind("decision", firm_id=firm_record.id)
    assert noted.outcome.startswith("pulled back")


def test_an_outcome_cannot_be_attached_to_a_decision_nobody_made(store):
    """Otherwise the record grows facts about decisions that never happened."""
    from src.trading.brain.memory import AgentMemory

    m = AgentMemory(store)
    assert m.settle_decision(999_999, "it went well") is False


def test_a_lesson_is_not_settleable_as_a_decision(store, firm_record):
    from src.trading.brain.memory import AgentMemory

    m = AgentMemory(store)
    lesson = m.remember("SPY loses money for this firm", firm_id=firm_record.id)
    assert m.settle_decision(lesson, "went fine") is False


def test_precedent_answers_what_happened_last_time(store, firm_record):
    """The point of keeping decisions at all."""
    from src.trading.brain.memory import AgentMemory

    m = AgentMemory(store)
    old = m.remember_decision("put alpha on real money", "first attempt",
                              firm_id=firm_record.id)
    m.settle_decision(old, "pulled back after two days: drawdown")
    m.remember_decision("put alpha on real money", "second attempt",
                        firm_id=firm_record.id)

    history = m.precedent("put alpha on real money", firm_id=firm_record.id)
    assert len(history) == 2
    assert history[0].outcome != "pending", "settled ones come first — they are the evidence"
    assert "pulled back" in history[0].outcome


def test_precedent_does_not_leak_across_firms(store, firm_record):
    from src.trading.brain.memory import AgentMemory

    m = AgentMemory(store)
    m.remember_decision("killed something", "a reason", firm_id=firm_record.id)
    # A village-wide decision, belonging to no firm.
    m.remember_decision("killed something", "a reason", firm_id=None)
    assert len(m.precedent("killed something", firm_id=firm_record.id)) == 1
    assert len(m.precedent("killed something")) == 2


# =========================================================================
# wired into the village
# =========================================================================
def test_going_live_and_coming_back_is_one_remembered_decision(ecosystem):
    """The pair that matters most. Next time this firm is proposed for real
    money, the village can say what happened last time."""
    firm = ecosystem.store.firms()[0]
    ecosystem._remember_decision(
        f"put {firm.firm_key} on real money", "approved by robbie at $500.00",
        firm_key=firm.firm_key)

    noted = ecosystem.memory.precedent(f"put {firm.firm_key} on real money",
                                       firm_id=firm.id)
    assert noted and noted[0].outcome == "pending"

    ecosystem._settle_decision(f"put {firm.firm_key} on real money",
                               "pulled back: drawdown 24%", firm.id)
    after = ecosystem.memory.precedent(f"put {firm.firm_key} on real money",
                                       firm_id=firm.id)
    assert "pulled back" in after[0].outcome


def test_a_diary_entry_is_never_worth_a_tick(ecosystem):
    """Best-effort by design: a village that cannot write a note still has to
    be able to kill a firm."""
    broken = ecosystem.memory

    class Refuses:
        def remember_decision(self, *a, **k):
            raise RuntimeError("disk full")

        def precedent(self, *a, **k):
            raise RuntimeError("disk full")

    ecosystem.memory = Refuses()
    try:
        ecosystem._remember_decision("killed something", "a reason")
        ecosystem._settle_decision("killed something", "an outcome")
    finally:
        ecosystem.memory = broken


def test_the_tick_still_reconciles_with_decisions_being_written(ecosystem):
    report = ecosystem.tick()
    assert report.oversight is not None
    assert report.oversight.reconciliation.ok
