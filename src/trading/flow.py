"""The flow — the tick, narrating itself as it runs.

The diagram at ``/village/flow`` is not an illustration of the architecture.
It is the tick: every dot that moves along an edge is a proposal, a fill or a
refusal that actually happened, and the node it stops at is the stage that
handled it. If nothing is running, nothing moves.

Three rules keep it honest:

**Blocked things go to the blocked node.** A proposal the risk manager or the
conscience refused does not glide on to the venue with a red tint; it leaves
the flow. An animation that shows every message reaching the end is a very
expensive way to be told nothing.

**Emission can never break a tick.** ``emit`` swallows its own errors. This
table is telemetry: the ledger does not read it, and a tick must not fail
because a diagram was watching.

**It goes through the database.** The usual arrangement is ``trade run`` in
one terminal and the dashboard in another — two processes. An in-process
queue would render an empty diagram in exactly the case the diagram exists
for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from ..db.connection import Database, utcnow_iso

# id, label, x, y, what it is
NODES = (
    ("market", "Market data", 60, 50, "prices up to the cursor, never past it"),
    ("firms", "Firms", 200, 50, "analysts, bull/bear debate, trader, risk manager"),
    ("heart", "Heart", 340, 50, "six moral foundations, before anything executes"),
    ("venue", "Venue", 480, 50, "paper by default; live venues refuse without approval"),
    ("ledger", "Ledger", 620, 50, "position, cash and P&L move together or not at all"),
    ("brain", "Brain", 760, 50, "remembers the fill and what argued for it"),
    ("blocked", "Blocked", 340, 165, "risk or ethics refused it; the proposal is still stored"),
    ("gate", "Human gate", 480, 165, "kills and capital raises wait here"),
    ("brokerage", "Brokerage", 620, 165, "reconcile, score, kill, allocate"),
    ("audit", "Audit", 760, 165, "the Obsidian vault"),
    ("halt", "KILL_ALL", 620, 275, "everything paused, waiting on a human"),
)

# from, to — every edge a dot can travel
EDGES = (
    ("market", "firms"),
    ("firms", "heart"),
    ("heart", "venue"),
    ("heart", "blocked"),
    ("venue", "ledger"),
    ("venue", "blocked"),
    ("ledger", "brain"),
    ("brain", "brokerage"),
    ("brokerage", "audit"),
    ("brokerage", "gate"),
    ("brokerage", "halt"),
)

NODE_IDS = frozenset(n[0] for n in NODES)
EDGE_IDS = frozenset(f"{a}>{b}" for a, b in EDGES)

KINDS = ("ok", "blocked", "refused", "alarm")

# How many rows to keep. This is a viewport, not a record: the audit trail is
# the vault and the event tables, and neither of them is this.
KEEP = 400


@dataclass
class FlowEvent:
    id: Optional[int]
    run_id: str
    node: str
    edge: Optional[str]
    kind: str
    label: str
    firm: str
    detail: str
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "FlowEvent":
        return cls(
            id=row.get("id"),
            run_id=row.get("run_id") or "",
            node=row.get("node") or "",
            edge=row.get("edge"),
            kind=row.get("kind") or "ok",
            label=row.get("label") or "",
            firm=row.get("firm") or "",
            detail=row.get("detail") or "",
            created_at=row.get("created_at") or "",
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run": self.run_id,
            "node": self.node,
            "edge": self.edge,
            "kind": self.kind,
            "label": self.label,
            "firm": self.firm,
            "detail": self.detail,
            "at": self.created_at,
        }


class FlowRecorder:
    """Writes what the tick is doing. Never raises, never blocks a decision."""

    def __init__(self, db: Database, enabled: bool = True):
        self.db = db
        self.enabled = enabled
        self.run_id = new_run_id()
        self._written = 0

    def start_run(self, run_id: Optional[str] = None) -> str:
        self.run_id = run_id or new_run_id()
        return self.run_id

    def emit(
        self,
        node: str,
        label: str = "",
        edge: Optional[str] = None,
        kind: str = "ok",
        firm: str = "",
        detail: str = "",
    ) -> None:
        """Record one step. Failure here is silent, by design."""
        if not self.enabled:
            return
        try:
            if node not in NODE_IDS:
                return
            if edge is not None and edge not in EDGE_IDS:
                edge = None
            self.db.insert(
                "flow_events",
                {
                    "run_id": self.run_id,
                    "node": node,
                    "edge": edge,
                    "kind": kind if kind in KINDS else "ok",
                    "label": label[:120],
                    "firm": firm[:40],
                    "detail": detail[:400],
                    "created_at": utcnow_iso(),
                },
            )
            self._written += 1
            if self._written % 50 == 0:
                self.prune()
        except Exception:  # noqa: BLE001 - telemetry must not break a tick
            return

    def move(self, frm: str, to: str, label: str = "", kind: str = "ok", firm: str = "",
             detail: str = "") -> None:
        """Something travelled from one stage to the next."""
        self.emit(to, label, edge=f"{frm}>{to}", kind=kind, firm=firm, detail=detail)

    def prune(self) -> None:
        try:
            row = self.db.query_one("SELECT MAX(id) AS top FROM flow_events")
            top = int((row or {}).get("top") or 0)
            if top > KEEP:
                self.db.execute("DELETE FROM flow_events WHERE id <= ?", (top - KEEP,))
        except Exception:  # noqa: BLE001
            return

    # -- reads -------------------------------------------------------------
    def since(self, after_id: int = 0, limit: int = 200) -> list:
        try:
            rows = self.db.query(
                "SELECT * FROM flow_events WHERE id > ? ORDER BY id LIMIT ?",
                (after_id, limit),
            )
        except Exception:  # noqa: BLE001 - an unmigrated database is not an error here
            return []
        return [FlowEvent.from_row(r) for r in rows]

    def recent(self, limit: int = 40) -> list:
        try:
            rows = self.db.query(
                "SELECT * FROM flow_events ORDER BY id DESC LIMIT ?", (limit,)
            )
        except Exception:  # noqa: BLE001
            return []
        return [FlowEvent.from_row(r) for r in reversed(rows)]

    def latest_id(self) -> int:
        try:
            row = self.db.query_one("SELECT MAX(id) AS top FROM flow_events")
            return int((row or {}).get("top") or 0)
        except Exception:  # noqa: BLE001
            return 0


class NullRecorder:
    """Used where a tick runs without telemetry — the backtester, tests."""

    run_id = ""
    enabled = False

    def start_run(self, run_id=None) -> str:
        return ""

    def emit(self, *args, **kwargs) -> None:
        return

    def move(self, *args, **kwargs) -> None:
        return

    def prune(self) -> None:
        return

    def since(self, after_id: int = 0, limit: int = 200) -> list:
        return []

    def recent(self, limit: int = 40) -> list:
        return []

    def latest_id(self) -> int:
        return 0


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def diagram() -> dict:
    """The shape of the flow, for the page to draw."""
    return {
        "nodes": [
            {"id": i, "label": lbl, "x": x, "y": y, "about": about}
            for i, lbl, x, y, about in NODES
        ],
        "edges": [{"id": f"{a}>{b}", "from": a, "to": b} for a, b in EDGES],
    }


__all__ = [
    "EDGES",
    "EDGE_IDS",
    "FlowEvent",
    "FlowRecorder",
    "KEEP",
    "NODES",
    "NODE_IDS",
    "NullRecorder",
    "diagram",
    "new_run_id",
]
