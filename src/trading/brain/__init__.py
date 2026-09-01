"""The Brain — memory, evolution, and what the two of them imply.

    memory.py    every trade, recallable and quotable
    evolver.py   deterministic mutation and selection over strategy genomes
    crucible.py  the walk-forward gauntlet: windows the evolver cannot read
    lock.py      certificates, and what a proof licenses
    learning.py  conclusions across firms, stated with their evidence
"""

from __future__ import annotations

from .crucible import (
    Crucible,
    CrucibleReport,
    Fold,
    FoldResult,
    NotEnoughHistory,
    Window,
    WindowedFeed,
    split,
)
from .evolver import BASE_GENOME, GENES, Candidate, Evolver, Generation
from .learning import Learner, Lesson
from .lock import (
    Certificate,
    GenomeNotCertified,
    LockDecision,
    WalkForwardLock,
    genome_fingerprint,
)
from .memory import AgentMemory, Memory

__all__ = [
    "BASE_GENOME",
    "GENES",
    "AgentMemory",
    "Candidate",
    "Certificate",
    "Crucible",
    "CrucibleReport",
    "Evolver",
    "Fold",
    "FoldResult",
    "Generation",
    "GenomeNotCertified",
    "Learner",
    "Lesson",
    "LockDecision",
    "Memory",
    "NotEnoughHistory",
    "WalkForwardLock",
    "Window",
    "WindowedFeed",
    "genome_fingerprint",
    "split",
]
