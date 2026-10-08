"""The operator's desk — a paper firm that trades when the operator says so.

See migration 034. In the chat (`chat.py`) the operator can tell the village
to trade: "buy $1,000 of NVDA", "sell all my TSLA". The village writes the
order under its reply as a line the model is told to use,

    ORDER: {"side": "buy", "symbol": "NVDA", "dollars": 1000}

which `parse` takes out of the text and `propose` keeps. **Nothing trades
until the operator taps Confirm** under the reply (decided by the operator on
2026-10-08, so a question like "should I buy NVDA?" can never become a trade).

A confirmed order goes to `firm_operator_desk`, a paper firm like any other
whose strategy is `bots/operator_desk.py`. On the next tick that bot hands the
order over as an ordinary proposal, and it meets everything every firm's order
meets: the risk manager (position and cash limits, which may shrink it), the
conscience, the daily loss halt, the paper venue, the ledger. What happened is
copied back here and shown under the reply. A stock order waits for the market
to open; an order nobody confirms is dropped after `PROPOSED_FOR`.

The desk is the operator's own: none of the village's firms trade on what is
said in the chat, and the desk's genome is locked, so evolution leaves it alone.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..db.connection import to_datetime, utcnow, utcnow_iso

DESK = "firm_operator_desk"
#: The desk's universe always includes these; the symbols it holds or has
#: orders for are added each tick (`sync_universe`), because a firm can only
#: trade, and only be priced, inside its universe.
BASE_UNIVERSE = ("SPY",)

#: An order nobody confirms is dropped after this; a confirmed one that never
#: found an open market after `CONFIRMED_FOR`.
PROPOSED_FOR = timedelta(hours=12)
CONFIRMED_FOR = timedelta(days=4)
#: An order handed to a tick that left no proposal behind in this long (the
#: feed had no price, say) goes back to waiting.
SENT_FOR = timedelta(minutes=30)
#: Biggest single order the chat may write, in dollars. The risk manager
#: shrinks it further to the desk's own limits.
MAX_DOLLARS = Decimal("1000000")

_ORDER_LINE = re.compile(r"^\s*ORDER:\s*(\{.*\})\s*$", re.M)
_ANY_ORDER_LINE = re.compile(r"^\s*ORDER:.*$", re.M)
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}(-USD)?$")

OPEN = ("proposed", "confirmed", "sent")


# =========================================================================
# from the chat
# =========================================================================
def parse(text: str) -> tuple:
    """(the reply without its ORDER lines, the orders it wrote). Bad lines are dropped."""
    orders = []
    for raw in _ORDER_LINE.findall(text or ""):
        try:
            order = _clean(json.loads(raw))
        except (ValueError, TypeError):
            continue
        if order:
            orders.append(order)
    clean = _ANY_ORDER_LINE.sub("", text or "").strip()
    return re.sub(r"\n{3,}", "\n\n", clean), orders


def _clean(d: dict) -> Optional[dict]:
    if not isinstance(d, dict):
        return None
    side = str(d.get("side") or "").lower().strip()
    if side not in ("buy", "sell"):
        return None
    symbol = str(d.get("symbol") or "").upper().strip().lstrip("$")
    if symbol in ("BTC", "ETH", "SOL", "DOGE"):
        symbol += "-USD"
    if not _SYMBOL.match(symbol):
        return None
    out = {"side": side, "symbol": symbol, "dollars": None, "quantity": None,
           "sell_all": False, "note": str(d.get("note") or "")[:200]}
    if side == "sell" and d.get("all"):
        out["sell_all"] = True
        return out
    for key in ("dollars", "quantity"):
        if d.get(key) is None:
            continue
        try:
            value = Decimal(str(d[key]))
        except (InvalidOperation, ValueError):
            return None
        if value <= 0 or (key == "dollars" and value > MAX_DOLLARS):
            return None
        out[key] = value
    if out["dollars"] is None and out["quantity"] is None:
        return None
    return out


def propose(db, message_id: int, orders: list) -> list:
    """Keep the orders a reply wrote, waiting for the operator's tap."""
    ids = []
    for o in orders:
        ids.append(db.insert("operator_orders", {
            "message_id": message_id, "side": o["side"], "symbol": o["symbol"],
            "dollars": o.get("dollars"), "quantity": o.get("quantity"),
            "sell_all": 1 if o.get("sell_all") else 0, "note": o.get("note") or "",
            "status": "proposed", "created_at": utcnow_iso()}))
    return ids


