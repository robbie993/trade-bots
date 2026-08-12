"""Settings that survive closing a terminal.

Every tunable here is an environment variable, which is right for a container
and wrong for a laptop: the configuration lives in one shell window, does not
reach the two background processes the village runs as, and vanishes when the
window closes. The observable result was a village whose tick loop read one
feed while the page read another, with nothing saying so.
"""

from __future__ import annotations

import os

from src.dotenv import load, parse


# =========================================================================
# parsing what people actually write
# =========================================================================
def test_a_plain_assignment():
    assert parse("TRADE_DATA_SOURCE=alpaca") == {"TRADE_DATA_SOURCE": "alpaca"}


def test_the_export_prefix_people_paste_from_a_shell():
    assert parse("export KEY=value") == {"KEY": "value"}


def test_quotes_come_off():
    assert parse("A='one'\nB=\"two\"") == {"A": "one", "B": "two"}


def test_a_comment_after_a_quoted_value_does_not_join_the_value():
    """Checking first and last character got this wrong, and the failure was
    a secret with apostrophes in it — which fails authentication in a way
    that looks like a bad key rather than a bad parser."""
    assert parse("KEY='secret'   # a note") == {"KEY": "secret"}


def test_a_hash_inside_quotes_is_part_of_the_value():
    assert parse("KEY='a#b'") == {"KEY": "a#b"}


def test_a_url_with_a_query_string_survives():
    line = "DATABASE_URL=postgres://u:p@host/db?sslmode=require"
    assert parse(line)["DATABASE_URL"] == "postgres://u:p@host/db?sslmode=require"


def test_comments_and_blank_lines_and_nonsense_are_skipped():
    text = "# a comment\n\nnot an assignment\n=novalue\nGOOD=yes\n"
    assert parse(text) == {"GOOD": "yes"}


def test_an_empty_value_is_kept_as_empty():
    assert parse("KEY=") == {"KEY": ""}


# =========================================================================
# loading
# =========================================================================
def test_it_sets_what_is_missing(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("VILLAGE_TEST_ONE=from-file\n")
    monkeypatch.delenv("VILLAGE_TEST_ONE", raising=False)

    assert load(path) == ["VILLAGE_TEST_ONE"]
    assert os.environ["VILLAGE_TEST_ONE"] == "from-file"


def test_the_real_environment_always_wins(tmp_path, monkeypatch):
    """The file is a default, not an authority: `VAR=x command` must still
    override it, and a container's injected variables must be untouched."""
    path = tmp_path / ".env"
    path.write_text("VILLAGE_TEST_TWO=from-file\n")
    monkeypatch.setenv("VILLAGE_TEST_TWO", "from-the-shell")

    assert load(path) == []
    assert os.environ["VILLAGE_TEST_TWO"] == "from-the-shell"


def test_no_file_is_not_an_error(tmp_path):
    assert load(tmp_path / "nothing-here") == []


def test_a_malformed_file_does_not_stop_the_village(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("this is not\nan env file at all\nGOOD=yes\n")
    monkeypatch.delenv("GOOD", raising=False)

    assert load(path) == ["GOOD"]


def test_it_returns_names_and_never_values(tmp_path, monkeypatch):
    """A startup line that prints an API key is worse than no startup line."""
    path = tmp_path / ".env"
    path.write_text("VILLAGE_TEST_SECRET=hunter2\n")
    monkeypatch.delenv("VILLAGE_TEST_SECRET", raising=False)

    assert load(path) == ["VILLAGE_TEST_SECRET"]
    assert "hunter2" not in str(load(path))


# =========================================================================
# wired in
# =========================================================================
def test_the_config_module_loads_it_before_reading_anything():
    """Otherwise the file would be read after the defaults were decided."""
    import src.config as config

    assert hasattr(config, "_DOTENV_LOADED")


def test_the_example_file_names_the_settings_without_holding_secrets():
    from pathlib import Path

    text = Path(".env.example").read_text()
    for name in ("TRADE_DATA_SOURCE", "ALPACA_API_KEY_ID", "MVV_GATE_PORT"):
        assert name in text
    # every credential line is commented out and empty
    for line in text.splitlines():
        if "API_KEY" in line or "SECRET" in line:
            assert line.strip().startswith("#"), line


def test_dot_env_is_gitignored():
    """The whole point of putting keys there."""
    from pathlib import Path

    assert any(
        line.strip() == ".env"
        for line in Path(".gitignore").read_text().splitlines()
    )
