"""Quality (Asness, Frazzini & Pedersen, "Quality Minus Junk"). NOT BUILT.

Needs point-in-time fundamentals; see `data/fundamentals.py`. Quality has
no meaning for an index ETF, so Atlas Core runs without it.
"""
from ..data.fundamentals import load as _load


def quality_score(*args, **kwargs):
    _load()
