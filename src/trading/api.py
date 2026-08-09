"""A read-only JSON view of the village.

Mission Control renders HTML because a dashboard that needs a gateway and an
npm install to show a table is three more things that can be down when you want
to know whether a firm is bleeding. This module exists for the other case: some
*other* program wants the same numbers — a dashboard someone else wrote, a
notebook, a script, a status board on a spare monitor.

**Read-only by construction, not by convention.** There is no POST here and no
route that takes a decision. The router is built from `GET` handlers that call
the same store and evaluator Mission Control calls and then serialise. If you
want to move capital you go through the approval gate, exactly as the CLI and
the web page do.

**Every number is stringified.** Money and prices are `Decimal` throughout this
codebase precisely so they do not go through a float; serialising them as JSON
numbers would undo that at the last step, because JSON has no decimal type and
every parser on the other end will hand you a float. So `equity` is `"49448.85"`
and not `49448.85`. Consumers that want arithmetic should parse it as a decimal;
consumers that want to print it can print it.

**It reports, it does not compute.** `sufficient_data` rides along on every
scorecard, so a consumer can tell "score 42 on four trades" from "score 42 on
forty". Anything that drops that flag and plots the number is drawing a
conclusion the ledger refused to draw.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..money import D, ZERO, money
from .web import ecosystem

router = APIRouter()


def _firm(firm, card, purse, open_positions: int = 0) -> dict:
    return {
        "key": firm.firm_key,
        "name": firm.name,
        "status": firm.status,
        "asset_class": firm.asset_class,
        "venue": firm.venue,
        "universe": list(firm.universe or ()),
        "allocation": str(money(D(firm.allocation))),
        "initial_allocation": str(money(D(firm.initial_allocation))),
        "cash": str(purse.withdrawable) if purse else None,
        "equity": str(money(D(card.equity))) if card else None,
        "return_pct": str(card.return_pct) if card else None,
        "drawdown_pct": str(card.drawdown_pct) if card else None,
        "win_rate_pct": str(card.win_rate_pct) if card else None,
        "score": str(card.score) if card else None,
        "closed_trades": card.closed_trades if card else 0,
        # The flag that stops a score being read as a verdict.
        "sufficient_data": bool(card.sufficient_data) if card else False,
        "open_positions": open_positions,
    }


@router.get("/api/firms")
def firms() -> JSONResponse:
    """Every firm, as the brokerage currently scores it."""
    eco = ecosystem()
    try:
        market = eco.market()
        records = eco.store.firms()
        cards = {c.firm_id: c for c in eco.brokerage.evaluator.evaluate_all(records, market)}
        out = []
        for record in records:
            purse = eco.store.cash_view(
                record, cash_floor_pct=eco.config.firm.cash_floor_pct
            )
            out.append(_firm(
                record,
                cards.get(record.id),
                purse,
                len(eco.store.positions(record.id, open_only=True)),
            ))
        payload = {"as_of": str(market.as_of()), "source": eco.feed.name, "firms": out}
    finally:
        eco.db.close()
    return JSONResponse(payload)


@router.get("/api/status")
def status() -> JSONResponse:
    """The one-screen health check, as JSON.

    Named to match what most dashboards look for, and what this codebase's own
    `trade status` prints. `reconciled` is the field that matters: while it is
    false the brokerage refuses to score, kill or allocate, so every other
    number on this page is the last good one rather than the current one.
    """
    eco = ecosystem()
    try:
        market = eco.market()
        records = eco.store.firms()
        cards = eco.brokerage.evaluator.evaluate_all(records, market)
        reconciliation = eco.brokerage.reconcile(market)
        equity = money(sum((D(c.equity) for c in cards), ZERO))
        capital = money(sum((D(f.allocation) for f in records if not f.is_killed), ZERO))
        payload = {
            "as_of": str(market.as_of()),
            "source": eco.feed.name,
            "firms": len(records),
            "active": sum(1 for f in records if f.is_active),
            "paused": sum(1 for f in records if f.status == "paused"),
            "killed": sum(1 for f in records if f.is_killed),
            "equity": str(equity),
            "capital_deployed": str(capital),
            "reconciled": reconciliation.ok,
            "reconciliation": reconciliation.summary(),
            "pending_approvals": len(eco.gate.pending()),
            "autonomy": eco.config.autonomy.mode,
            "council_sitting": eco.config.autonomy.council_decides,
            # Kept for dashboards that probe a health endpoint before drawing.
            "ok": reconciliation.ok,
        }
    finally:
        eco.db.close()
    return JSONResponse(payload)


@router.get("/api/council")
def council(limit: int = 20) -> JSONResponse:
    """Recent rulings. `defer` means it came back to a human."""
    eco = ecosystem()
    try:
        rows = eco.council.recent(max(1, min(200, limit)))
        payload = {
            "sitting": eco.config.autonomy.council_decides,
            "rulings": [
                {
                    "id": r.get("id"),
                    "action": r.get("action"),
                    "firm": r.get("firm_key"),
                    "verdict": r.get("verdict"),
                    "reason": r.get("reason"),
                    "for": str(D(r.get("for_weight") or 0)),
                    "against": str(D(r.get("against_weight") or 0)),
                    "at": r.get("created_at"),
                }
                for r in rows
            ],
        }
    finally:
        eco.db.close()
    return JSONResponse(payload)


@router.get("/api/tokens")
def tokens() -> JSONResponse:
    """The token standings. Points, never capital."""
    eco = ecosystem()
    try:
        payload = {
            "note": "tokens are points; nothing converts them into capital",
            "standings": [
                {
                    "rank": row.get("rank"),
                    "firm": row.get("firm"),
                    "tokens": int(D(row.get("tokens") or 0)),
                    "title": row.get("title") or "",
                    "reputation": int(D(row.get("reputation") or 0)),
                }
                for row in eco.arena.standings()
            ],
        }
    finally:
        eco.db.close()
    return JSONResponse(payload)


__all__ = ["router"]
