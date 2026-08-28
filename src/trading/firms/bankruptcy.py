"""Bankruptcy — what happens to a firm after it dies.

A kill used to be the end of the story, and it left the story unfinished. The
firm stopped proposing, kept every position it held, and handed back only the
cash that happened to be uninvested at the moment it died. Six firms sat that
way for up to thirteen days holding $155,859 of live market exposure that
nothing in the system could sell. Their lessons went with them: `postmortem.py`
could take a dead firm apart in detail, but nothing ever called it, so the only
record of why a firm failed was the one line in `kill_reason`.

So death has a process now, in three parts, and the order matters:

1. **Liquidate.** The estate proposes exits every tick until it is flat. That
   happens in `firm.py`, not here, because it goes through the same risk
   manager, the same conscience and the same venue as any other order — an exit
   nobody reviewed is not safer for being an exit.
2. **Account for it.** When the book is empty, the remaining cash goes back and
   the allocation is closed out. This is the step that turns a dead firm's
   holdings back into capital the village can use.
3. **Learn from it.** The postmortem is written, stored against the firm, and
   published as a lesson the living firms can read.

**Nothing here is allowed to start a position.** Winding up sells, records and
returns money. The successor it files is born paused with an allocation of zero,
the same line `recruit.py` holds for a strategy dropped in from outside — a
system that replaces its own failures with funded, trading firms is a system
that can lose money on its own initiative.

**What actually stops the heir is the empty allocation, not the pause.** Under
`TRADE_AUTONOMY=council` the council holds `resume_firm`, and it resumes these
heirs immediately — measured, not assumed: all six came back `active` on the
tick that filed them. They still cannot trade, because the risk manager refuses
any order from a firm with no allocation, and `allocate_capital` is a separate
decision. So the heir is inert for as long as it is broke, and this module does
not and cannot make it inert in any stronger sense: the council's authority over
capital is the operator's standing choice, and bankruptcy does not get a private
exemption from it. If an heir should never be fundable without a human, that
belongs in the autonomy config, where every other such rule lives.

`wind_up` is idempotent. It refuses a firm that still holds anything, and it
refuses one already wound up, because the tick calls it on every dead firm on
every pass and it must be safe to call a hundred times.
"""

from __future__ import annotations

import json
from typing import Optional

from ...money import D, ZERO, fmt_money, money
from ..models import FirmRecord, FirmStatus


def _lesson(pm, firm: FirmRecord) -> str:
    """One paragraph a living firm could act on. The postmortem's own words.

    `postmortem._diagnose` already ranks what went wrong in the order worth
    acting on, so this quotes it rather than inventing a second opinion that
    could disagree with the table printed by `trade postmortem`.
    """
    head = (
        f"{firm.firm_key} went bankrupt after {pm.closed} closed trade(s): "
        f"{firm.kill_reason or 'no reason recorded'}."
    )
    if pm.notes:
        return head + " " + " ".join(str(n) for n in pm.notes[:3])
    return head + (
        f" Realised {fmt_money(pm.realized)} against {fmt_money(pm.costs)} "
        "of fees and slippage."
    )


def _successor_key(firm_key: str, existing: set) -> str:
    """`firm_b_stocks` → `firm_b_stocks_ii`, then `_iii`. Never reuses a key."""
    for suffix in ("_ii", "_iii", "_iv", "_v", "_vi"):
        candidate = f"{firm_key}{suffix}"
        if candidate not in existing:
            return candidate
    return ""


