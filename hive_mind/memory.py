"""The Obsidian brain — what happened before, keyed by the regime it happened in.

A flat list of past trades is not memory, it is a log. What a scout needs to
ask is narrower: *when the market looked roughly like this, how did this go?*
So every outcome is filed under a coarse key — asset plus a VIX bucket — and
recall is a lookup on that key rather than a similarity search.

Coarse on purpose. Buckets of five VIX points mean the crash of one year and
the crash of the next land in the same drawer, which is the whole point of
having drawers. Finer keys would make every regime unique, every recall empty,
and the memory decorative.

Two properties this insists on:

* **Recall never invents.** With fewer than ``minimum_sample`` trades in a
  drawer the answer is "I do not know", not a mean of two numbers. A confident
  average over a sample of one is how a system talks itself into a position.
* **It is a record, not a controller.** Nothing here changes what the engine
  does. Recall informs a scout's confidence; the genome and the council decide.
  A memory that could silently move the book would be a second strategy nobody
  audits.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev


def regime_key(asset: str, vix: float) -> str:
    """``SPY_VIX35`` — the drawer a trade gets filed in."""
    return f"{asset.upper()}_VIX{int(round(vix / 5.0) * 5)}"


@dataclass
class Recall:
    """What memory will say about a regime. ``known`` is the honest part."""

    key: str
    known: bool = False
    trades: int = 0
    avg_return: float = 0.0
    win_rate: float = 0.0
    risk: float = 1.0  # dispersion of outcomes; 1.0 when nothing is known

    def __str__(self) -> str:
        if not self.known:
            return f"{self.key}: nothing comparable on file"
        return (
            f"{self.key}: {self.trades} trades, avg {self.avg_return:+.2f}%, "
            f"{self.win_rate:.0f}% wins, dispersion {self.risk:.2f}"
        )


class ObsidianMemory:
    def __init__(self, minimum_sample: int = 5):
        self.knowledge_graph: dict = {}
        self.minimum_sample = int(minimum_sample)

    # -- writing ----------------------------------------------------------
    def store(
        self,
        asset: str,
        vix: float,
        action: str,
        pnl: float,
        return_pct: float = 0.0,
        note: str = "",
    ) -> str:
        key = regime_key(asset, vix)
        drawer = self.knowledge_graph.setdefault(key, {"trades": []})
        drawer["trades"].append(
            {
                "action": action,
                "pnl": round(float(pnl), 2),
                "return_pct": round(float(return_pct), 4),
                "note": note,
            }
        )
        return key

    # -- reading ----------------------------------------------------------
    def recall(self, asset: str, vix: float, sentiment: float = 0.0) -> Recall:
        key = regime_key(asset, vix)
        trades = self.knowledge_graph.get(key, {}).get("trades", [])
        if len(trades) < self.minimum_sample:
            # Deliberately not a zero-filled average. "Not enough to say" and
            # "flat on average" are different claims, and only one is true.
            return Recall(key=key, known=False, trades=len(trades))

        returns = [t["return_pct"] for t in trades]
        wins = sum(1 for t in trades if t["pnl"] > 0)
        return Recall(
            key=key,
            known=True,
            trades=len(trades),
            avg_return=mean(returns),
            win_rate=100.0 * wins / len(trades),
            risk=pstdev(returns) if len(returns) > 1 else 1.0,
        )

    def nodes(self) -> int:
        return len(self.knowledge_graph)

    def total_trades(self) -> int:
        return sum(len(d["trades"]) for d in self.knowledge_graph.values())

    def snapshot(self) -> dict:
        """A copy, for handing to something that must not write to it."""
        return {k: {"trades": list(v["trades"])} for k, v in self.knowledge_graph.items()}

    def restore(self, snapshot: dict) -> None:
        """Roll memory back — used when a phase must not leave a mark."""
        self.knowledge_graph = {k: {"trades": list(v["trades"])} for k, v in snapshot.items()}


__all__ = ["ObsidianMemory", "Recall", "regime_key"]
