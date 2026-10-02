"""Asset-class value (Asness, Moskowitz & Pedersen 2013, "Value and
Momentum Everywhere").

For assets with no book value (indices, bonds, commodities, currencies) the
paper's value measure is the five-year reversal: the average price 4.5 to
5.5 years ago over today's price. Cheap means it has fallen a long way.
It is the only value signal Atlas Core uses on ETFs. Stock value ratios
(earnings, FCF, sales/EV, book/price) wait for point-in-time fundamentals;
see `data/fundamentals.py`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FROM_DAYS = 1134   # 4.5 years
TO_DAYS = 1386     # 5.5 years


def five_year_reversal(prices: pd.DataFrame) -> pd.DataFrame:
    past = sum(prices.shift(d) for d in range(FROM_DAYS, TO_DAYS + 1, 21))
    past = past / len(range(FROM_DAYS, TO_DAYS + 1, 21))
    return np.log(past / prices)
