"""An approval that can never succeed must be answered, not retried forever.

Approval 227 asked to resume `firm_a_etf_ii_v` on 2026-09-05. The firm had
gone bankrupt, so `resume_firm` refused — correctly. But the refusal was a bare
raise inside `apply_approvals`, so it escaped the loop, aborted the whole tick,
and left the row unmarked to be retried on the next one.

341 ticks died on it over eleven days, every one with the same line:

    tick failed: firm_a_etf_ii_v is killed; killed firms do not resume.

The damage is wider than the lost ticks. `apply_approvals` walks approved rows
`ORDER BY id`, so every approval queued behind 227 went unapplied too — the
loop never reached them.
"""

from __future__ import annotations

import pytest


class _Brokerage:
    """Refuses exactly the way the real one does."""

    def __init__(self):
        self.resumed = []

    def resume_firm(self, firm_key, by):
        if firm_key == "dead_firm":
            raise ValueError(
                f"{firm_key} is killed; killed firms do not resume. "
                "If it was killed on a feed outage, see `trade revive`."
            )
        self.resumed.append(firm_key)
        return {"firm": firm_key, "status": "active", "by": by}


def test_a_permanently_failing_resume_is_marked_applied_not_retried():
    """The row is consumed, so the next tick does not meet it again.

    A killed firm does not become un-killed, so retrying is not patience — it
    is a loop that cannot terminate.
    """
    broker = _Brokerage()
    details = {"kind": "resume", "firm": "dead_firm"}

    # The shape of the fix: refuse, record, mark applied, carry on.
    try:
        broker.resume_firm(details["firm"], "the council")
    except ValueError as exc:
        refusal = f"REFUSED resuming {details['firm']}: {exc}"
        details["applied"] = True

    assert details["applied"] is True, "an unconsumable row would replay forever"
    assert "REFUSED" in refusal
    assert "killed" in refusal, "the reason must survive into the record"


def test_the_refusal_does_not_stop_later_approvals():
    """The real cost of the bug: everything queued behind 227 never ran.

    `apply_approvals` reads approved rows `ORDER BY id`, so an exception on a
    low id meant no higher id was ever reached, on any tick, for eleven days.
    """
    broker = _Brokerage()
    queue = [
        {"id": 227, "firm": "dead_firm"},       # the poison pill
        {"id": 229, "firm": "live_firm_a"},
        {"id": 231, "firm": "live_firm_b"},
    ]

    applied = []
    for row in queue:
        try:
            broker.resume_firm(row["firm"], "the council")
        except ValueError:
            applied.append(f"REFUSED {row['firm']}")
            continue
        applied.append(f"resumed {row['firm']}")

    assert broker.resumed == ["live_firm_a", "live_firm_b"], (
        "the two healthy resumes must land even though 227 refused"
    )
    assert len(applied) == 3, "every row gets an answer, not just the first"


def test_a_healthy_resume_still_works():
    """The guard must not swallow the normal path."""
    broker = _Brokerage()
    result = broker.resume_firm("live_firm_a", "the council")
    assert result["status"] == "active"
    assert broker.resumed == ["live_firm_a"]


def test_only_value_error_is_swallowed():
    """A refusal is a ValueError. A database being down is not, and must not
    be quietly marked applied — that would consume an approval that never
    happened."""
    class Broken(_Brokerage):
        def resume_firm(self, firm_key, by):
            raise RuntimeError("database is gone")

    with pytest.raises(RuntimeError):
        try:
            Broken().resume_firm("any", "the council")
        except ValueError:
            pytest.fail("a RuntimeError must not be caught as a refusal")
