"""The walk-forward lock — what a proof is worth, and what it licenses.

``crucible.py`` produces a verdict. This file is what makes the verdict bind:
a passing gauntlet writes a **certificate**, and a live venue will not take an
order from a firm whose *current* genome does not hold one.

The certificate names a fingerprint, not a firm. That single choice is the
lock:

* Mutate one gene and the fingerprint changes. The certificate stops matching
  and the firm is uncertified again — so a strategy cannot evolve its way
  onto real money between proofs. Promotion on a live firm is refused by the
  evolver for the same reason; this is the second lock behind that one, and
  it holds even if a genome is edited by hand, imported, restored from a
  backup, or promoted by a future code path nobody has written yet.
* A proof is bound to the data that produced it. A certificate earned on the
  synthetic random walk does not license real money, because beating a seeded
  PRNG is a fact about the PRNG. That refusal is on by default.
* A proof expires. Microstructure moves; a gauntlet run against last year's
  history is evidence about last year.

What this is not: a promise the strategy works. It is a record that a specific
genome was measured on data that could not have shaped it, and the date that
measurement was made. Everything else here exists so that record cannot be
quietly detached from the money.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Sequence

from ...db.connection import to_datetime, utcnow, utcnow_iso
from ...money import D, ZERO
from ..config import TradingConfig
from ..models import FirmRecord
from ..store import TradingStore

CERTIFICATES = "genome_certificates"


class GenomeNotCertified(RuntimeError):
    """This genome has no live licence. Carries the reason it does not."""


def genome_fingerprint(genome: dict) -> str:
    """A stable name for one exact parameter set.

    Canonical JSON, then sha256, truncated to 16 hex characters — short enough
    to read out in a log line and long enough that two genomes never collide
    by accident. Values are stringified first because ``10`` and ``"10"``
    reach this function from the database and from a config file
    respectively, and they are the same strategy.
    """
    canonical = {str(k): str(v) for k, v in sorted((genome or {}).items())}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def data_fingerprint(source: str, symbols: Sequence[str], bars: int) -> str:
    """What the proof was earned on. Different data, different certificate."""
    blob = f"{source}|{','.join(sorted(s.upper() for s in symbols))}|{bars}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class Certificate:
    """One gauntlet result, as stored. A failed one is still a record."""

    firm_key: str = ""
    genome: dict = field(default_factory=dict)
    fingerprint: str = ""
    data_source: str = ""
    data_fingerprint: str = ""
    bars: int = 0
    folds: int = 0
    folds_passed: int = 0
    verdict: bool = False
    in_sample_fitness: Decimal = ZERO
    oos_fitness: Decimal = ZERO
    oos_return_pct: Decimal = ZERO
    oos_max_drawdown_pct: Decimal = ZERO
    oos_sharpe: Optional[Decimal] = None
    oos_trades: int = 0
    reasons: list = field(default_factory=list)
    report: dict = field(default_factory=dict)
    firm_id: Optional[int] = None
    created_at: Optional[str] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row: dict) -> "Certificate":
        return cls(
            id=row.get("id"),
            firm_id=row.get("firm_id"),
            firm_key=row.get("firm_key") or "",
            genome=_loads(row.get("genome"), {}),
            fingerprint=row.get("fingerprint") or "",
            data_source=row.get("data_source") or "",
            data_fingerprint=row.get("data_fingerprint") or "",
            bars=int(row.get("bars") or 0),
            folds=int(row.get("folds") or 0),
            folds_passed=int(row.get("folds_passed") or 0),
            verdict=bool(row.get("verdict")),
            in_sample_fitness=D(row.get("in_sample_fitness")),
            oos_fitness=D(row.get("oos_fitness")),
            oos_return_pct=D(row.get("oos_return_pct")),
            oos_max_drawdown_pct=D(row.get("oos_max_drawdown_pct")),
            oos_sharpe=D(row["oos_sharpe"]) if row.get("oos_sharpe") is not None else None,
            oos_trades=int(row.get("oos_trades") or 0),
            reasons=[r for r in (row.get("reasons") or "").split("\n") if r],
            report=_loads(row.get("report"), {}),
            created_at=row.get("created_at"),
        )

    def age_days(self) -> Optional[int]:
        stamp = to_datetime(self.created_at)
        return (utcnow() - stamp).days if stamp else None

    def summary(self) -> str:
        verdict = "PASSED" if self.verdict else "FAILED"
        age = self.age_days()
        return (
            f"{self.firm_key} {self.fingerprint} {verdict} "
            f"{self.folds_passed}/{self.folds} folds on {self.data_source} "
            f"({self.bars} bars): {self.oos_return_pct}% out-of-sample return, "
            f"{self.oos_max_drawdown_pct}% drawdown, {self.oos_trades} trades"
            + (f", {age} day(s) old" if age is not None else "")
        )


@dataclass(frozen=True)
class LockDecision:
    """Whether this genome may trade live, and why."""

    allowed: bool
    reason: str
    certificate: Optional[Certificate] = None

    def __bool__(self) -> bool:
        return self.allowed


class WalkForwardLock:
    """Reads and writes certificates, and answers the one question that matters."""

    def __init__(self, store: TradingStore, config: Optional[TradingConfig] = None):
        self.store = store
        self.config = config or TradingConfig()

    @property
    def settings(self):
        return self.config.crucible

    # -- writing ----------------------------------------------------------
    def certify(self, report, firm: Optional[FirmRecord] = None) -> Certificate:
        """Store a gauntlet result — pass or fail — and return it.

        Failures are stored deliberately. Without them, a genome that could not
        survive its holdout can be re-proposed indefinitely and nothing in the
        ledger remembers that it was already tried and already failed.
        """
        certificate = Certificate(
            firm_key=report.firm_key,
            firm_id=firm.id if firm is not None else None,
            genome=dict(report.genome),
            fingerprint=genome_fingerprint(report.genome),
            data_source=report.data_source,
            data_fingerprint=data_fingerprint(
                report.data_source, report.symbols, report.total_bars
            ),
            bars=report.total_bars,
            folds=len(report.folds),
            folds_passed=report.folds_passed,
            verdict=report.passed,
            in_sample_fitness=report.in_sample_fitness,
            oos_fitness=report.oos_fitness,
            oos_return_pct=report.oos_return_pct,
            oos_max_drawdown_pct=report.oos_max_drawdown_pct,
            oos_sharpe=report.oos_sharpe,
            oos_trades=report.oos_trades,
            reasons=list(report.reasons),
            report=report.to_payload(),
        )
        certificate.created_at = utcnow_iso()
        certificate.id = self.store.db.insert(
            CERTIFICATES,
            {
                "firm_id": certificate.firm_id,
                "firm_key": certificate.firm_key,
                "genome": json.dumps(certificate.genome, sort_keys=True),
                "fingerprint": certificate.fingerprint,
                "data_source": certificate.data_source,
                "data_fingerprint": certificate.data_fingerprint,
                "bars": certificate.bars,
                "folds": certificate.folds,
                "folds_passed": certificate.folds_passed,
                "verdict": certificate.verdict,
                "in_sample_fitness": certificate.in_sample_fitness,
                "oos_fitness": certificate.oos_fitness,
                "oos_return_pct": certificate.oos_return_pct,
                "oos_max_drawdown_pct": certificate.oos_max_drawdown_pct,
                "oos_sharpe": certificate.oos_sharpe,
                "oos_trades": certificate.oos_trades,
                "reasons": "\n".join(certificate.reasons),
                "report": json.dumps(certificate.report, sort_keys=True),
                "created_at": certificate.created_at,
            },
        )
        self.store.record_event(
            "crucible",
            f"{report.firm_key}: genome {certificate.fingerprint} "
            + (
                f"passed the crucible ({report.folds_passed}/{len(report.folds)} folds "
                f"on {report.data_source})"
                if report.passed
                else f"failed the crucible — {'; '.join(report.reasons) or 'no reason recorded'}"
            ),
            firm_id=certificate.firm_id,
            payload={"fingerprint": certificate.fingerprint, "passed": report.passed},
        )
        return certificate

    # -- reading ----------------------------------------------------------
    def certificates(self, firm_key: Optional[str] = None, limit: int = 20) -> list:
        sql = f"SELECT * FROM {CERTIFICATES}"
        params: list = []
        if firm_key:
            sql += " WHERE firm_key = ?"
            params.append(firm_key)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [Certificate.from_row(r) for r in self.store.db.query(sql, tuple(params))]

    def certificate_for(self, genome: dict, firm_key: Optional[str] = None) -> Optional[Certificate]:
        """The most recent *passing* certificate for this exact genome."""
        fingerprint = genome_fingerprint(genome)
        sql = f"SELECT * FROM {CERTIFICATES} WHERE fingerprint = ? AND verdict = ?"
        params: list = [fingerprint, True]
        if firm_key:
            sql += " AND firm_key = ?"
            params.append(firm_key)
        sql += " ORDER BY id DESC LIMIT 1"
        row = self.store.db.query_one(sql, tuple(params))
        return Certificate.from_row(row) if row else None

    # -- the gate ---------------------------------------------------------
    def check(self, firm: FirmRecord) -> LockDecision:
        """May this firm's current genome be sent to a live venue?

        Every refusal names the fingerprint it was looking for. A lock whose
        error message is "not allowed" gets disabled by the first person who
        hits it at nine in the morning.
        """
        settings = self.settings
        fingerprint = genome_fingerprint(firm.genome or {})
        venue = (firm.venue or "paper").strip().lower()
        if venue in ("", "paper"):
            # Paper is where a strategy is *supposed* to be unproven. Gating it
            # would only teach the operator to turn the gate off.
            return LockDecision(
                True, f"{firm.firm_key} trades paper; the lock gates live venues only"
            )
        if not settings.required_for_live:
            return LockDecision(
                True,
                f"the walk-forward lock is switched off (TRADE_CRUCIBLE_REQUIRED=0); "
                f"genome {fingerprint} is trading live unproven",
            )

        certificate = self.certificate_for(firm.genome or {}, firm.firm_key)
        if certificate is None:
            latest = self.certificates(firm.firm_key, limit=1)
            trailer = (
                f" The most recent gauntlet on this firm was for {latest[0].fingerprint}"
                f" ({'passed' if latest[0].verdict else 'failed'}), not this genome."
                if latest
                else ""
            )
            return LockDecision(
                False,
                f"{firm.firm_key}: genome {fingerprint} holds no passing crucible "
                f"certificate, so it may not trade {firm.venue}. Run "
                f"`python -m src.main trade crucible {firm.firm_key}`." + trailer,
            )

        if certificate.data_source.startswith("synthetic") and not settings.allow_synthetic:
            return LockDecision(
                False,
                f"{firm.firm_key}: genome {fingerprint} was proved on "
                f"{certificate.data_source} — a seeded random walk licenses nothing. "
                "Re-run the crucible on real history (TRADE_DATA_SOURCE=csv or yahoo).",
                certificate,
            )

        age = certificate.age_days()
        ttl = settings.certificate_ttl_days
        if ttl and age is not None and age > ttl:
            return LockDecision(
                False,
                f"{firm.firm_key}: the certificate for genome {fingerprint} is {age} days "
                f"old, past the {ttl}-day limit. A proof about last year's market is "
                "evidence about last year; run the crucible again.",
                certificate,
            )

        return LockDecision(
            True,
            f"{firm.firm_key}: genome {fingerprint} passed {certificate.folds_passed}/"
            f"{certificate.folds} out-of-sample folds on {certificate.data_source}"
            + (f", {age} day(s) ago" if age is not None else ""),
            certificate,
        )

    def require(self, firm: FirmRecord) -> Certificate:
        decision = self.check(firm)
        if not decision.allowed:
            raise GenomeNotCertified(decision.reason)
        return decision.certificate


def _loads(raw, default):
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


__all__ = [
    "CERTIFICATES",
    "Certificate",
    "GenomeNotCertified",
    "LockDecision",
    "WalkForwardLock",
    "data_fingerprint",
    "genome_fingerprint",
]