def confirm(db, order_id: int) -> str:
    row = db.query_one("SELECT status FROM operator_orders WHERE id = ?", (order_id,))
    if not row:
        return "no such order"
    if row["status"] != "proposed":
        return f"order #{order_id} is already {row['status']}"
    db.update("operator_orders", order_id, {"status": "confirmed", "confirmed_at": utcnow_iso(),
                                            "result": "waiting for the next tick"})
    return f"order #{order_id} confirmed: it goes to your desk on the next tick"


def cancel(db, order_id: int) -> str:
    row = db.query_one("SELECT status FROM operator_orders WHERE id = ?", (order_id,))
    if not row:
        return "no such order"
    if row["status"] not in ("proposed", "confirmed"):
        return f"order #{order_id} is already {row['status']}"
    db.update("operator_orders", order_id, {"status": "cancelled", "done_at": utcnow_iso(),
                                            "result": "cancelled by you"})
    return f"order #{order_id} cancelled"


def orders(db, limit: int = 60) -> list:
    try:
        return db.query("SELECT * FROM operator_orders ORDER BY id DESC LIMIT ?", (limit,))
    except Exception:  # noqa: BLE001 - an unmigrated ledger has no orders
        return []


def describe(o: dict) -> str:
    side = str(o["side"])
    if int(o.get("sell_all") or 0):
        return f"sell all of your {o['symbol']}"
    if o.get("dollars") is not None:
        return f"{side} ${Decimal(str(o['dollars'])):,.2f} of {o['symbol']}"
    return f"{side} {Decimal(str(o['quantity'])).normalize()} {o['symbol']}"


# =========================================================================
# into the tick
# =========================================================================
def _desk(db) -> Optional[dict]:
    try:
        return db.query_one("SELECT * FROM firms WHERE firm_key = ?", (DESK,))
    except Exception:  # noqa: BLE001
        return None


def sync_universe(db) -> None:
    """The desk's universe: the base, what it holds, and what it has orders for."""
    desk = _desk(db)
    if not desk:
        return
    held = {str(r["symbol"]).upper() for r in db.query(
        "SELECT symbol FROM positions WHERE firm_id = ? AND quantity <> 0", (desk["id"],))}
    wanted = {str(r["symbol"]).upper() for r in db.query(
        "SELECT symbol FROM operator_orders WHERE status IN ('confirmed', 'sent')")}
    try:
        current = [str(s).upper() for s in json.loads(desk.get("universe") or "[]")]
    except (TypeError, ValueError):
        current = []
    universe = list(dict.fromkeys(list(BASE_UNIVERSE) + sorted(held | wanted)))
    if universe != current:
        db.update("firms", desk["id"], {"universe": json.dumps(universe)})


def expire(db, now=None) -> None:
    now = to_datetime(now) or utcnow()
    for o in db.query("SELECT id, status, created_at, confirmed_at FROM operator_orders "
                      "WHERE status IN ('proposed', 'confirmed')"):
        if o["status"] == "proposed":
            at, limit, why = o.get("created_at"), PROPOSED_FOR, "not confirmed in time"
        else:
            at, limit, why = o.get("confirmed_at"), CONFIRMED_FOR, "no open market to fill it"
        when = to_datetime(at)
        if when is not None and now - when > limit:
            db.update("operator_orders", o["id"], {"status": "expired", "done_at": utcnow_iso(),
                                                   "result": why})