def file_successor(store, firm: FirmRecord, pm, lesson: str) -> Optional[FirmRecord]:
    """Register an unfunded heir carrying what killed its predecessor.

    **Paused, and broke.** Allocation zero, status paused, so it cannot trade.
    Funding it is a separate human decision, exactly as for a recruit. What it
    inherits is the genome, the universe and the postmortem — so whoever looks
    at it later is reading the reason the last attempt failed, attached to the
    thing that would try again.

    Returns None when an heir already exists, so the tick cannot breed a queue
    of identical paused firms by calling this more than once.
    """
    existing = {f.firm_key for f in store.firms()}
    key = _successor_key(firm.firm_key, existing)
    if not key:
        return None

    genome = dict(firm.genome or {})
    genome["inherited_from"] = firm.firm_key
    genome["inherited_lesson"] = lesson
    genome["predecessor_realized"] = str(pm.realized)
    genome["predecessor_costs"] = str(pm.costs)

    heir = FirmRecord(
        firm_key=key,
        name=f"{firm.name or firm.firm_key} II",
        asset_class=firm.asset_class,
        strategy=firm.strategy,
        venue=firm.venue,
        status=FirmStatus.PAUSED.value,
        risk_limit=D(firm.risk_limit),
        initial_allocation=ZERO,
        allocation=ZERO,
        cash=ZERO,
        genome=genome,
        universe=list(firm.universe or []),
    )
    return store.upsert_firm(heir)


def wind_up(eco, firm: FirmRecord, card=None) -> Optional[dict]:
    """Close a dead, flat firm's estate. Returns None when it is not ready.

    Not ready means: not dead, already wound up, or still holding something.
    All three are normal on most ticks — this is called on every dead firm
    every pass — so they are quiet returns, not errors.
    """
    if not firm.is_killed or firm.is_bankrupt:
        return None
    if any(p.is_open for p in eco.store.positions(firm.id)):
        return None             # still has a book; keep liquidating

    from .. import postmortem

    pm = postmortem.examine(eco.store, firm, card)
    lesson = _lesson(pm, firm)

    # Hand the money back before anything else. If a later step raises, the
    # capital is already home rather than stranded behind a failed write —
    # which is the failure mode this whole module exists to correct.
    returned = money(firm.cash)
    if returned != 0:
        eco.brokerage.allocator.release(firm, f"bankruptcy: {firm.firm_key} wound up")

    heir = file_successor(eco.store, firm, pm, lesson)

    # The lesson goes where the living firms already look. `firm_id` stays on
    # the dead firm: it is that firm's lesson, and attributing it to the heir
    # would put a loss on a book that never traded.
    eco.memory.remember(
        lesson,
        firm_id=firm.id,
        memory_type="bankruptcy",
        outcome="loss" if pm.realized < 0 else "mixed",
        reward=pm.realized,
        payload={
            "kill_reason": firm.kill_reason or "",
            "closed_trades": pm.closed,
            "wins": pm.wins,
            "losses": pm.losses,
            "realized": str(pm.realized),
            "fees": str(pm.fees),
            "slippage": str(pm.slippage),
            "costs": str(pm.costs),
            "capital_returned": str(returned),
            "diagnosis": [str(n) for n in pm.notes],
            "worst_symbols": [
                {"symbol": s.symbol, "net": str(s.net), "closed": s.closed}
                for s in pm.symbols[:3]
            ],
            "successor": heir.firm_key if heir else "",
        },
    )

    eco.store.set_firm_status(
        firm.id, FirmStatus.BANKRUPT.value,
        firm.kill_reason or "wound up",
    )
    eco.store.record_event(
        "bankruptcy",
        f"{firm.firm_key} wound up: returned {fmt_money(returned)}, "
        f"{pm.closed} closed trade(s), realised {fmt_money(pm.realized)}"
        + (f", successor {heir.firm_key} filed (paused, unfunded)" if heir else ""),
        firm_id=firm.id,
        payload={"returned": str(returned), "lesson": lesson,
                 "successor": heir.firm_key if heir else ""},
    )
    return {
        "firm": firm.firm_key,
        "returned": str(returned),
        "lesson": lesson,
        "successor": heir.firm_key if heir else "",
        "diagnosis": [str(n) for n in pm.notes],
    }


__all__ = ["wind_up", "file_successor"]
