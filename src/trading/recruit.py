"""Recruitment — drop a bot in, and it becomes a firm in the village.

The court already reads a dropped strategy file safely: parsed with ``ast``,
never imported, never executed, twelve jurors and a ruling. What it did *not*
do was let the file become a firm. An accepted strategy was admitted as an
unselected candidate genome and stopped there, so there was no way to bring
your own bots into the village and watch them compete.

This is that path, and it holds the same line the rest of the system holds.

**The trial is not optional.** Recruitment goes through the same court as
``trade court-submit``. A file that imports ``socket``, or evals at runtime, or
cannot be parsed, is not recruited — it is rejected with reasons, exactly as
before. There is no "trusted" flag that skips the jury.

**A recruit is born paused and broke.** Creating a funded, trading firm from a
file would be starting the bleeding — the one thing this system may never do on
its own. So the firm is created at ``paused`` with an allocation of zero, and
the capital is a separate ``ALLOCATE_CAPITAL`` request. Approving it funds and
activates the firm in one step. Until then the recruit exists, is visible, and
trades nothing.

**A verdict of MODIFY does not recruit.** The court's middle verdict means it
could not tell — the same thing the council's DEFER means. A body that treats
"I don't know" as a yes is not a body worth having.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..agents.human_gate import ApprovalAction
from ..money import D, ZERO, fmt_money, money
from .court.judge import ACCEPT
from .models import FirmRecord, FirmStatus

# What a recruited firm is called if the file does not say. Kept to the
# characters a firm_key is allowed, because it becomes a directory name in the
# vault and a node id in the village map.
_SAFE = re.compile(r"[^a-z0-9_]+")


class RecruitError(ValueError):
    """The file cannot become a firm, and why."""


@dataclass
class Recruitment:
    firm_key: str
    case = None
    firm: Optional[FirmRecord] = None
    approval_id: Optional[int] = None
    requested: Decimal = ZERO
    reason: str = ""
    accepted: bool = False
    notes: list = field(default_factory=list)

    def __str__(self) -> str:
        if not self.accepted:
            return f"{self.firm_key}: not recruited — {self.reason}"
        return (
            f"{self.firm_key}: recruited, paused, awaiting approval "
            f"#{self.approval_id} for {fmt_money(self.requested)}"
        )


def firm_key_for(name: str, taken=()) -> str:
    """A stable, safe key from a file name, with collisions numbered."""
    stem = Path(str(name)).stem.lower().replace("-", "_")
    key = _SAFE.sub("_", stem).strip("_")[:32] or "recruit"
    if key not in taken:
        return key
    for suffix in range(2, 100):
        candidate = f"{key}_{suffix}"[:36]
        if candidate not in taken:
            return candidate
    raise RecruitError(f"too many firms named {key}")


def stake_for(eco, requested: Optional[Decimal] = None) -> Decimal:
    """What to ask for a new firm.

    Defaults to the configured per-firm allocation, trimmed to whatever is left
    under the brokerage's total capital. It is only ever a *request*: nothing
    here moves a penny.
    """
    config = eco.config.brokerage
    deployed = sum(
        (D(f.allocation) for f in eco.store.firms() if not f.is_killed), ZERO
    )
    headroom = money(D(config.total_capital) - deployed)
    want = money(D(requested)) if requested is not None else money(
        D(eco.config.firm.allocation)
    )
    return money(max(ZERO, min(want, headroom)))


def recruit(eco, path, submitted_by: str = "", stake: Optional[Decimal] = None,
            name: str = "") -> Recruitment:
    """Try a bot file, and if the court clears it, enrol it as a firm."""
    path = Path(str(path))
    case = eco.court.submit(path, submitted_by=submitted_by or "recruiter")
    key = firm_key_for(name or path.name, taken={f.firm_key for f in eco.store.firms()})
    out = Recruitment(firm_key=key)
    out.case = case

    if case.ruling.verdict != ACCEPT:
        out.reason = f"the court returned {case.ruling.verdict.upper()} — {case.ruling.reason}"
        return out
    if not case.evidence.universe:
        out.reason = "no UNIVERSE in the file; a firm with nothing to trade is not a firm"
        return out

    requested = stake_for(eco, stake)
    if requested <= ZERO:
        out.reason = (
            "no capital left under the brokerage's total; retire a firm or raise "
            "TRADE_TOTAL_CAPITAL before recruiting another"
        )
        return out

    # Paused and unfunded. The approval below is what makes it trade.
    record = eco.store.upsert_firm(FirmRecord(
        firm_key=key,
        name=name or case.evidence.name or key,
        asset_class=str(case.evidence.declared.get("asset_class") or "recruited"),
        strategy=str(case.evidence.declared.get("strategy") or "dropped file"),
        venue="paper",
        risk_limit=D(eco.config.firm.risk_limit),
        initial_allocation=ZERO,
        allocation=ZERO,
        cash=ZERO,
        high_water_mark=ZERO,
        genome=case.evidence.genome,
        universe=list(case.evidence.universe),
        status=FirmStatus.PAUSED.value,
    ))
    out.firm = record
    out.requested = requested

    approval = eco.gate.request(
        ApprovalAction.ALLOCATE_CAPITAL.value,
        f"Fund recruited firm {key} with {fmt_money(requested)} "
        f"(from {path.name}; court said ACCEPT)",
        amount=requested,
        details={
            "kind": "recruit",
            "firm": key,
            "case": case.id,
            "file": path.name,
            "old_allocation": "0",
            "new_allocation": str(requested),
            "reason": f"recruited from {path.name}",
        },
        dedupe_key=f"recruit:{key}",
    )
    out.approval_id = approval.id
    out.accepted = True

    eco.store.record_event(
        "recruited",
        f"{key} recruited from {path.name}; paused and unfunded until approval "
        f"#{approval.id}",
        firm_id=record.id,
        payload={"case": case.id, "requested": str(requested), "file": path.name},
    )
    eco.flow.move("court", "gate", f"{key} recruited — needs funding",
                  kind="blocked", firm=key, detail=str(out))
    return out


def fund(eco, firm_key: str, allocation: Decimal, by: str = "approval") -> dict:
    """Carry out an approved recruitment: fund the firm and let it trade.

    One step rather than two, because a recruit that was funded but left paused
    would need a second approval to do the thing the first one was for.
    """
    firm = eco.store.get_firm(firm_key)
    if firm is None:
        raise RecruitError(f"unknown firm {firm_key}")
    amount = money(D(allocation))
    eco.store.set_allocation(firm.id, amount, amount)
    eco.store.set_firm_status(firm.id, FirmStatus.ACTIVE.value, f"funded by {by}")
    eco.store.record_event(
        "recruit_funded",
        f"{firm_key} funded with {fmt_money(amount)} by {by} and set trading",
        firm_id=firm.id,
        payload={"allocation": str(amount), "by": by},
    )
    return {"firm": firm_key, "allocation": str(amount), "by": by}


def watch(eco, directory, submitted_by: str = "", move_to: Optional[Path] = None) -> list:
    """Try every strategy file in a directory. The drop box.

    Files that fail are left where they are with their reason, so a directory
    of bots can be re-run after fixing one without re-recruiting the rest.
    """
    directory = Path(str(directory))
    if not directory.exists():
        raise RecruitError(f"{directory} does not exist")
    out = []
    for path in sorted(directory.iterdir()):
        if path.is_dir() or path.suffix.lower() not in (".py", ".yaml", ".yml", ".json"):
            continue
        try:
            result = recruit(eco, path, submitted_by=submitted_by)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            result = Recruitment(firm_key=path.name)
            result.reason = f"could not be read: {exc}"
        out.append(result)
        if result.accepted and move_to is not None:
            move_to.mkdir(parents=True, exist_ok=True)
            path.rename(move_to / path.name)
    return out


def pending_recruits(eco) -> list:
    """Recruited firms still waiting to be funded."""
    out = []
    for approval in eco.gate.pending():
        details = approval.details or {}
        if details.get("kind") == "recruit":
            out.append({
                "firm": details.get("firm"),
                "file": details.get("file"),
                "requested": details.get("new_allocation"),
                "approval": approval.id,
            })
    return out


def _dumps(value) -> str:  # pragma: no cover - trivial
    return json.dumps(value, default=str)


__all__ = [
    "RecruitError",
    "Recruitment",
    "firm_key_for",
    "fund",
    "pending_recruits",
    "recruit",
    "stake_for",
    "watch",
]
