"""Point-in-time fundamentals for the quality and value layers. NOT BUILT YET.

The quality layer (ROE, gross profitability, earnings stability, debt/equity)
and the stock value layer (earnings, FCF, sales/EV, book/price) need two
things this session could not get, and faking either would make every
number downstream meaningless:

1. **Fundamentals as they were known on the day.** Each figure has to carry
   the date its filing became public (SEC EDGAR's `filed` date in the XBRL
   company-facts API), and a backtest may only read figures filed before its
   decision date. sec.gov is blocked from the cloud session's network; it is
   reachable from Robbie's PC.
2. **A stock universe that includes the companies that died.** The only
   stock history in reach (Qlib's Yahoo dump) lists only names still trading
   in Nov 2020, so every bankruptcy is missing. Qlib does ship historical
   S&P 500 membership (`instruments/sp500.txt`), but without prices for the
   members that were later delisted, a backtest on it is survivorship-biased.

Until both exist, `load` refuses rather than returning a stand-in. The ETF
core uses a documented asset-class value signal instead (see
`signals/value.py`), which needs only prices.
"""


class FundamentalsUnavailable(RuntimeError):
    pass


def load(*_args, **_kwargs):
    raise FundamentalsUnavailable(
        "point-in-time fundamentals and a survivorship-free stock universe are "
        "not built yet; see atlas_research/data/fundamentals.py"
    )
