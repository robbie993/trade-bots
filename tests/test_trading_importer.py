"""Importing bots that were never written for this village.

The failure this is written against is a plausible lie: an importer that reads
someone's strategy, emits a genome that looks like it, and lets them believe
the thing was ported. What actually carries over is configuration. So the tests
are mostly about the report being *honest* — what was guessed, what was
defaulted, what was left behind entirely.
"""

from __future__ import annotations

import pytest

from src.trading.importer import scan, scan_all, to_village, write

FOREIGN = '''"""My meme coin bot."""
import ccxt

FAST_MA = 5
SLOW_MA = 21
RSI_PERIOD = 9
TAKE_PROFIT_PCT = 35
TICKERS = ["DOGE-USD", "SHIB-USD"]

def should_buy(df): ...

class Sniper: ...
'''


@pytest.fixture
def bot(tmp_path):
    def write_file(text, name="foreign.py"):
        path = tmp_path / name
        path.write_text(text)
        return path
    return write_file


# =========================================================================
# it never runs the file
# =========================================================================
def test_importing_never_executes_the_file(bot, tmp_path):
    marker = tmp_path / "IMPORT_SIDE_EFFECT"
    scan(bot(f"open({str(marker)!r},'w').write('x')\nTICKERS=['SPY']\n", "bomb.py"))
    assert not marker.exists()


def test_a_computed_parameter_is_reported_not_guessed(bot):
    report = scan(bot("FAST_MA = 6 * 2\nTICKERS = ['SPY']\n"))
    assert any("computed" in note for note in report.ignored)
    assert "fast_window" in report.defaulted


# =========================================================================
# what it maps
# =========================================================================
def test_it_finds_parameters_under_other_peoples_names(bot):
    report = scan(bot(FOREIGN))
    assert report.guessed["fast_window"] == "FAST_MA"
    assert report.guessed["slow_window"] == "SLOW_MA"
    assert report.guessed["rsi_window"] == "RSI_PERIOD"
    assert report.genome["fast_window"] == 5
    assert report.genome["slow_window"] == 21


def test_an_exact_name_is_mapped_not_guessed(bot):
    report = scan(bot("fast_window = 7\nUNIVERSE = ['SPY']\n"))
    assert report.mapped["fast_window"] == "fast_window"
    assert "fast_window" not in report.guessed


def test_it_finds_the_symbol_list_under_other_peoples_names(bot):
    for name in ("TICKERS", "WATCHLIST", "PAIRS", "COINS", "symbols"):
        report = scan(bot(f"{name} = ['SPY', 'QQQ']\n", f"{name}.py"))
        assert report.universe == ("SPY", "QQQ"), name


def test_a_gene_the_file_never_mentions_is_defaulted_not_invented(bot):
    report = scan(bot("FAST_MA = 5\nTICKERS = ['SPY']\n"))
    assert "trend_bias" in report.defaulted
    from src.trading.brain.evolver import BASE_GENOME

    assert report.genome["trend_bias"] == BASE_GENOME["trend_bias"]


def test_a_value_outside_its_range_is_clamped_and_said_so(bot):
    report = scan(bot("FAST_MA = 900\nTICKERS = ['SPY']\n"))
    assert "fast_window" in report.out_of_range
    assert report.genome["fast_window"] == 30      # the top of the range


def test_parameters_that_mean_nothing_here_are_listed_not_dropped(bot):
    report = scan(bot(FOREIGN))
    assert "TAKE_PROFIT_PCT" in report.ignored


def test_a_params_dict_is_read_as_well_as_loose_constants(bot):
    report = scan(bot(
        "PARAMS = {'fast_period': 9, 'slow_period': 40}\nTICKERS = ['SPY']\n"
    ))
    assert report.genome["fast_window"] == 9
    assert report.genome["slow_window"] == 40


# =========================================================================
# what it refuses to pretend
# =========================================================================
def test_the_logic_that_did_not_come_across_is_named(bot):
    report = scan(bot(FOREIGN))
    assert set(report.logic) == {"should_buy", "Sniper"}


def test_an_options_bot_is_flagged_as_unsupported(bot):
    report = scan(bot(
        "STRIKE_OFFSET_PCT = 5\nDAYS_TO_EXPIRY = 30\nUNDERLYINGS = ['SPY']\n",
        "wheel.py",
    ))
    assert "options" in report.unsupported
    assert {"strike", "expiry"} <= report.unsupported["options"]


def test_a_shorting_bot_is_flagged_as_unsupported(bot):
    report = scan(bot("ALLOW_SHORT = True\nTICKERS = ['SPY']\n", "ls.py"))
    assert "shorting" in report.unsupported


def test_a_moving_average_window_is_not_mistaken_for_shorting(bot):
    """`short_window` is a length, not a direction.

    Matching on the raw text found "short" inside `short_window` and "tick"
    inside `TICKERS`. A confident, wrong warning is worse than none: it teaches
    you to skip the section that matters.
    """
    report = scan(bot("SHORT_WINDOW = 10\nLONG_WINDOW = 50\nTICKERS = ['SPY']\n"))
    assert "shorting" not in report.unsupported
    assert "intraday" not in report.unsupported
    assert report.genome["fast_window"] == 10
    assert report.genome["slow_window"] == 50


def test_a_file_with_no_symbols_is_not_written(bot, tmp_path):
    report = scan(bot("FAST_MA = 5\n", "nosymbols.py"))
    assert report.usable is False
    assert write(report, tmp_path / "out") is None


