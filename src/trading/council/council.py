"""The council — the village deciding for itself what a human used to decide.

The village already had a body that reaches a defensible verdict from evidence
rather than from an opinion: the strategy court. This is that machinery pointed
at the decisions the approval gate was holding — raise this allocation, kill
this firm, un-pause that one, move capital between these two.

Three verdicts, and the third is the important one:

``GRANT``   the panel carried it; the approval row is approved and the tick
            carries it out.
``REFUSE``  the panel rejected it; the row is rejected with its reasons.
``DEFER``   the panel could not tell, or a veto fired. The row stays pending
            and waits for a human, exactly as it did before.

A body that can only say yes or no will say one of them when it should have
said neither. ``DEFER`` is what keeps the council honest, and it is why
switching autonomy on does not mean nothing is ever asked of you — it means
you are only asked about the cases the evidence does not settle.

**What the council may never decide.** Sending an order to a live venue. That
is the one act in the system that leaves it: irreversible, outside the ledger,
real money at a real broker. Everything else the council rules on moves numbers
between rows in a database that reconciles, and can be read back and undone.
The boundary is not a matter of configuration — ``LIVE_TRADING`` is not in
``PANELS`` and there is a test that asserts it never will be.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ...db.connection import utcnow_iso
from ...money import D, ZERO, percent
from ..court.advocates import Defence, Prosecution
from . import jurors
from .evidence import CouncilEvidence, gather

GRANT = "grant"
REFUSE = "refuse"
DEFER = "defer"

# Which panel hears which decision. A decision with no panel is not the
# council's to make — it stays pending for a human, which is why adding a new
# approval action never silently becomes autonomous.
PANELS = {
    "allocate_capital": jurors.RAISE,
    "capital_transfer": jurors.TRANSFER,
    "recruit": jurors.RECRUIT,
    "kill_firm": jurors.KILL,
    "resume_firm": jurors.RESUME,
}


# The approval actions the council hears. Kept beside PANELS rather than
# derived from it because PANELS is keyed partly by `kind` (a capital transfer
# is an allocate_capital row), and a set of *actions* is what the gate wants.
COUNCIL_ACTIONS = ("allocate_capital", "kill_firm", "resume_firm")


@dataclass
class CouncilRuling:
    verdict: str
    reason: str
    action: str = ""
    firm_key: str = ""
    approval_id: Optional[int] = None
    confidence: Decimal = ZERO
    for_weight: Decimal = ZERO
    against_weight: Decimal = ZERO
    vetoes: tuple = ()
    findings: tuple = ()
    evidence: Optional[CouncilEvidence] = None
    id: Optional[int] = None
    created_at: str = ""
    digest: str = ""
    transcript: str = field(default="", repr=False)

    @property
    def granted(self) -> bool:
        return self.verdict == GRANT

    @property
    def deferred(self) -> bool:
        return self.verdict == DEFER

    def __str__(self) -> str:
        head = f"{self.verdict.upper()}"
        if self.firm_key:
            head += f" {self.firm_key}"
        return f"{head} — {self.reason} (for {self.for_weight} / against {self.against_weight})"


class Council:
    """Deterministic. Same evidence in, same ruling out, every time."""

    def __init__(self, db=None, margin: Decimal = D("20")):
        self.db = db
        # Below the margin the panel is saying it cannot tell them apart, and
        # a coin-flip on capital is worse than an unanswered question.
        self.margin = D(margin)

    # -- ruling ------------------------------------------------------------
    def panel_for(self, evidence: CouncilEvidence):
        # `kind` narrows an action: a transfer and a recruit are both
        # allocate_capital rows, and they are not the same decision.
        if evidence.kind in ("capital_transfer", "recruit"):
            return PANELS.get(evidence.kind)
        return PANELS.get(evidence.action)

    def rule(self, evidence: CouncilEvidence) -> CouncilRuling:
        panel = self.panel_for(evidence)
        if panel is None:
            return CouncilRuling(
                DEFER,
                f"the council has no panel for {evidence.action or 'this'}; "
                "it stays with a human",
                action=evidence.action,
                firm_key=evidence.firm_key,
                evidence=evidence,
            )

        findings = tuple(juror(evidence) for juror in panel)
        prosecution = Prosecution().build(findings)
        defence = Defence().build(findings)
        vetoes = tuple(f.juror for f in findings if f.veto and f.is_against)

        if vetoes:
            reasons = "; ".join(f.reason for f in findings if f.veto and f.is_against)
            return self._ruling(DEFER, reasons, evidence, findings,
                                defence.weight, prosecution.weight, vetoes,
                                confidence=D("100"))

        total = defence.weight + prosecution.weight
        if total == ZERO:
            return self._ruling(
                DEFER, "no juror found anything to say", evidence, findings,
                defence.weight, prosecution.weight, vetoes,
            )

        margin = defence.weight - prosecution.weight
        share = (max(defence.weight, prosecution.weight) / total) * D("100")
        confidence = percent(max(ZERO, min(D("100"), (share - D("50")) * D("2"))))

        if margin >= self.margin:
            verdict = GRANT
            reason = f"the panel carried it {defence.weight} to {prosecution.weight}"
        elif margin <= -self.margin:
            verdict = REFUSE
            reason = f"the panel rejected it {prosecution.weight} to {defence.weight}"
        else:
            verdict = DEFER
            reason = (
                f"too close to call ({defence.weight} to {prosecution.weight}); "
                "the evidence does not settle it, so it waits for a human"
            )
        return self._ruling(verdict, reason, evidence, findings,
                            defence.weight, prosecution.weight, vetoes,
                            confidence=confidence,
                            transcript=f"{defence.text}\n\n{prosecution.text}")

    def _ruling(self, verdict, reason, evidence, findings, for_weight,
                against_weight, vetoes, confidence=ZERO, transcript="") -> CouncilRuling:
        return CouncilRuling(
            verdict=verdict,
            reason=reason,
            action=evidence.action,
            firm_key=evidence.firm_key,
            confidence=confidence,
            for_weight=for_weight,
            against_weight=against_weight,
            vetoes=vetoes,
            findings=findings,
            evidence=evidence,
            transcript=transcript,
        )

    # -- the record --------------------------------------------------------
    def record(self, ruling: CouncilRuling, approval_id: Optional[int] = None) -> CouncilRuling:
        """Write the ruling down. A decision nobody can review is not oversight."""
        if self.db is None:
            return ruling
        ruling.approval_id = approval_id
        ruling.created_at = utcnow_iso()
        ruling.digest = digest_of(ruling.evidence)
        try:
            ruling.id = self.db.insert(
                "council_rulings",
                {
                    "approval_id": approval_id,
                    "action": ruling.action,
                    "firm_key": ruling.firm_key,
                    "verdict": ruling.verdict,
                    "reason": ruling.reason[:400],
                    "confidence": ruling.confidence,
                    "for_weight": ruling.for_weight,
                    "against_weight": ruling.against_weight,
                    "findings": json.dumps(
                        [f.to_dict() for f in ruling.findings], default=str
                    ),
                    "evidence": json.dumps(
                        ruling.evidence.to_dict() if ruling.evidence else {}, default=str
                    ),
                    "evidence_digest": ruling.digest,
                    "created_at": ruling.created_at,
                },
            )
        except Exception:  # noqa: BLE001 - a ruling is not lost because the log failed
            return ruling
        return ruling

    def already_heard(self, approval_id: Optional[int], evidence) -> bool:
        """Has this exact request already been ruled on, on these exact facts?

        Without this the council reconsiders every pending row on every tick
        and writes an identical ruling each time — a hundred and nineteen
        copies of one decision, which is not an audit trail, it is noise. The
        digest is of the gathered evidence, so a deferred request is reheard
        the moment anything it depends on actually moves.
        """
        if self.db is None or approval_id is None:
            return False
        try:
            row = self.db.query_one(
                "SELECT evidence_digest FROM council_rulings WHERE approval_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (approval_id,),
            )
        except Exception:  # noqa: BLE001
            return False
        return bool(row) and (row.get("evidence_digest") or "") == digest_of(evidence)

    def recent(self, limit: int = 20) -> list:
        if self.db is None:
            return []
        try:
            rows = self.db.query(
                "SELECT * FROM council_rulings ORDER BY id DESC LIMIT ?", (limit,)
            )
        except Exception:  # noqa: BLE001
            return []
        return rows


def digest_of(evidence) -> str:
    """A stable fingerprint of the facts a ruling was reached on."""
    if evidence is None:
        return ""
    payload = json.dumps(evidence.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def consider(eco, approval, market=None) -> CouncilRuling:
    """Gather the evidence and rule on one pending approval."""
    council = Council(eco.db)
    return council.rule(gather(eco, approval, market))


__all__ = [
    "COUNCIL_ACTIONS",
    "Council",
    "CouncilRuling",
    "DEFER",
    "GRANT",
    "PANELS",
    "REFUSE",
    "consider",
    "digest_of",
]
