"""The news desk: what it reads, what it refuses to say, and how it fails.

Three of these tests exist because the first live fetch failed in three
different ways, each of which looked from the outside like "no news today":
TLS verification the price feed had already solved, a lexicon that could not
read a participle, and a village that only knew its symbols by ticker.
"""

from decimal import Decimal

import pytest

from src.money import D
from src.trading.news import (
    ALIASES,
    MAX_CONFIDENCE,
    Digest,
    Story,
    read_the_news,
    score_text,
)


class FakeSource:
    def __init__(self, name, titles, boom=False):
        self.name = name
        self.titles = titles
        self.boom = boom

    def fetch(self, symbols):
        if self.boom:
            raise RuntimeError("the internet fell over")
        return [Story(title=t, source=self.name) for t in self.titles]


# =========================================================================
# reading a headline
# =========================================================================
def test_the_lexicon_reads_participles_not_just_infinitives():
    """The bug the first live fetch found.

    "Is JNJ Outperforming the Consumer Staples Sector?" scored zero, because
    the lexicon held `outperform`. Financial headlines are written in
    participles and an exact match reads almost none of them.
    """
    for word in ("outperforming", "outperformed", "outperforms"):
        score, hits = score_text(f"JNJ is {word} the sector")
        assert hits == 1, f"{word!r} was not read at all"
        assert score > 0

    for word in ("plunges", "plunging", "plunged"):
        score, hits = score_text(f"stock {word} on the news")
        assert hits == 1 and score < 0


def test_the_lexicon_is_symmetric():
    """A one-sided vocabulary is a systematic bias, not a gap.

    The first version had `outperform` and no `underperform`, and the very
    first live fetch returned two headlines using it — "Is XOM Underperforming
    the Energy Sector?" and "Is Procter & Gamble Stock Underperforming the
    Nasdaq?" — both scoring zero. A lexicon that knows more bullish words than
    bearish ones does not merely miss stories: it reads the market as more
    bullish than it is, and the village sizes positions off that number.
    """
    from src.trading.news import BEARISH, BULLISH

    for up, down in [
        ("outperform", "underperform"), ("rise", "fall"),
        ("gains", "declines"), ("higher", "lower"),
        ("strong", "weak"), ("bullish", "bearish"),
        ("beat", "miss"), ("optimis", "pessimis"),
    ]:
        assert up in BULLISH, f"{up!r} missing from the bullish lexicon"
        assert down in BEARISH, (
            f"{down!r} missing from the bearish lexicon while {up!r} is "
            "present — that asymmetry is a long bias"
        )


def test_opposite_headlines_score_opposite_ways():
    up, _ = score_text("Is XOM Outperforming the Energy Sector?")
    down, _ = score_text("Is XOM Underperforming the Energy Sector?")
    assert up > 0 and down < 0


def test_a_headline_with_no_loaded_words_scores_nothing():
    score, hits = score_text("Company schedules its annual meeting for Tuesday")
    assert hits == 0 and score == 0


# =========================================================================
# matching a symbol
# =========================================================================
def test_a_company_is_matched_by_name_not_only_by_ticker():
    """Nobody writes "PG missed earnings"; they write "Procter & Gamble"."""
    story = Story(title="Is Procter & Gamble Stock Underperforming?", source="t")
    assert story.mentions("PG", ALIASES["PG"])
    assert not story.mentions("PG", ())


def test_a_ticker_does_not_match_inside_another_word():
    """Substring matching makes KO fire on Tokyo and fill the board with noise."""
    story = Story(title="Tokyo shares rally as the Nikkei climbs", source="t")
    assert not story.mentions("KO", ())
    assert Story(title="KO beats on earnings", source="t").mentions("KO", ())


# =========================================================================
# turning stories into readings
# =========================================================================
def test_a_story_about_a_symbol_becomes_a_reading():
    src = FakeSource("fake", ["NVDA beats expectations and surges on record profit"])
    digest = read_the_news([src], ["NVDA"])
    assert len(digest.readings) == 1
    assert digest.readings[0].symbol == "NVDA"
    assert digest.readings[0].score > 0


def test_a_symbol_nobody_wrote_about_gets_no_reading():
    src = FakeSource("fake", ["NVDA beats expectations"])
    digest = read_the_news([src], ["TLT"])
    assert digest.readings == []


def test_being_written_about_in_words_it_cannot_read_is_not_a_signal():
    """Mentioned but unreadable must be silence, not a confident zero."""
    src = FakeSource("fake", ["TLT is the subject of a scheduled announcement"])
    digest = read_the_news([src], ["TLT"])
    assert digest.readings == []


def test_the_news_never_sounds_certain():
    """Volume of coverage is not conviction."""
    titles = [f"NVDA surges to a record on breakthrough profit {n}" for n in range(50)]
    digest = read_the_news([FakeSource("fake", titles)], ["NVDA"])
    assert digest.readings
    assert digest.readings[0].confidence <= MAX_CONFIDENCE


# =========================================================================
# failing
# =========================================================================
def test_a_source_that_raises_does_not_take_the_tick_with_it():
    good = FakeSource("good", ["NVDA surges on record profit"])
    bad = FakeSource("bad", [], boom=True)
    digest = read_the_news([bad, good], ["NVDA"])
    assert digest.readings, "one broken source must not silence the rest"
    assert any("bad" in f for f in digest.sources_failed)


def test_a_silent_source_says_why_it_was_silent():
    """Silence and failure look identical from outside, and this village has
    lost days to exactly that confusion."""
    digest = read_the_news([FakeSource("empty", [])], ["NVDA"])
    assert digest.sources_failed
    assert "empty" in digest.sources_failed[0]


def test_no_sources_is_an_empty_digest_not_a_crash():
    digest = read_the_news([], ["NVDA"])
    assert isinstance(digest, Digest)
    assert digest.readings == [] and digest.stories == 0
