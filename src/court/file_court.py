"""The File Court — escalating review, one docket row per file.

Three tiers, each earned by the one before it:

    tier 1  CodeTribunal            every file
    tier 2  /tribunal               only if tier 1 scored above 30
    tier 3  /roundtable panel       only if the running score is above 50

A file that clears tier 1 cleanly never pays for the expensive reviewers. A
file that looks bad gets progressively more adversarial attention.

Verdicts follow the deployment spec's bands:

    > 70  GUILTY
    > 40  MIXED
    else  NOT_GUILTY

with one band the spec did not have and needs:

    (nothing scored it)  UNREVIEWED

An unreviewed file is not an innocent file. Keeping them distinct is the whole
reason this module exists — see the note in backends.py about the
``risk_score: 50`` fallback that made every failure look like a verdict.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..db.connection import Database, to_iso, utcnow
from .backends import ReviewOutcome, RISK_PRECISION, build_backends

GUILTY = "GUILTY"
MIXED = "MIXED"
NOT_GUILTY = "NOT_GUILTY"
UNREVIEWED = "UNREVIEWED"


def sha256_of(path: Path, chunk: int = 65536) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class FileCase:
    """The docket entry for one file."""

    file_name: str
    file_path: str
    file_hash: str = ""
    verdict: str = UNREVIEWED
    risk_score: Optional[Decimal] = None
    summary: str = ""
    outcomes: list = field(default_factory=list)
    reviewed_at: Optional[datetime] = None
    id: Optional[int] = None

    @property
    def unreviewed(self) -> bool:
        return self.verdict == UNREVIEWED

    @property
    def pending_reviewers(self) -> list:
        """Reviewers that were called for but could not run themselves."""
        return [o.backend for o in self.outcomes if not o.available]

    def to_row(self) -> dict:
        return {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "file_hash": self.file_hash,
            "verdict": self.verdict,
            "risk_score": str(self.risk_score) if self.risk_score is not None else None,
            "summary": self.summary,
            "backends": json.dumps([o.to_dict() for o in self.outcomes]),
            "evidence": json.dumps(
                {o.backend: o.raw.get("evidence") for o in self.outcomes if o.raw}
            ),
            "investigation": json.dumps(
                {o.backend: o.raw.get("investigation") for o in self.outcomes if o.raw}
            ),
            "trial_transcript": json.dumps(
                {o.backend: o.raw.get("trial") for o in self.outcomes if o.raw}
            ),
            "reviewed_at": to_iso(self.reviewed_at),
        }


def aggregate(outcomes: list, config) -> tuple[str, Optional[Decimal], str]:
    """Combine reviewer scores into a verdict.

    Only reviewers that actually ran get a vote. If none did, the answer is
    UNREVIEWED — never NOT_GUILTY, which would clear a file nobody read, and
    never MIXED, which would invent a middling score out of nothing.
    """
    scored = [o for o in outcomes if o.counted]
    if not scored:
        missing = ", ".join(o.backend for o in outcomes) or "none configured"
        return UNREVIEWED, None, f"No reviewer produced a score ({missing})."

    total = sum((o.score for o in scored), Decimal("0"))
    avg = (total / Decimal(len(scored))).quantize(RISK_PRECISION)

    if avg > config.guilty_above:
        verdict = GUILTY
    elif avg > config.mixed_above:
        verdict = MIXED
    else:
        verdict = NOT_GUILTY

    voters = ", ".join(f"{o.backend}={o.score}" for o in scored)
    return verdict, avg, f"{len(scored)} reviewer(s): {voters}."


class FileCourt:
    """Runs files through the tiers and writes the docket."""

    def __init__(self, db: Database, config, backends: Optional[dict] = None):
        self.db = db
        self.config = config
        self.backends = backends if backends is not None else build_backends(config)

    # -- the pipeline -----------------------------------------------------
    def process_file(self, file_path) -> FileCase:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"no such file: {path}")

        case = FileCase(
            file_name=path.name,
            file_path=str(path.resolve()),
            file_hash=sha256_of(path),
        )

        outcomes = self._run_tier("tier1", path)
        running = self._running_score(outcomes)

        if running is not None and running > self.config.tribunal_above:
            outcomes += self._run_tier("tier2", path)
            running = self._running_score(outcomes)

        if running is not None and running > self.config.panel_above:
            outcomes += self._run_tier("tier3", path)

        case.outcomes = outcomes
        case.verdict, case.risk_score, case.summary = aggregate(outcomes, self.config)
        case.reviewed_at = utcnow()
        self.record(case)
        return case

    def _run_tier(self, tier: str, path: Path) -> list:
        results = []
        for backend in self.backends.get(tier, []):
            try:
                results.append(backend.review(path))
            except Exception as exc:  # a reviewer crashing is not a verdict
                results.append(
                    ReviewOutcome.unavailable(backend.name, f"raised {type(exc).__name__}: {exc}")
                )
        return results

    @staticmethod
    def _running_score(outcomes: list) -> Optional[Decimal]:
        scored = [o for o in outcomes if o.counted]
        if not scored:
            return None
        total = sum((o.score for o in scored), Decimal("0"))
        return (total / Decimal(len(scored))).quantize(RISK_PRECISION)

    # -- persistence ------------------------------------------------------
    def record(self, case: FileCase) -> FileCase:
        row = case.to_row()
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        self.db.execute(
            f"INSERT INTO file_cases ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(row[c] for c in columns),
        )
        found = self.db.query(
            "SELECT id FROM file_cases WHERE file_hash = ? ORDER BY id DESC LIMIT 1",
            (case.file_hash,),
        )
        if found:
            case.id = found[0]["id"]
        return case

    def cases(self, limit: int = 50) -> list:
        return self.db.query(
            "SELECT * FROM file_cases ORDER BY id DESC LIMIT ?", (limit,)
        )

    def pending(self, limit: int = 50) -> list:
        """Cases still waiting on a reviewer a human has to trigger."""
        return self.db.query(
            "SELECT * FROM file_cases WHERE verdict = ? ORDER BY id DESC LIMIT ?",
            (UNREVIEWED, limit),
        )
