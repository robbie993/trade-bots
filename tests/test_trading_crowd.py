"""Explicit crowd calls — and the control that makes them measurable.

The module's whole claim is that it separates *"somebody said to buy it"* from
*"somebody mentioned it"*. Most of these tests are that distinction, and the
rest are the null control, which is not a test fixture but half the
measurement.
"""

from __future__ import annotations

from src.money import D
from src.trading.crowd import (
    MAX_CONFIDENCE,
    Call,
    CrowdReading,
    extract_calls,
    readings_from,
    shuffle_symbols,
)

UNIVERSE = ["DOGE-USD", "SHIB-USD", "PEPE-USD", "WIF-USD"]


# =========================================================================
# a mention is not a call
# =========================================================================
def test_a_bare_mention_is_not_a_call():
    """The failure this module exists to avoid.

    A name trending because it crashed is mentioned constantly, and a mention
    counter reads that as bullish.
    """
    assert extract_calls("DOGE is down 40% today", UNIVERSE) == []
    assert extract_calls("what happened to PEPE", UNIVERSE) == []


def test_an_explicit_call_is_found_with_the_span_that_justified_it():
    calls = extract_calls("just bought more DOGE this morning", UNIVERSE)
    assert len(calls) == 1
    assert calls[0].symbol == "DOGE-USD"
    assert calls[0].is_bullish
    assert "bought" in calls[0].phrase


def test_the_dollar_form_is_the_same_symbol():
    assert extract_calls("buying $WIF", UNIVERSE)[0].symbol == "WIF-USD"


def test_a_bearish_call_is_read_as_bearish():
    calls = extract_calls("sold all my SHIB, done with it", UNIVERSE)
    assert len(calls) == 1 and not calls[0].is_bullish


def test_a_negator_flips_the_call():
    """"not buying DOGE" is not a buy. Without this every warning in the
    corpus reads as an endorsement, which on this asset class is most of it."""
    calls = extract_calls("I am not buying DOGE at these prices", UNIVERSE)
    assert len(calls) == 1
    assert not calls[0].is_bullish
    assert calls[0].negated


def test_a_ticker_inside_a_longer_word_is_not_a_mention():
    """SHIBA is not SHIB. A substring matcher says it is."""
    assert extract_calls("buying SHIBASWAP tokens", UNIVERSE) == []


def test_a_symbol_outside_the_universe_is_ignored():
    assert extract_calls("buying BTC hard", UNIVERSE) == []


def test_a_distant_verb_does_not_attach_to_a_ticker():
    text = "bought a sandwich and then walked the dog for a while before DOGE"
    assert extract_calls(text, UNIVERSE) == []


def test_two_tickers_in_one_sentence_each_get_their_own_call():
    calls = extract_calls("buying DOGE and selling PEPE", UNIVERSE)
    by_symbol = {c.symbol: c for c in calls}
    assert by_symbol["DOGE-USD"].is_bullish
    assert not by_symbol["PEPE-USD"].is_bullish


def test_repeating_yourself_is_still_one_person():
    """"buying DOGE, buying DOGE, buying DOGE" is one caller, not three."""
    calls = extract_calls("buying DOGE buying DOGE buying DOGE", UNIVERSE)
    assert len(calls) == 1


def test_a_two_sided_post_records_only_the_nearer_call():
    """A documented limit, pinned so it cannot drift silently.

    "bought DOGE yesterday, selling it today" is genuinely two-sided, and one
    mention of the ticker takes only its nearest verb. The test exists to make
    the limit visible, not to bless it.
    """
    calls = extract_calls("bought DOGE yesterday, selling it today", UNIVERSE)
    assert len(calls) == 1
    assert calls[0].is_bullish        # "bought" is nearer than "selling"


def test_a_ticker_named_twice_can_carry_both_directions():
    calls = extract_calls("bought DOGE last week. now selling DOGE", UNIVERSE)
    assert {c.is_bullish for c in calls} == {True, False}
    # And they net to nothing, which is the honest reading of an ambiguous post.
    assert readings_from(["bought DOGE last week. now selling DOGE"],
                         UNIVERSE)["DOGE-USD"].score == 0


def test_empty_and_junk_inputs_are_silence_not_errors():
    assert extract_calls("", UNIVERSE) == []
    assert extract_calls("...", UNIVERSE) == []
    assert extract_calls("buying DOGE", []) == []


# =========================================================================
# aggregation
# =========================================================================
def test_a_symbol_nobody_called_is_absent_rather_than_zero():
    """Absent and zero are opposite states: "nobody mentioned it" against
    "the crowd is evenly split"."""
    readings = readings_from(["buying DOGE"], UNIVERSE)
    assert set(readings) == {"DOGE-USD"}
    assert "PEPE-USD" not in readings


def test_a_split_crowd_scores_zero_and_carries_no_confidence():
    """Loud and saying nothing. It must not collect the confidence of its
    turnout."""
    readings = readings_from(
        ["buying DOGE", "selling DOGE", "bought DOGE", "sold DOGE"], UNIVERSE)
    doge = readings["DOGE-USD"]
    assert doge.total == 4
    assert doge.score == 0
    assert doge.confidence == 0


def test_confidence_grows_with_agreement_and_is_capped():
    one = readings_from(["buying DOGE"], UNIVERSE)["DOGE-USD"]
    many = readings_from(["buying DOGE"] * 20, UNIVERSE)["DOGE-USD"]
    assert 0 < one.confidence < many.confidence
    assert many.confidence <= MAX_CONFIDENCE


def test_score_is_bounded_and_directional():
    bulls = readings_from(["buying DOGE"] * 3, UNIVERSE)["DOGE-USD"]
    bears = readings_from(["selling DOGE"] * 3, UNIVERSE)["DOGE-USD"]
    assert bulls.score == 100
    assert bears.score == -100


# =========================================================================
# the null control
# =========================================================================
def test_the_shuffle_keeps_the_count_and_moves_the_symbol():
    """Same timing, no selection. That is the only arm that can tell a crowd
    edge apart from volatility clustering with a Reddit-shaped mask on it."""
    calls = [Call("DOGE-USD", 1, "buying", "buying doge")] * 10
    shuffled = shuffle_symbols(calls, UNIVERSE, seed=1)
    assert len(shuffled) == len(calls)
    assert all(c.symbol != "DOGE-USD" for c in shuffled)
    assert {c.direction for c in shuffled} == {1}


def test_the_shuffle_is_deterministic_for_a_seed():
    calls = [Call("DOGE-USD", 1, "buying", "x")] * 25
    a = [c.symbol for c in shuffle_symbols(calls, UNIVERSE, seed=7)]
    b = [c.symbol for c in shuffle_symbols(calls, UNIVERSE, seed=7)]
    assert a == b
    assert a != [c.symbol for c in shuffle_symbols(calls, UNIVERSE, seed=8)]


def test_the_shuffle_spreads_across_the_rest_of_the_universe():
    calls = [Call("DOGE-USD", 1, "buying", "x")] * 200
    landed = {c.symbol for c in shuffle_symbols(calls, UNIVERSE, seed=3)}
    assert landed == {"SHIB-USD", "PEPE-USD", "WIF-USD"}


def test_a_single_symbol_universe_has_no_control_and_says_so_by_returning_unchanged():
    """With one symbol there is nowhere to reassign to. The calls come back
    untouched, which the caller must treat as "no control exists here" rather
    than as a control that passed."""
    calls = [Call("DOGE-USD", 1, "buying", "x")] * 5
    assert shuffle_symbols(calls, ["DOGE-USD"], seed=1) == calls
