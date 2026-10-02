"""What cash earns: the 3-month Treasury bill rate, one number per year.

**Approximate, and labelled so.** These are annual averages of the
secondary-market 3-month T-bill rate (FRED series TB3MS), typed in because
the session that built this could not reach FRED. 2000-2024 are published
annual averages rounded to two decimals. 2025 and 2026 are this file's
estimates (the Fed cut through late 2024 and 2025) and should be replaced
with the real series when someone runs `pc_fetch.py` on a machine that can
reach FRED.

Cash matters twice here. It is what Atlas earns on the part of the book it
does not invest, so a generous rate flatters de-risking. And it is the zero
of every Sharpe ratio: Sharpe is computed on return *over* this rate, which
is what stops sitting in cash from scoring as a perfect, riskless strategy
(the "idler" loophole the earlier GA found).
"""
from __future__ import annotations

import pandas as pd

TBILL_3M_ANNUAL_PCT = {
    2000: 5.82, 2001: 3.39, 2002: 1.60, 2003: 1.01, 2004: 1.37, 2005: 3.15,
    2006: 4.73, 2007: 4.36, 2008: 1.37, 2009: 0.15, 2010: 0.14, 2011: 0.05,
    2012: 0.09, 2013: 0.06, 2014: 0.03, 2015: 0.05, 2016: 0.32, 2017: 0.93,
    2018: 1.94, 2019: 2.06, 2020: 0.37, 2021: 0.04, 2022: 2.02, 2023: 5.07,
    2024: 4.97,
    2025: 4.10,  # estimate
    2026: 3.70,  # estimate
}

ESTIMATED_YEARS = (2025, 2026)


def daily_cash_returns(index: pd.DatetimeIndex) -> pd.Series:
    """Per-trading-day cash return on `index`, compounding to the annual rate."""
    years = index.year
    annual = pd.Series([TBILL_3M_ANNUAL_PCT[y] / 100 for y in years], index=index)
    return (1 + annual) ** (1 / 252) - 1
