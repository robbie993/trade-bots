"""The ledger copy's checks, without a Postgres server.

The end-to-end copy was verified against a real Postgres 16 (a synthetic
village, 50,242 rows, reconcile and leaderboard identical on both sides). What
is pinned here is the part that decides whether a value may cross at all.
"""

import importlib.util
from collections import defaultdict
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "sqlite_to_postgres",
    Path(__file__).resolve().parents[1] / "scripts" / "sqlite_to_postgres.py",
)
s2p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s2p)


def col(dtype, maxlen=None):
    return {"type": dtype, "maxlen": maxlen, "default": None}


def convert(value, column):
    problems, notes = [], defaultdict(int)
    out = s2p._convert(value, column, "t[1].c", problems, notes)
    return out, problems, dict(notes)


@pytest.mark.parametrize("raw,want", [(1, "t"), (0, "f"), ("1", "t"), ("false", "f")])
def test_sqlite_booleans_become_postgres_booleans(raw, want):
    assert convert(raw, col("boolean"))[0] == want


def test_a_non_boolean_in_a_boolean_column_is_refused_not_guessed():
    out, problems, _ = convert("maybe", col("boolean"))
    assert out is None and problems


def test_too_long_for_varchar_is_refused_before_anything_is_written():
    out, problems, _ = convert("x" * 21, col("character varying", 20))
    assert out is None and "VARCHAR(20)" in problems[0]


def test_invalid_json_is_refused():
    _, problems, _ = convert("{not json", col("json"))
    assert problems


def test_blank_json_and_blank_timestamps_become_null_and_are_counted():
    out, problems, notes = convert("", col("jsonb"))
    assert out is None and not problems and notes == {"blank JSON written as NULL": 1}
    out, problems, notes = convert("  ", col("timestamp with time zone"))
    assert out is None and not problems and notes == {"blank timestamp written as NULL": 1}


def test_money_keeps_its_digits():
    # SQLite's NUMERIC affinity hands money back as a float; repr() is the
    # shortest string that round-trips, so nothing is invented or dropped here.
    assert convert(451.16, col("numeric"))[0] == "451.16"
    assert convert(0.1 + 0.2, col("numeric"))[0] == repr(0.1 + 0.2)


def test_integer_valued_float_in_an_integer_column():
    assert convert(3.0, col("integer"))[0] == "3"


def test_parents_load_before_children():
    deps = {"fills": {"trade_proposals", "firms"}, "trade_proposals": {"firms"}}
    order = s2p._load_order(["fills", "firms", "trade_proposals", "bouts"], deps)
    assert order.index("firms") < order.index("trade_proposals") < order.index("fills")


def test_a_foreign_key_cycle_stops_the_copy():
    with pytest.raises(SystemExit):
        s2p._load_order(["a", "b"], {"a": {"b"}, "b": {"a"}})


def test_refuses_a_url_that_is_not_postgres(tmp_path):
    with pytest.raises(SystemExit):
        s2p.main(["--sqlite", str(tmp_path / "x.db"), "--postgres", "sqlite:///x.db"])
