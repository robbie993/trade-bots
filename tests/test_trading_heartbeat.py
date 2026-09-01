"""Two processes must never tick one village.

Written after 2026-09-01, when a `serve` process eighteen hours old and a
browser tab left on "Let it run" drove 35,637 ticks at one every 2.5 seconds
while the background loop ticked every 60. The web process was running the code
it had been started with, so each fix deployed that day was undone on the next
web tick, and the best firm in the village was wound up on an artifact that had
already been fixed in the loop.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from src.trading import heartbeat as hb


@pytest.fixture
def beat_file(tmp_path):
    return tmp_path / "loop.beat"


def _stamp(path, pid, when):
    path.write_text(f"{pid}\n{when.isoformat()}\n")


def test_no_heartbeat_means_nothing_is_running(beat_file):
    assert hb.running_elsewhere(beat_file) is None


def test_our_own_heartbeat_is_not_a_conflict(beat_file):
    """Otherwise the loop would refuse to tick because the loop is ticking."""
    hb.beat(beat_file)
    assert hb.running_elsewhere(beat_file) is None


def test_another_live_process_ticking_recently_is_a_conflict(beat_file):
    _stamp(beat_file, 1, datetime.now(timezone.utc))   # pid 1 always exists
    other = hb.running_elsewhere(beat_file)
    assert other is not None and other["pid"] == 1


def test_a_stale_heartbeat_is_a_loop_that_has_stopped(beat_file):
    """A crashed loop must not block the console forever."""
    old = datetime.now(timezone.utc) - timedelta(seconds=hb.STALE_AFTER_S + 60)
    _stamp(beat_file, 1, old)
    assert hb.running_elsewhere(beat_file) is None


def test_a_process_that_stamped_and_died_is_not_a_conflict(beat_file):
    _stamp(beat_file, 999_999, datetime.now(timezone.utc))
    assert hb.running_elsewhere(beat_file) is None


def test_an_unreadable_heartbeat_never_raises(beat_file):
    beat_file.write_text("not a heartbeat")
    assert hb.running_elsewhere(beat_file) is None


def test_beat_never_raises_even_when_it_cannot_write(tmp_path):
    """A village that cannot write its heartbeat still has to trade."""
    hb.beat(tmp_path / "nope" / "deeper" / "loop.beat")   # creates it
    hb.beat(tmp_path)                                     # a directory: unwritable
