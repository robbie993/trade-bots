"""The brain, written into the vault.

The vault was a diary: what happened, day by day. This makes it a graph — a
lesson links to the symbol it is about, a symbol links back to the firms that
traded it, a genome links to the genome it was mutated from. The property
worth testing is that those links are *real relationships out of the database*
rather than decoration, and that a note which is regenerated says so, because
the rest of the vault promises never to rewrite what it has recorded.
"""

from __future__ import annotations

import json

import pytest

from src.money import D
from src.trading.audit.obsidian_logger import ObsidianLogger, slug
from src.trading.brain.learning import Lesson


@pytest.fixture
def vault(tmp_path):
    return ObsidianLogger(tmp_path / "vault")


# =========================================================================
# lessons — appended, because when it was concluded is the interesting part
# =========================================================================
def test_a_lesson_links_to_the_symbol_it_is_about(vault):
    path = vault.log_lesson(
        Lesson(topic="SPY", statement="loses money on average", evidence="8 trades", kind="loser")
    )
    body = path.read_text()
    assert "[[brain/symbols/SPY|SPY]]" in body
    assert "loses money on average" in body
    assert "8 trades" in body


def test_a_lesson_that_is_not_about_a_ticker_links_nowhere(vault):
    """`costs` is a topic, not a symbol; a link to brain/symbols/costs would
    invent a node that nothing else in the vault points at."""
    body = vault.log_lesson(
        Lesson(topic="costs", statement="fees eat the edge", evidence="…", kind="fee_drag")
    ).read_text()
    assert "brain/symbols" not in body
    assert "`costs`" in body


def test_a_lesson_note_is_appended_never_rewritten(vault):
    lesson = Lesson(topic="SPY", statement="first conclusion", evidence="4 trades", kind="loser")
    vault.log_lesson(lesson)
    vault.log_lesson(Lesson(topic="SPY", statement="still true", evidence="9 trades",
                            kind="loser"))
    body = vault.lesson_note(lesson.key).read_text()
    assert "first conclusion" in body
    assert "still true" in body
    assert body.count("## ") == 2


def test_a_lesson_can_name_the_firm_that_learned_it(vault):
    body = vault.log_lesson(
        Lesson(topic="alpha", statement="is bleeding", evidence="…"), firm_key="alpha"
    ).read_text()
    assert "[[firms/alpha/" in body


# =========================================================================
# symbols — regenerated, and honest about being a view
# =========================================================================
def test_a_symbol_note_says_it_is_a_view_not_a_record(vault):
    body = vault.write_symbol({"symbol": "SPY", "trades": 2, "wins": 1, "losses": 1,
                               "net": D("10"), "best": D("20"), "worst": D("-10")}).read_text()
    assert "Regenerated" in body
    assert "not a record" in body


def test_a_symbol_note_separates_fills_from_closed_trades(vault):
    """'20 trades (1 won, 1 lost)' reads as if eighteen went missing.

    They did not: an opening buy is remembered with a P&L of zero, and only
    the closing leg of a round trip realises anything.
    """
    body = vault.write_symbol({"symbol": "SPY", "trades": 20, "wins": 1, "losses": 1,
                               "net": D("-164.94"), "best": D("6.50"),
                               "worst": D("-171.44")}).read_text()
    assert "Fills remembered: **20**" in body
    assert "Closed with a result: **2**" in body
    assert "18 opened or added to a position" in body


def test_a_symbol_note_links_to_its_firms_and_its_lessons(vault):
    body = vault.write_symbol(
        {"symbol": "SPY", "trades": 2, "wins": 1, "losses": 1, "net": D("0"),
         "best": D("0"), "worst": D("0")},
        firms=["alpha", "beta"],
        lesson_keys=["SPY:loser"],
    ).read_text()
    assert "[[firms/alpha/" in body
    assert "[[firms/beta/" in body
    assert f"[[brain/lessons/{slug('SPY:loser')}]]" in body


def test_regenerating_a_symbol_replaces_it_rather_than_growing_it(vault):
    record = {"symbol": "SPY", "trades": 2, "wins": 1, "losses": 1, "net": D("0"),
              "best": D("0"), "worst": D("0")}
    vault.write_symbol(record)
    body = vault.write_symbol({**record, "trades": 4}).read_text()
    assert body.count("# SPY") == 1
    assert "Fills remembered: **4**" in body


# =========================================================================
# genomes — the lineage
# =========================================================================
def test_a_genome_note_points_at_the_genome_it_came_from(vault):
    vault.write_genome({"id": 1, "generation": 1, "fitness": D("5"), "trades": 10,
                        "selected": 0, "notes": "incumbent", "genome": "{}"}, "alpha")
    body = vault.write_genome({"id": 2, "generation": 1, "fitness": D("6"), "trades": 10,
                               "selected": 1, "notes": "mutant", "parent_id": 1,
                               "genome": '{"fast_window": 8}'}, "alpha").read_text()
    assert "[[brain/genomes/g00001]]" in body
    assert "**selected**" in body
    assert "fast_window" in body


