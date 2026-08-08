"""The File Court — escalating multi-reviewer file review."""

from __future__ import annotations

from .backends import (
    CodeTribunalBackend,
    CommandBackend,
    InteractiveSkillBackend,
    ReviewBackend,
    ReviewOutcome,
    build_backends,
)
from .file_court import (
    GUILTY,
    MIXED,
    NOT_GUILTY,
    UNREVIEWED,
    FileCase,
    FileCourt,
    aggregate,
)

__all__ = [
    "CodeTribunalBackend",
    "CommandBackend",
    "InteractiveSkillBackend",
    "ReviewBackend",
    "ReviewOutcome",
    "build_backends",
    "FileCase",
    "FileCourt",
    "aggregate",
    "GUILTY",
    "MIXED",
    "NOT_GUILTY",
    "UNREVIEWED",
]
