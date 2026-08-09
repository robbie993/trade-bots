"""The strategy court — drop a file in, get a ruling you can check.

    evidence  →  backtest (twice, for the determinism juror)
              →  jury of twelve
              →  prosecution vs defence
              →  judge
              →  brain remembers
              →  accepted files become CANDIDATE genomes

The whole trial is stored on one row: the file's hash, the genome read out of
it, every juror's finding, both cases and the ruling. A ruling can therefore
be re-derived months later from the row alone, which is the same standard the
kill switch and the allocator are held to.

The file is never imported and never executed — see ``evidence.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ...db.connection import utcnow_iso
from ...money import D
from ..backtest import Backtester
from ..brain.evolver import BASE_GENOME, Evolver
from ..brain.memory import AgentMemory
from ..config import TradingConfig
from ..data.market_data import MarketData
from ..store import TradingStore
from .advocates import Case, Defence, Prosecution
from .evidence import Evidence, EvidenceError, gather
from .judge import ACCEPT, Judge, Ruling
from .jury import Jury


@dataclass
class StrategyCase:
    id: Optional[int] = None
    evidence: Optional[Evidence] = None
    findings: list = field(default_factory=list)
    prosecution: Optional[Case] = None
    defence: Optional[Case] = None
    ruling: Optional[Ruling] = None
    result: Optional[object] = None
    genome_id: Optional[int] = None
    firm_key: str = ""

    @property
    def verdict(self) -> str:
        return self.ruling.verdict if self.ruling else "unruled"

    def transcript(self) -> str:
        lines = [
            f"CASE #{self.id or '-'} — {self.evidence.name if self.evidence else '?'}",
            f"  sha256 {self.evidence.sha256[:16] if self.evidence else '?'}…",
        ]
        if self.result is not None:
            lines.append(f"  backtest: {self.result.summary()}")
        lines.append("")
        lines.append("JURY")
        for finding in self.findings:
            mark = {"for": "+", "against": "-", "abstain": "·"}[finding.verdict]
            veto = "  [VETO]" if finding.veto else ""
            lines.append(f"  {mark} {finding.juror:<15} {finding.reason}{veto}")
        lines.append("")
        if self.prosecution:
            lines += [self.prosecution.text, ""]
        if self.defence:
            lines += [self.defence.text, ""]
        if self.ruling:
            lines.append(f"RULING: {self.ruling}")
        if self.genome_id:
            lines.append(
                f"  admitted as candidate genome #{self.genome_id} — not deployed; it "
                "reaches a firm only by beating that firm's incumbent, or by your hand"
            )
        return "\n".join(lines)


class StrategyCourt:
    def __init__(
        self,
        store: TradingStore,
        config: Optional[TradingConfig] = None,
        memory: Optional[AgentMemory] = None,
    ):
        self.store = store
        self.config = config or TradingConfig()
        self.jury = Jury(self.config)
        self.judge = Judge()
        self.backtester = Backtester(self.config)
        self.memory = memory or AgentMemory(store, self.config.brain)
        self.evolver = Evolver(store, self.config)

    # -- the trial ---------------------------------------------------------
    def submit(
        self,
        path,
        feed=None,
        firm_key: str = "",
        submitted_by: str = "",
    ) -> StrategyCase:
        evidence = gather(path)
        case = StrategyCase(evidence=evidence, firm_key=firm_key)

        result, replay = self._backtest(evidence, feed, firm_key)
        case.result = result
        case.findings = self.jury.deliberate(evidence, result, replay)
        case.prosecution = Prosecution().build(case.findings)
        case.defence = Defence().build(case.findings)
        case.ruling = self.judge.rule(case.findings, case.prosecution, case.defence)

        if case.ruling.verdict == ACCEPT:
            case.genome_id = self._admit_candidate(case)

        case.id = self._persist(case, submitted_by)
        self._remember(case)
        return case

    def _backtest(self, evidence: Evidence, feed, firm_key: str):
        """Two runs over identical bars — the second is the determinism check."""
        if not evidence.has_genome:
            return None, None
        symbols = list(evidence.universe)
        if not symbols and firm_key:
            firm = self.store.get_firm(firm_key)
            if firm is not None:
                symbols = list(firm.universe)
        if not symbols:
            return None, None

        if feed is None:
            from ..data.feeds import build_feed

            feed = build_feed(self.config.data)

        genome = self.evolver.normalise({**BASE_GENOME, **evidence.genome})
        runs = []
        for _ in range(2):
            try:
                runs.append(
                    self.backtester.run(
                        firm_key=firm_key or "submitted",
                        symbols=symbols,
                        market=MarketData(feed, symbols),
                        genome=genome,
                    )
                )
            except Exception:  # noqa: BLE001 - an unbacktestable file is evidence, not a crash
                return None, None
        return runs[0], runs[1]

    def _admit_candidate(self, case: StrategyCase) -> int:
        """Write an accepted strategy as an unselected candidate genome.

        `selected` is False on purpose. Admission is not deployment: the
        evolver promotes a genome only when it beats the incumbent on the same
        data, and a human can deploy one by hand. Nothing trades because a
        court said it was interesting.
        """
        genome = self.evolver.normalise({**BASE_GENOME, **(case.evidence.genome or {})})
        firm = self.store.get_firm(case.firm_key) if case.firm_key else None
        return self.store.db.insert(
            "strategy_genomes",
            {
                "firm_id": firm.id if firm else None,
                "generation": 0,
                "genome": json.dumps(genome, sort_keys=True),
                "fitness": D(case.result.fitness) if case.result else None,
                "trades": case.result.closed_trades if case.result else 0,
                "selected": False,
                "notes": f"court candidate from {case.evidence.name}",
                "created_at": utcnow_iso(),
            },
        )

    def _persist(self, case: StrategyCase, submitted_by: str) -> int:
        evidence = case.evidence
        firm = self.store.get_firm(case.firm_key) if case.firm_key else None
        return self.store.db.insert(
            "strategy_cases",
            {
                "firm_id": firm.id if firm else None,
                "file_path": str(evidence.path),
                "file_name": evidence.name,
                "file_sha256": evidence.sha256,
                "file_bytes": evidence.size,
                "submitted_by": submitted_by,
                "genome": json.dumps(evidence.genome, sort_keys=True),
                "universe": json.dumps(list(evidence.universe)),
                "evidence": json.dumps(evidence.to_dict(), default=str),
                "findings": json.dumps([f.to_dict() for f in case.findings]),
                "prosecution": case.prosecution.text if case.prosecution else "",
                "defence": case.defence.text if case.defence else "",
                "ruling": case.ruling.verdict if case.ruling else "",
                "ruling_reason": case.ruling.reason if case.ruling else "",
                "confidence": case.ruling.confidence if case.ruling else 0,
                "fitness": D(case.result.fitness) if case.result else None,
                "genome_id": case.genome_id,
                "status": "ruled",
                "created_at": utcnow_iso(),
            },
        )

    def _remember(self, case: StrategyCase) -> None:
        """The brain records the ruling — as a lesson, never as a trade."""
        verdict = case.verdict
        self.memory.remember(
            f"court {verdict.upper()} on {case.evidence.name}: "
            f"{case.ruling.reason if case.ruling else ''}",
            memory_type="lesson",
            payload={
                "key": f"court:{case.evidence.sha256[:16]}",
                "topic": "court",
                "kind": f"ruling_{verdict}",
                "evidence": f"sha256 {case.evidence.sha256[:16]}, "
                f"{len([f for f in case.findings if f.is_against])} juror(s) against",
            },
        )

    # -- reads -------------------------------------------------------------
    def docket(self, limit: int = 20) -> list:
        return self.store.db.query(
            "SELECT * FROM strategy_cases ORDER BY id DESC LIMIT ?", (limit,)
        )

    def case(self, case_id: int) -> Optional[dict]:
        return self.store.db.query_one("SELECT * FROM strategy_cases WHERE id = ?", (case_id,))

    def candidates(self, limit: int = 20) -> list:
        return self.store.db.query(
            "SELECT * FROM strategy_genomes WHERE notes LIKE ? ORDER BY id DESC LIMIT ?",
            ("court candidate%", limit),
        )

    def watch(self, directory=None, feed=None) -> list:
        """Review every strategy file sitting in a directory. Moves nothing."""
        target = Path(directory or (self.config.vendor_dir.parent / "inbox"))
        if not target.exists():
            return []
        cases = []
        for path in sorted(target.iterdir()):
            if path.suffix.lower() not in (".py", ".yaml", ".yml", ".json"):
                continue
            try:
                cases.append(self.submit(path, feed=feed))
            except EvidenceError:
                continue
        return cases


__all__ = ["StrategyCase", "StrategyCourt"]