def test_an_incumbent_says_it_has_no_parent(vault):
    body = vault.write_genome({"id": 1, "generation": 1, "fitness": D("5"), "trades": 4,
                               "selected": 0, "notes": "incumbent", "genome": "{}"},
                              "alpha").read_text()
    assert "this one is an incumbent" in body


def test_a_genome_note_is_written_once(vault):
    row = {"id": 1, "generation": 1, "fitness": D("5"), "trades": 4, "selected": 0,
           "notes": "incumbent", "genome": "{}"}
    first = vault.write_genome(row, "alpha")
    stamp = first.read_text()
    vault.write_genome({**row, "fitness": D("999")}, "alpha")
    assert first.read_text() == stamp


# =========================================================================
# the index
# =========================================================================
def test_the_brain_index_lists_what_is_on_disk(vault):
    vault.log_lesson(Lesson(topic="SPY", statement="x", evidence="y", kind="loser"))
    vault.write_symbol({"symbol": "SPY", "trades": 1, "wins": 1, "losses": 0,
                        "net": D("1"), "best": D("1"), "worst": D("0")})
    vault.write_genome({"id": 3, "generation": 1, "fitness": D("1"), "trades": 1,
                        "selected": 0, "notes": "incumbent", "genome": "{}"}, "alpha")
    body = vault.rebuild_brain().read_text()
    assert "Lessons (1)" in body and "Symbols (1)" in body and "Genomes (1)" in body
    assert "[[brain/symbols/SPY]]" in body
    assert "[[brain/genomes/g00003]]" in body


def test_the_main_index_reaches_the_brain(vault):
    vault.rebuild_brain()
    assert "[[brain/index]]" in vault.rebuild_index().read_text()


def test_the_main_index_says_nothing_about_a_brain_that_is_not_there(vault):
    assert "brain" not in vault.rebuild_index().read_text()


def test_a_slug_is_safe_and_stable():
    assert slug("SPY:loser") == "SPY-loser"
    assert slug("BTC-USD") == "BTC-USD"
    assert slug("../../etc/passwd") == ".._.._etc_passwd".replace("_", "-")
    assert slug("") == "unknown"


# =========================================================================
# through the ecosystem
# =========================================================================
def test_a_tick_writes_the_brain_into_the_vault(ecosystem):
    ecosystem.tick()
    vault = ecosystem.audit.vault
    assert (vault / "brain" / "index.md").exists()
    symbols = list((vault / "brain" / "symbols").glob("*.md"))
    assert symbols, "the firms' universes should have produced symbol notes"


def test_only_newly_drawn_lessons_get_a_note(ecosystem):
    """A note per tick per known lesson would bury the moment one was learned."""
    for _ in range(3):
        ecosystem.tick()
    lessons = list((ecosystem.audit.vault / "brain" / "lessons").glob("*.md"))
    for note in lessons:
        assert note.read_text().count("## ") == 1


def test_evolution_writes_its_lineage(ecosystem):
    ecosystem.tick()
    ecosystem.evolve(generations=1)
    notes = sorted((ecosystem.audit.vault / "brain" / "genomes").glob("*.md"))
    assert notes
    bodies = [n.read_text() for n in notes]
    assert any("Mutated from: [[brain/genomes/" in b for b in bodies), "no lineage recorded"
    assert any("this one is an incumbent" in b for b in bodies), "no root recorded"


def test_every_mutant_records_the_genome_it_came_from(ecosystem):
    """Without parent_id the descent of a strategy is unrecoverable."""
    ecosystem.tick()
    ecosystem.evolve(generations=1)
    rows = ecosystem.db.query("SELECT * FROM strategy_genomes ORDER BY id")
    incumbents = [r for r in rows if r["notes"] == "incumbent"]
    mutants = [r for r in rows if r["notes"] == "mutant"]
    assert incumbents and mutants
    assert all(r["parent_id"] is None for r in incumbents)

    # One incumbent per firm per generation, and a mutant descends from its own
    # firm's — pointing them all at the first incumbent would cross the firms.
    root = {(r["firm_id"], r["generation"]): r["id"] for r in incumbents}
    assert all(
        mutant["parent_id"] == root[(mutant["firm_id"], mutant["generation"])]
        for mutant in mutants
    )
    assert len(root) == len({r["firm_id"] for r in rows})


def test_a_broken_vault_never_breaks_a_tick(ecosystem, monkeypatch):
    """The vault is telemetry for humans; the ledger does not depend on it."""
    def boom(*args, **kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(ecosystem.audit, "write_symbol", boom)
    report = ecosystem.tick()
    assert report.errors == []


def test_the_lesson_notes_carry_the_same_text_as_the_database(ecosystem):
    ecosystem.tick()
    stored = {json.loads(m.payload and json.dumps(m.payload) or "{}").get("key")
              for m in ecosystem.memory.lessons(limit=50)}
    notes = {p.stem for p in (ecosystem.audit.vault / "brain" / "lessons").glob("*.md")}
    assert {slug(key) for key in stored if key} == notes
