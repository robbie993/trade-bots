"""ASTRAL TP 6: A BIG FIXED PERCENTAGE.

+8% for equities and +15% for crypto, both from the average entry. No
model at all: the number is a statement that a 15m entry is worth holding
for a swing-sized move, and it is the easiest of the ten to read in a log.

Everything else is Astral, unchanged: see bots/astral.py, and
`run` there for how a target interacts with the other exits. Not in the
village: no firm names this file.
"""

from bots.astral import run

EQUITY_TARGET = 0.08
CRYPTO_TARGET = 0.15


def targets(context, symbol, read, entry, basket):
    pct = CRYPTO_TARGET if "-" in str(symbol) else EQUITY_TARGET
    return [(entry * (1 + pct), 0.0)]


def propose(context):
    return run(context, take_profit=targets)
