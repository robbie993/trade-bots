"""The audit trail — an Obsidian vault of plain Markdown.

One note per day per firm, one note per day for the brokerage, and an index
that links them. Obsidian is not required to read any of it: the vault is
ordinary Markdown files with wiki-links, so the audit trail outlives the tool
that renders it.

What goes in a note is the *decision and its inputs*, never a credential and
never a raw API response. Files are appended to rather than rewritten, so an
entry that has been written cannot be quietly amended by a later run.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from ...db.connection import utcnow
from ...money import fmt_money
from ..config import TradingConfig
from ..models import Fill, TradeProposal


class ObsidianLogger:
    def __init__(self, vault: Optional[Path] = None, config: Optional[TradingConfig] = None):
        cfg = config or TradingConfig()
        self.vault = Path(vault or cfg.audit_vault)

    # -- paths ------------------------------------------------------------
    def _day(self, when: Optional[datetime] = None) -> str:
        return (when or utcnow()).strftime("%Y-%m-%d")

    def firm_note(self, firm_key: str, when: Optional[datetime] = None) -> Path:
        return self.vault / "firms" / firm_key / f"{self._day(when)}.md"

    def brokerage_note(self, when: Optional[datetime] = None) -> Path:
        return self.vault / "brokerage" / f"{self._day(when)}.md"

    def index_note(self) -> Path:
        return self.vault / "index.md"

    # -- writing ----------------------------------------------------------
    def _append(self, path: Path, header: str, body: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        first_write = not path.exists()
        with path.open("a") as handle:
            if first_write:
                handle.write(f"# {header}\n\n")
            handle.write(body.rstrip() + "\n\n")
        return path

    def log_trade(
        self,
        firm_key: str,
        proposal: TradeProposal,
        fill: Optional[Fill] = None,
        narration: str = "",
    ) -> Path:
        stamp = (proposal.as_of or utcnow()).strftime("%Y-%m-%d %H:%M")
        lines = [
            f"## {stamp} — {proposal.side.upper()} {proposal.symbol}",
            "",
            f"- Quantity: {proposal.quantity} @ ~{proposal.reference_price}",
            f"- Notional: {fmt_money(proposal.notional)}",
            f"- Debate: **{proposal.debate_winner or 'undecided'}** "
            f"(confidence {proposal.confidence})",
            f"- Risk: `{proposal.risk_verdict}` — {proposal.risk_reason}",
            f"- Ethics: `{proposal.ethics_verdict}` — {proposal.ethics_reason}",
            f"- Status: **{proposal.status}**",
        ]
        if fill is not None:
            lines.append(
                f"- Fill: {fill.quantity} @ {fill.price} "
                f"(fee {fmt_money(fill.fee)}, cash {fmt_money(fill.cash_delta)}, "
                f"realised {fmt_money(fill.realized_pnl)})"
            )
        if proposal.rationale:
            lines += ["", f"> {proposal.rationale}"]
        if proposal.bull_case:
            lines += ["", "### Bull", "```", proposal.bull_case, "```"]
        if proposal.bear_case:
            lines += ["", "### Bear", "```", proposal.bear_case, "```"]
        if narration:
            lines += ["", "### Narration", narration]
        return self._append(
            self.firm_note(firm_key, proposal.as_of), f"{firm_key} — {self._day(proposal.as_of)}", "\n".join(lines)
        )

    def log_oversight(self, report, when: Optional[datetime] = None) -> Path:
        stamp = (when or utcnow()).strftime("%Y-%m-%d %H:%M")
        lines = [f"## {stamp} — brokerage oversight", ""]
        if report.reconciliation is not None:
            lines.append(f"- Reconciliation: {report.reconciliation.summary()}")
        for card in report.cards:
            gate = "" if card.sufficient_data else " _(below sample gate)_"
            lines.append(
                f"- [[firms/{card.firm_key}/{self._day(when)}|{card.firm_key}]]: "
                f"score **{card.score}**, equity {fmt_money(card.equity)}, "
                f"return {card.return_pct}%, drawdown {card.drawdown_pct}%, "
                f"{card.closed_trades} closed trades{gate}"
            )
        for paused in report.paused:
            lines.append(f"- ⏸ **PAUSED** {paused['firm']}: {paused['reason']}")
        for change in report.allocation_changes:
            lines.append(f"- 💰 {change}")
        if report.halted:
            lines.append(f"- 🛑 **HALTED**: {report.halted['reason']}")
        if report.leaderboard is not None:
            lines += ["", "### Leaderboard", "", "```", report.leaderboard.render(), "```"]
        return self._append(
            self.brokerage_note(when), f"Brokerage — {self._day(when)}", "\n".join(lines)
        )

    def log_note(self, title: str, body: str, when: Optional[datetime] = None) -> Path:
        return self._append(self.brokerage_note(when), f"Brokerage — {self._day(when)}",
                            f"## {title}\n\n{body}")

    def rebuild_index(self) -> Path:
        """Regenerate index.md from what is actually on disk."""
        path = self.index_note()
        path.parent.mkdir(parents=True, exist_ok=True)
        firm_dirs = sorted((self.vault / "firms").glob("*")) if (self.vault / "firms").exists() else []
        broker_notes = (
            sorted((self.vault / "brokerage").glob("*.md"), reverse=True)
            if (self.vault / "brokerage").exists()
            else []
        )
        lines = [
            "# The AI Village — Trading Edition",
            "",
            "Audit trail. Every note is appended to, never rewritten.",
            "",
            "## Brokerage",
            "",
        ]
        lines += [f"- [[brokerage/{note.stem}]]" for note in broker_notes[:60]] or ["- (none yet)"]
        lines += ["", "## Firms", ""]
        if firm_dirs:
            for firm_dir in firm_dirs:
                notes = sorted(firm_dir.glob("*.md"), reverse=True)
                lines.append(f"### {firm_dir.name}")
                lines += [f"- [[firms/{firm_dir.name}/{n.stem}]]" for n in notes[:30]]
                lines.append("")
        else:
            lines.append("- (none yet)")
        path.write_text("\n".join(lines) + "\n")
        return path

    def entries(self, firm_key: Optional[str] = None) -> list:
        """Every note path in the vault, newest first."""
        root = self.vault / "firms" / firm_key if firm_key else self.vault
        if not root.exists():
            return []
        return sorted(root.rglob("*.md"), reverse=True)


__all__ = ["ObsidianLogger"]
