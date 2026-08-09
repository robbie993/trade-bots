"""The strategy court — a dropped file, tried and ruled on.

    evidence.py   forensics: read the file, never run it
    jury.py       twelve deterministic jurors, three of them vetoes
    advocates.py  prosecution and defence, citing only the jurors
    judge.py      accept / modify / reject
    docket.py     the trial, and the case row it leaves behind

Distinct from ``src/court`` (the product village's File Court), which reviews
arbitrary files for code quality. This one asks a narrower question with a
checkable answer: should this strategy be allowed near money?
"""

from __future__ import annotations

from .advocates import Case, Defence, Prosecution
from .docket import StrategyCase, StrategyCourt
from .evidence import Evidence, EvidenceError, gather
from .judge import ACCEPT, MODIFY, REJECT, Judge, Ruling
from .jury import Finding, Jury

__all__ = [
    "ACCEPT",
    "MODIFY",
    "REJECT",
    "Case",
    "Defence",
    "Evidence",
    "EvidenceError",
    "Finding",
    "Judge",
    "Jury",
    "Prosecution",
    "Ruling",
    "StrategyCase",
    "StrategyCourt",
    "gather",
]