# =========================================================================
# credentials
# =========================================================================
def test_a_file_holding_a_credential_is_refused(bot, tmp_path):
    report = scan(bot(
        "API_KEY = 'sk-live-abcdef'\nFAST_MA = 5\nTICKERS = ['SPY']\n", "keys.py"
    ))
    assert report.secrets is True
    assert report.usable is False
    assert write(report, tmp_path / "out") is None
    assert "credential" in report.summary()


def test_a_secret_never_reaches_the_written_file(bot, tmp_path):
    report = scan(bot(
        "api_secret = 'hunter2'\nFAST_MA = 5\nTICKERS = ['SPY']\n", "keys.py"
    ))
    written = write(report, tmp_path / "out")
    assert written is None
    assert not list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else True


# =========================================================================
# what it writes
# =========================================================================
def test_the_written_file_carries_its_own_provenance(bot):
    body = to_village(scan(bot(FOREIGN)))
    assert "imported from foreign.py" in body
    assert "FAST_MA" in body                       # where the number came from
    assert "did NOT come across" in body
    assert "should_buy" in body


def test_the_written_file_is_one_the_court_can_read(bot, tmp_path):
    written = write(scan(bot(FOREIGN)), tmp_path / "out")
    from src.trading.court.evidence import gather

    evidence = gather(written)
    assert evidence.syntax_error is None
    assert evidence.genome["fast_window"] == 5
    assert evidence.universe == ("DOGE-USD", "SHIB-USD")
    assert not evidence.out_of_range


def test_an_imported_bot_can_be_recruited(ecosystem, bot, tmp_path):
    """The whole point: your file, through the importer, into the village."""
    source = bot(
        "FAST_MA = 8\nSLOW_MA = 60\nRSI_PERIOD = 14\nTREND_BIAS = 88\n"
        "LOOKBACK = 60\nBAND_PCT = 6\nVOLATILITY = 30\n"
        "TICKERS = ['SPY', 'QQQ']\n",
        "my_old_bot.py",
    )
    written = write(scan(source), tmp_path / "out")
    result = ecosystem.recruit(written)
    assert result.accepted, result.reason
    firm = ecosystem.store.get_firm(result.firm_key)
    assert firm.universe == ["SPY", "QQQ"]
    assert firm.status == "paused"          # still needs its funding approved


# =========================================================================
# a directory of them
# =========================================================================
def test_a_whole_directory_is_read_in_one_go(tmp_path):
    (tmp_path / "a.py").write_text("FAST_MA = 5\nTICKERS = ['SPY']\n")
    (tmp_path / "b.yaml").write_text("params:\n  fast_period: 9\ntickers: [QQQ]\n")
    (tmp_path / "notes.txt").write_text("ignore me")
    reports = scan_all(tmp_path)
    assert {r.path.name for r in reports} == {"a.py", "b.yaml"}


def test_yaml_and_json_are_read_too(tmp_path):
    (tmp_path / "c.json").write_text('{"fast_period": 7, "tickers": ["SPY"]}')
    report = scan(tmp_path / "c.json")
    assert report.genome["fast_window"] == 7
    assert report.universe == ("SPY",)


def test_an_unreadable_file_is_a_report_not_an_exception(tmp_path):
    path = tmp_path / "broken.py"
    path.write_text("def (((\n")
    report = scan(path)
    assert report.error
    assert "cannot be read" in report.summary()


# =========================================================================
# symbols, spelled the way the feed spells them
#
# A bot written against a crypto exchange says BTC/USD. The village's feed
# says BTC-USD. Left alone the symbol resolves to nothing and the firm arrives
# holding an empty universe with no explanation — which surfaces days later as
# a leaderboard row that never trades.
# =========================================================================
def test_a_pair_is_respelled_for_the_feed():
    from src.trading.importer import _feed_symbol

    assert _feed_symbol("BTC/USD") == "BTC-USD"
    assert _feed_symbol("ETH_USD") == "ETH-USD"
    assert _feed_symbol("BTC:USD") == "BTC-USD"


def test_a_venue_prefix_is_dropped():
    from src.trading.importer import _feed_symbol

    assert _feed_symbol("BINANCE:BTC/USDT") == "BTC-USD"
    assert _feed_symbol("COINBASE-ETH-USD") == "ETH-USD"


def test_a_stablecoin_quote_becomes_dollars():
    from src.trading.importer import _feed_symbol

    assert _feed_symbol("BTC/USDT") == "BTC-USD"
    assert _feed_symbol("SOL/USDC") == "SOL-USD"


def test_a_symbol_the_feed_already_understands_is_left_alone():
    from src.trading.importer import _feed_symbol

    for symbol in ("SPY", "AAPL", "BTC-USD", "QQQ"):
        assert _feed_symbol(symbol) == symbol


def test_the_respelling_is_reported_not_silent(tmp_path):
    """A symbol quietly rewritten is a symbol you cannot check."""
    from src.trading.importer import scan

    path = tmp_path / "exchange_bot.py"
    path.write_text('SYMBOLS = ["BTC/USD", "SPY"]\nparams = {"fast_ma": 9}\n')

    report = scan(path)
    assert report.universe == ("BTC-USD", "SPY")
    assert report.renamed == {"BTC/USD": "BTC-USD"}
    assert "respelled" in report.summary()
    assert "BTC/USD -> BTC-USD" in report.summary()


def test_nothing_respelled_says_nothing(tmp_path):
    from src.trading.importer import scan

    path = tmp_path / "equity_bot.py"
    path.write_text('TICKERS = ["SPY", "QQQ"]\nparams = {"fast_ma": 9}\n')

    report = scan(path)
    assert report.renamed == {}
    assert "respelled" not in report.summary()