def reconcile(db, now=None) -> None:
    """Copy back what the tick did with each order the desk sent."""
    now = to_datetime(now) or utcnow()
    desk = _desk(db)
    if not desk:
        return
    for o in db.query("SELECT * FROM operator_orders WHERE status = 'sent'"):
        tag = f"operator order #{o['id']}:"
        p = db.query_one(
            "SELECT * FROM trade_proposals WHERE firm_id = ? AND rationale LIKE ? "
            "ORDER BY id DESC LIMIT 1", (desk["id"], tag + "%"))
        if p is None:
            sent = to_datetime(o.get("sent_at"))
            if sent is not None and now - sent > SENT_FOR:
                db.update("operator_orders", o["id"], {
                    "status": "confirmed", "result": "not traded yet, trying again"})
            continue
        status = str(p.get("status") or "")
        if status == "filled":
            fill = db.query_one("SELECT quantity, price FROM fills WHERE proposal_id = ? "
                                "ORDER BY id DESC LIMIT 1", (p["id"],)) or {}
            qty, price = fill.get("quantity"), fill.get("price")
            done = (f"filled: {o['side']} {Decimal(str(qty)).normalize()} {o['symbol']} "
                    f"at ${Decimal(str(price)):,.2f}" if qty is not None and price is not None
                    else "filled")
            if p.get("risk_verdict") == "resize":
                done += f" (made smaller: {str(p.get('risk_reason') or '')[:120]})"
            db.update("operator_orders", o["id"], {"status": "filled", "result": done,
                                                   "done_at": utcnow_iso()})
        elif status in ("rejected", "blocked"):
            why = p.get("risk_reason") if p.get("risk_verdict") == "block" else ""
            why = why or (p.get("ethics_reason") if p.get("ethics_verdict") == "block" else "")
            db.update("operator_orders", o["id"], {
                "status": "blocked", "done_at": utcnow_iso(),
                "result": f"not traded: {str(why or 'refused by the venue')[:200]}"})


def take(db, context) -> list:
    """Confirmed orders the desk can place this bar, as bot orders. Marks them sent.

    Called by `bots/operator_desk.py` with the tick's `adapter.Context`.
    """
    from .session import is_open

    reconcile(db)
    expire(db)
    out = []
    as_of = to_datetime(getattr(context, "as_of", None)) or utcnow()
    for o in db.query("SELECT * FROM operator_orders WHERE status = 'confirmed' ORDER BY id"):
        symbol = str(o["symbol"]).upper()
        note = None
        if symbol not in context.universe:
            note = "added to the desk; trades on the next tick"
        elif context.price(symbol) is None:
            note = "no price for it yet"
        elif not is_open(as_of, symbol):
            note = "waiting for the market to open"
        if note:
            if o.get("result") != note:
                db.update("operator_orders", o["id"], {"result": note})
            continue
        order = {"symbol": symbol, "side": o["side"],
                 "rationale": f"operator order #{o['id']}: {describe(o)}"
                              + (f" ({o['note']})" if o.get("note") else "")}
        if int(o.get("sell_all") or 0):
            held = context.quantity(symbol)
            if held <= 0:
                db.update("operator_orders", o["id"], {
                    "status": "blocked", "done_at": utcnow_iso(),
                    "result": f"not traded: your desk holds no {symbol}"})
                continue
            order["quantity"] = held
        elif o.get("quantity") is not None:
            order["quantity"] = Decimal(str(o["quantity"]))
        else:
            order["notional"] = Decimal(str(o["dollars"]))
        db.update("operator_orders", o["id"], {"status": "sent", "sent_at": utcnow_iso(),
                                               "result": "sent to the desk"})
        out.append(order)
    return out


def summary(db) -> str:
    """The desk and its orders, for the chat's snapshot."""
    desk = _desk(db)
    lines = []
    if desk:
        lines.append(f"\nTHE OPERATOR'S OWN PAPER DESK ({DESK}): cash {desk.get('cash')}, "
                     f"allocation {desk.get('allocation')}, status {desk.get('status')}.")
    recent = orders(db, 10)
    if recent:
        lines.append("Orders given in this chat (newest first):")
        for o in recent:
            lines.append(f"- #{o['id']} {describe(o)}: {o['status']}"
                         + (f" ({o['result']})" if o.get("result") else ""))
    return "\n".join(lines)


__all__ = ["DESK", "cancel", "confirm", "describe", "expire", "orders", "parse",
           "propose", "reconcile", "summary", "sync_universe", "take"]
