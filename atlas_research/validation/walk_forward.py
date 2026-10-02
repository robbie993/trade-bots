"""Walk-forward windows and the frozen final test.

Each window: the GA searches TRAIN, picks its champion on VALIDATE, and the
champion is then run once on TEST. TEST never feeds back into anything. The
TEST years of all windows, stitched end to end, are an out-of-sample record
of the *process* (search + selection), which is the thing that would be
running live.

The data begins in 2000 and an ETF needs a year of history to be eligible,
so every TRAIN starts in 2002 rather than 2005.

The final champion is chosen on TRAIN 2002-2022 / VALIDATE 2023-2024 and
then frozen. `FINAL_OOS` (2025-01-01 to the end of the data) is sealed:
`run_core.py` will only run it from a frozen champion file, once.
"""
from __future__ import annotations

TRAIN_START = "2002-01-01"

WINDOWS = [
    # (name, train_end, validate, test)
    ("W1", "2009-12-31", ("2010-01-01", "2011-12-31"), ("2012-01-01", "2013-12-31")),
    ("W2", "2011-12-31", ("2012-01-01", "2013-12-31"), ("2014-01-01", "2015-12-31")),
    ("W3", "2013-12-31", ("2014-01-01", "2015-12-31"), ("2016-01-01", "2017-12-31")),
    ("W4", "2015-12-31", ("2016-01-01", "2017-12-31"), ("2018-01-01", "2019-12-31")),
    ("W5", "2017-12-31", ("2018-01-01", "2019-12-31"), ("2020-01-01", "2021-12-31")),
    ("W6", "2019-12-31", ("2020-01-01", "2021-12-31"), ("2022-01-01", "2023-12-31")),
    ("W7", "2021-12-31", ("2022-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31")),
]

FINAL_TRAIN = (TRAIN_START, "2022-12-31")
FINAL_VALIDATE = ("2023-01-01", "2024-12-31")
FINAL_OOS = ("2025-01-01", "2026-12-31")
SEALED_FROM = "2025-01-01"


def check_unsealed(end: str, allow_oos: bool = False) -> None:
    """Refuse to look at the sealed period unless explicitly running the OOS."""
    if end >= SEALED_FROM and not allow_oos:
        raise PermissionError(
            f"{end} is inside the frozen OOS period (from {SEALED_FROM}); "
            "only `run_core.py oos` may read it, once, from a frozen champion"
        )
