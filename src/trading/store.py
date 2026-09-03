"""Persistence for the trading ecosystem — the one place SQL is written.

Modelled on ``src/agents/experiment_ledger.py``: agents call methods, never
the database. Two rules carried over verbatim:

* Money is never summed in SQL. SQLite's NUMERIC affinity would hand back
  floats and the totals would stop reconciling. Everything aggregates in
  Python over ``Decimal`` (``sum_decimal``).
* A firm's cash and its position move inside one transaction or not at all.
  A fill that debits cash without moving the book is exactly the corruption
  the reconciler exists to catch, so it must not be possible to write one.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from ..db.connection import Database, sum_decimal, utcnow_iso
from ..money import D, ZERO, money
from .models import (
    CashView,
    Fill,
    FirmRecord,
    FirmStatus,
    Position,
    ProposalStatus,
    TradeProposal,
)


class TradingLedgerError(RuntimeError):
    """Raised when a write would leave the books inconsistent."""


class TradingStore:
    def __init__(self, db: Database):
        self.db = db

    # -- firms ------------------------------------------------------------
    def upsert_firm(self, firm: FirmRecord) -> FirmRecord:
        """Create a firm, or update the mutable half of an existing one.

        Capital is deliberately *not* overwritten on update: re-running
        `trade init` after a firm has traded must not silently hand it a fresh
        allocation. Capital only moves through the allocator.
        """
        existing = self.get_firm(firm.firm_key)
        row = firm.to_row()
        if existing is None:
            row.setdefault("created_at", utcnow_iso())
            new_id = self.db.insert("firms", row)
            return self.require_firm_by_id(new_id)

        for protected in ("allocation", "cash", "high_water_mark", "initial_allocation",
                          "status", "consecutive_losses", "kill_reason", "killed_at"):
            row.pop(protected, None)
        self.db.update("firms", existing.id, row)
        return self.require_firm_by_id(existing.id)

    def get_firm(self, firm_key: str) -> Optional[FirmRecord]:
        row = self.db.query_one("SELECT * FROM firms WHERE firm_key = ?", (firm_key,))
        return FirmRecord.from_row(row) if row else None

    def get_firm_by_id(self, firm_id: int) -> Optional[FirmRecord]:
        row = self.db.query_one("SELECT * FROM firms WHERE id = ?", (firm_id,))
        return FirmRecord.from_row(row) if row else None

    def require_firm_by_id(self, firm_id: int) -> FirmRecord:
        firm = self.get_firm_by_id(firm_id)
        if firm is None:
            raise TradingLedgerError(f"firm {firm_id} not found")
        return firm

    def firms(self, status: Optional[str] = None) -> list[FirmRecord]:
        if status:
            rows = self.db.query("SELECT * FROM firms WHERE status = ? ORDER BY id", (status,))
        else:
            rows = self.db.query("SELECT * FROM firms ORDER BY id")
        return [FirmRecord.from_row(r) for r in rows]

    def active_firms(self) -> list[FirmRecord]:
        return self.firms(FirmStatus.ACTIVE.value)

    def bars_observed(self) -> int:
        """How many distinct market bars the village has actually seen.

        A real bar count, for the things whose cadence is quoted in bars. The
        alternative on hand was a calendar day number, and using it is how
        evolution came to run every twenty *days* while its own docstring
        promised every twenty *bars* — on an hourly feed a factor of about
        six and a half, and the reason no firm had ever evolved once in the
        village's first eighteen days of life.
        """
        try:
            row = self.db.query_one(
                "SELECT COUNT(DISTINCT bar_date) AS n FROM firm_feed_bars"
            )
        except Exception:  # noqa: BLE001 - never fail a tick over a counter
            return 0
        return int((row or {}).get("n") or 0)

    # -- strikes and the gulag ---------------------------------------------
    #
    # Kept in `firm_strikes` rather than on `firms` because every migration in
    # this repo re-runs on every init, and `CREATE TABLE IF NOT EXISTS` is the
    # shape that survives that on both dialects. Same reasoning as the feed
    # bar table in 021.
    def strike_state(self, firm_id: int) -> dict:
        """Strikes, sentence and breach flag. Zeroes for a firm with none."""
        row = self.db.query_one(
            "SELECT * FROM firm_strikes WHERE firm_id = ?", (firm_id,)
        )
        if row is None:
            return {"firm_id": firm_id, "strikes": 0, "gulag_bars_left": 0,
                    "gulag_last_bar": None, "breach_open": 0,
                    "last_strike_reason": None}
        return {
            "firm_id": firm_id,
            "strikes": int(row.get("strikes") or 0),
            "gulag_bars_left": int(row.get("gulag_bars_left") or 0),
            "gulag_last_bar": row.get("gulag_last_bar"),
            "breach_open": int(row.get("breach_open") or 0),
            "last_strike_reason": row.get("last_strike_reason"),
        }

    def _save_strike_state(self, state: dict) -> None:
        firm_id = state["firm_id"]
        exists = self.db.query_one(
            "SELECT firm_id FROM firm_strikes WHERE firm_id = ?", (firm_id,)
        )
        row = {
            "strikes": int(state.get("strikes") or 0),
            "gulag_bars_left": int(state.get("gulag_bars_left") or 0),
            "gulag_last_bar": state.get("gulag_last_bar"),
            "breach_open": int(state.get("breach_open") or 0),
            "last_strike_reason": state.get("last_strike_reason"),
            "updated_at": utcnow_iso(),
        }
        if exists is None:
            self.db.insert("firm_strikes", {"firm_id": firm_id, **row})
        else:
            self.db.execute(
                "UPDATE firm_strikes SET strikes = ?, gulag_bars_left = ?, "
                "gulag_last_bar = ?, breach_open = ?, last_strike_reason = ?, "
                "updated_at = ? WHERE firm_id = ?",
                (row["strikes"], row["gulag_bars_left"], row["gulag_last_bar"],
                 row["breach_open"], row["last_strike_reason"],
                 row["updated_at"], firm_id),
            )

    def set_strikes(self, firm_id: int, strikes: int, breach_open: int = 1,
                    reason: str = "") -> None:
        state = self.strike_state(firm_id)
        state.update(strikes=strikes, breach_open=breach_open,
                     last_strike_reason=reason)
        self._save_strike_state(state)

    def set_breach_open(self, firm_id: int, value: int) -> None:
        state = self.strike_state(firm_id)
        state["breach_open"] = int(value)
        self._save_strike_state(state)

    def set_gulag(self, firm_id: int, bars_left: int, last_bar) -> None:
        """Bars still owed, and the last bar counted against the sentence.

        `last_bar` is what stops a per-tick loop serving a twenty-bar sentence
        in twenty minutes; None leaves it untouched, for the moment a sentence
        is handed down and no bar has yet been counted against it.
        """
        state = self.strike_state(firm_id)
        state["gulag_bars_left"] = int(bars_left)
        if last_bar is not None:
            state["gulag_last_bar"] = last_bar
        self._save_strike_state(state)

    def release_from_gulag(self, firm_id: int) -> None:
        state = self.strike_state(firm_id)
        state["gulag_bars_left"] = 0
        state["gulag_last_bar"] = None
        self._save_strike_state(state)

    def set_firm_status(self, firm_id: int, status: str, reason: str = "") -> None:
        values = {"status": status, "updated_at": utcnow_iso()}
        if status == FirmStatus.KILLED.value:
            values["kill_reason"] = reason
            values["killed_at"] = utcnow_iso()
        self.db.update("firms", firm_id, values)

    def set_allocation(self, firm_id: int, allocation: Decimal, cash_delta: Decimal) -> None:
        """Move a firm's allocation and its uninvested cash by the same step.

        The brokerage can only take back money the firm is not currently
        holding in positions. Withdrawing more than the uninvested cash would
        put the firm's cash below zero — a state that then blocks the *sells*
        that would have freed the money, which is the exact opposite of what a
        capital cut is for. So it is refused here rather than clamped
        silently: the allocator is expected to size the cut to the cash.
        """
        firm = self.require_firm_by_id(firm_id)
        new_cash = money(firm.cash + cash_delta)
        if new_cash < 0:
            raise TradingLedgerError(
                f"{firm.firm_key}: cannot withdraw {money(-cash_delta)} — only "
                f"{money(firm.cash)} is uninvested"
            )
        self.db.update(
            "firms",
            firm_id,
            {
                "allocation": money(allocation),
                "cash": new_cash,
                # Capital moving in or out changes equity by exactly the same
                # amount, so the high-water mark moves with it. Without this,
                # taking $90k off a firm reads as a 90% drawdown, the score
                # collapses, and the cut triggers further cuts — a death
                # spiral driven by bookkeeping rather than by performance.
                "high_water_mark": money(max(ZERO, D(firm.high_water_mark) + D(cash_delta))),
                "updated_at": utcnow_iso(),
            },
        )

    def update_firm_fields(self, firm_id: int, **values) -> None:
        if not values:
            return
        values["updated_at"] = utcnow_iso()
        self.db.update("firms", firm_id, values)

    # -- positions --------------------------------------------------------
    def positions(self, firm_id: int, open_only: bool = True) -> list[Position]:
        rows = self.db.query(
            "SELECT * FROM positions WHERE firm_id = ? ORDER BY symbol", (firm_id,)
        )
        out = [Position.from_row(r) for r in rows]
        return [p for p in out if p.is_open] if open_only else out

    def cash_view(self, firm: FirmRecord, market=None, cash_floor_pct=None) -> "CashView":
        """Split a firm's capital into available / withdrawable / in-positions.

        The one place those buckets are computed. The risk manager sizes
        against ``available`` and the allocator withdraws against
        ``withdrawable``; if they ever disagreed about what the floor means,
        one of them would be writing cheques the other refuses to cash.
        """
        from .config import FirmDefaults

        floor_pct = (
            D(cash_floor_pct) if cash_floor_pct is not None else FirmDefaults().cash_floor_pct
        )
        in_positions = ZERO
        if market is not None and firm.id is not None:
            in_positions = sum(
                (p.market_value(market.mark(p.symbol)) for p in self.positions(firm.id)), ZERO
            )
        return CashView(
            cash=money(firm.cash),
            reserve=money(floor_pct * D(firm.allocation)),
            in_positions=money(in_positions),
        )

    def get_position(self, firm_id: int, symbol: str) -> Optional[Position]:
        row = self.db.query_one(
            "SELECT * FROM positions WHERE firm_id = ? AND symbol = ?", (firm_id, symbol)
        )
        return Position.from_row(row) if row else None

    def _save_position(self, position: Position) -> Position:
        row = position.to_row()
        if position.id is None:
            position.id = self.db.insert("positions", row)
        else:
            self.db.update("positions", position.id, row)
        return position

    # -- proposals and fills ----------------------------------------------
    def record_proposal(self, proposal: TradeProposal) -> TradeProposal:
        proposal.id = self.db.insert("trade_proposals", proposal.to_row())
        return proposal

    def set_proposal_status(self, proposal_id: int, status: str) -> None:
        self.db.update("trade_proposals", proposal_id, {"status": status})

    def proposals(self, firm_id: Optional[int] = None, limit: int = 50) -> list[TradeProposal]:
        if firm_id is None:
            rows = self.db.query(
                "SELECT * FROM trade_proposals ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            rows = self.db.query(
                "SELECT * FROM trade_proposals WHERE firm_id = ? ORDER BY id DESC LIMIT ?",
                (firm_id, limit),
            )
        return [TradeProposal.from_row(r) for r in rows]

    def settle(self, firm: FirmRecord, fill: Fill) -> Fill:
        """Apply a fill: position, realised P&L, cash and the fill row, atomically.

        This is the only function in the package that moves cash. Everything
        the reconciler later checks is decided here.
        """
        if firm.id is None:
            raise TradingLedgerError("cannot settle a fill for an unsaved firm")

        position = self.get_position(firm.id, fill.symbol) or Position(
            firm_id=firm.id, symbol=fill.symbol
        )
        realized = position.apply(fill)
        fill.realized_pnl = realized
        # Buying spends cash, selling returns it; fees always cost.
        #
        # `fill.gross` rather than quantity * price, because an option is
        # quoted per share and traded per contract: one contract at $3.20
        # moves $320 of cash, and computing it here a second way is how the
        # ledger and the position come to disagree by a factor of a hundred.
        gross = fill.gross
        fill.cash_delta = money(-gross - fill.fee if fill.side_enum.sign > 0 else gross - fill.fee)
        new_cash = money(firm.cash + fill.cash_delta)
        if new_cash < 0:
            raise TradingLedgerError(
                f"{firm.firm_key}: fill would overdraw cash "
                f"({firm.cash} + {fill.cash_delta} < 0)"
            )

        consecutive = firm.consecutive_losses
        if realized < 0:
            consecutive += 1
        elif realized > 0:
            consecutive = 0

        with self.db.transaction():
            self._save_position(position)
            fill.id = self.db.insert("fills", fill.to_row())
            self._record_provenance(firm.id, position, getattr(fill, "priced_by", ""))
            if fill.proposal_id:
                self.db.update(
                    "trade_proposals", fill.proposal_id, {"status": ProposalStatus.FILLED.value}
                )
            # **Relative, not absolute.** `new_cash` is computed from
            # `firm.cash` — an in-memory value read before this transaction
            # opened — so writing it as an absolute makes the last writer win.
            # Settle two fills against one stale record and the second silently
            # undoes the first's cash movement while both fill rows remain:
            # two debits recorded, one applied, and `cash = allocation + sum
            # (cash_delta)` breaks by exactly one fill.
            #
            # That is not hypothetical. On 2026-09-03 `firm_a_etf_ii_v` wrote
            # two identical EFA fills of -$400.28 in one pass and the ledger
            # came out $400.28 rich; `firm_d_value_iii` did the same with VZ
            # for $226.68. It is also the best explanation for two earlier
            # "torn ledgers" this session that were blamed on SIGKILL and on
            # two processes, and survived the fixes for both.
            #
            # Letting the database do the arithmetic makes the result correct
            # whatever the caller was holding.
            self.db.execute(
                "UPDATE firms SET cash = cash + ?, consecutive_losses = ?, "
                "updated_at = ? WHERE id = ?",
                (fill.cash_delta, consecutive, utcnow_iso(), firm.id),
            )
        firm.cash = new_cash
        firm.consecutive_losses = consecutive
        return fill

    # -- which feed built this book ----------------------------------------
    def _record_provenance(self, firm_id: int, position, priced_by: str) -> None:
        """Remember the feed a position was built with. See migration 019.

        Written inside `settle`'s transaction, so a position and the record of
        what priced it can never disagree. A position closed to flat forgets —
        the next one is a fresh book and inherits nothing.
        """
        if not priced_by:
            return
        try:
            if not position.is_open:
                self.db.execute(
                    "DELETE FROM position_provenance WHERE firm_id = ? AND symbol = ?",
                    (firm_id, position.symbol),
                )
                return
            existing = self.db.query_one(
                "SELECT priced_by FROM position_provenance WHERE firm_id = ? AND symbol = ?",
                (firm_id, position.symbol),
            )
            if existing is None:
                self.db.insert(
                    "position_provenance",
                    {"firm_id": firm_id, "symbol": position.symbol, "priced_by": priced_by},
                )
            elif str(existing["priced_by"]) != priced_by:
                # Adding to a position under a different feed than it was opened
                # with. The record names the feed it was *built* on, which is the
                # one whose prices the quantities reflect.
                self.db.execute(
                    "UPDATE position_provenance SET priced_by = ? "
                    "WHERE firm_id = ? AND symbol = ?",
                    (f"{existing['priced_by']}+{priced_by}", firm_id, position.symbol),
                )
        except Exception:  # noqa: BLE001 - an un-migrated database still trades
            pass

    def provenance(self, firm_id: int) -> dict:
        """symbol -> the feed that built that position."""
        try:
            rows = self.db.query(
                "SELECT symbol, priced_by FROM position_provenance WHERE firm_id = ?",
                (firm_id,),
            ) or []
        except Exception:  # noqa: BLE001
            return {}
        return {str(r["symbol"]): str(r["priced_by"]) for r in rows}

    def fills(self, firm_id: Optional[int] = None, limit: Optional[int] = None) -> list[Fill]:
        sql = "SELECT * FROM fills"
        params: list = []
        if firm_id is not None:
            sql += " WHERE firm_id = ?"
            params.append(firm_id)
        sql += " ORDER BY id"
        if limit is not None:
            sql += " DESC LIMIT ?"
            params.append(limit)
        rows = self.db.query(sql, tuple(params))
        out = [Fill.from_row(r) for r in rows]
        return list(reversed(out)) if limit is not None else out

    def realized_pnl(self, firm_id: int) -> Decimal:
        rows = self.db.query("SELECT realized_pnl FROM fills WHERE firm_id = ?", (firm_id,))
        return sum_decimal(r["realized_pnl"] for r in rows)

    def fees_paid(self, firm_id: int) -> Decimal:
        rows = self.db.query("SELECT fee FROM fills WHERE firm_id = ?", (firm_id,))
        return sum_decimal(r["fee"] for r in rows)

    def cash_delta_total(self, firm_id: int) -> Decimal:
        rows = self.db.query("SELECT cash_delta FROM fills WHERE firm_id = ?", (firm_id,))
        return sum_decimal(r["cash_delta"] for r in rows)

    # -- performance and events -------------------------------------------
    def next_generation(self, firm_id: int) -> int:
        """One past the highest generation this firm has already run.

        Read from the table rather than counted in memory, so a restarted loop
        continues the lineage instead of overwriting generation 1 forever.
        """
        try:
            row = self.db.query_one(
                "SELECT MAX(generation) AS g FROM strategy_genomes WHERE firm_id = ?",
                (firm_id,),
            )
        except Exception:  # noqa: BLE001 - an un-migrated database has no lineage
            return 1
        return int((row or {}).get("g") or 0) + 1

    def record_performance(self, firm_id: int, snapshot: dict) -> int:
        row = dict(snapshot)
        row["firm_id"] = firm_id
        row.setdefault("as_of", utcnow_iso())
        return self.db.insert("firm_performance", row)

    def performance_history(self, firm_id: int, limit: int = 30) -> list[dict]:
        return self.db.query(
            "SELECT * FROM firm_performance WHERE firm_id = ? ORDER BY id DESC LIMIT ?",
            (firm_id, limit),
        )

    def latest_performance(self, firm_id: int) -> Optional[dict]:
        rows = self.performance_history(firm_id, limit=1)
        return rows[0] if rows else None

    def record_event(
        self,
        event_type: str,
        detail: str,
        firm_id: Optional[int] = None,
        payload: Optional[dict] = None,
    ) -> int:
        return self.db.insert(
            "brokerage_events",
            {
                "firm_id": firm_id,
                "event_type": event_type,
                "detail": detail,
                "payload": json.dumps(payload or {}, default=str),
                "created_at": utcnow_iso(),
            },
        )

    def events(self, limit: int = 50, event_type: Optional[str] = None) -> list[dict]:
        if event_type:
            return self.db.query(
                "SELECT * FROM brokerage_events WHERE event_type = ? ORDER BY id DESC LIMIT ?",
                (event_type, limit),
            )
        return self.db.query("SELECT * FROM brokerage_events ORDER BY id DESC LIMIT ?", (limit,))
