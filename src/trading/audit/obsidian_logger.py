"""The audit trail — an Obsidian vault of plain Markdown.

One note per day per firm, one note per day for the brokerage, the brain's
memory under ``brain/``, and an index that links them. Obsidian is not required
to read any of it: the vault is ordinary Markdown files with wiki-links, so the
audit trail outlives the tool that renders it.

What goes in a note is the *decision and its inputs*, never a credential and
never a raw API response.

**Records are appended, views are regenerated, and they live in different
folders.** A decision that has been written cannot be quietly amended by a
later run — that is what makes `firms/`, `brokerage/` and `brain/lessons/`
append-only. A symbol's track record is not a decision, it is a rollup of
every trade in that name, and it changes whenever one closes; regenerating it
loses nothing because the trades it counts are recorded elsewhere. Every
regenerated note says so at the top, so nobody mistakes a view for a record.

The brain's folder is what turns the vault into a graph rather than a diary.
A lesson links to the symbol it is about; the symbol links back to every firm
that traded it; a genome links to the genome it was mutated from. Those
wiki-links are real relationships out of the database, which is why Obsidian's
graph view over this vault shows something true rather than decorative.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from ...db.connection import utcnow
from ...money import fmt_money
from ..config import TradingConfig
from ..models import Fill, TradeProposal


def slug(value: str) -> str:
    """A filename that is safe, stable and still readable in a graph view."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "unknown")).strip("-")
    return (text or "unknown")[:80]


def _link_topic(topic: str) -> str:
    """A topic that is a ticker links to its symbol note; anything else is text."""
    if topic and topic.isupper() and topic.replace("-", "").replace(".", "").isalnum():
        return f"[[brain/symbols/{slug(topic)}|{topic}]]"
    return f"`{topic}`" if topic else "-"


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

    def lesson_note(self, key: str) -> Path:
        return self.vault / "brain" / "lessons" / f"{slug(key)}.md"

    def symbol_note(self, symbol: str) -> Path:
        return self.vault / "brain" / "symbols" / f"{slug(symbol)}.md"

    def genome_note(self, genome_id: int) -> Path:
        return self.vault / "brain" / "genomes" / f"g{int(genome_id):05d}.md"

    def brain_note(self) -> Path:
        return self.vault / "brain" / "index.md"

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

    # -- the brain --------------------------------------------------------
    #
    # Three kinds of note and two different rules. Lessons are appended,
    # because a lesson is something the village concluded at a moment and the
    # history of *when* it concluded it is the interesting part. Symbols and
    # genomes are regenerated: one is a rollup that moves with every closed
    # trade, the other is an immutable row that only ever needs writing once.
    def log_lesson(self, lesson, firm_key: str = "", when: Optional[datetime] = None) -> Path:
        """Append a lesson the village has just drawn, linked to its subject."""
        stamp = (when or utcnow()).strftime("%Y-%m-%d %H:%M")
        topic = getattr(lesson, "topic", "") or ""
        lines = [
            f"## {stamp}",
            "",
            f"> {getattr(lesson, 'statement', '')}",
            "",
            f"- Evidence: {getattr(lesson, 'evidence', '')}",
            f"- Topic: {_link_topic(topic)}",
        ]
        if getattr(lesson, "kind", ""):
            lines.append(f"- Kind: `{lesson.kind}`")
        if firm_key:
            lines.append(f"- Firm: [[firms/{firm_key}/{self._day(when)}|{firm_key}]]")
        return self._append(
            self.lesson_note(getattr(lesson, "key", topic)),
            f"Lesson — {topic}",
            "\n".join(lines),
        )

    def write_symbol(self, record: dict, firms: Sequence[str] = (),
                     lesson_keys: Sequence[str] = ()) -> Path:
        """Regenerate one symbol's track record. A view, and it says so."""
        symbol = str(record.get("symbol") or "").upper()
        path = self.symbol_note(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# {symbol}",
            "",
            "_Regenerated from the ledger — a view of what the trades say, not a "
            "record of them. The trades themselves are in the firm notes._",
            "",
        ]
        # Every fill is remembered, but only the closing leg of a round trip
        # realises anything — an opening buy is recorded with a P&L of zero.
        # Printing "20 trades (1 won, 1 lost)" would read as if eighteen had
        # gone missing, so the note says which is which.
        fills = int(record.get("trades", 0) or 0)
        wins = int(record.get("wins", 0) or 0)
        losses = int(record.get("losses", 0) or 0)
        settled = wins + losses
        lines += [
            f"- Fills remembered: **{fills}**",
            f"- Closed with a result: **{settled}** ({wins} won, {losses} lost)"
            + (f" · {fills - settled} opened or added to a position"
               if fills > settled else ""),
            f"- Net realised: {fmt_money(record.get('net', 0))}",
            f"- Best: {fmt_money(record.get('best', 0))} · "
            f"worst: {fmt_money(record.get('worst', 0))}",
        ]
        if firms:
            lines += ["", "## Traded by", ""]
            lines += [f"- [[firms/{key}/{self._day()}|{key}]]" for key in sorted(set(firms))]
        if lesson_keys:
            lines += ["", "## Lessons about it", ""]
            lines += [f"- [[brain/lessons/{slug(key)}]]" for key in sorted(set(lesson_keys))]
        path.write_text("\n".join(lines) + "\n")
        return path

    def write_genome(self, row: dict, firm_key: str = "") -> Optional[Path]:
        """Write one genome, once. Rows never change, so neither does the note."""
        genome_id = row.get("id")
        if genome_id is None:
            return None
        path = self.genome_note(genome_id)
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        selected = "**selected**" if row.get("selected") else "not selected"
        lines = [
            f"# Genome {genome_id} — generation {row.get('generation', 0)}",
            "",
            f"- Firm: [[firms/{firm_key}/{self._day()}|{firm_key}]]" if firm_key else "- Firm: -",
            f"- Fitness: **{row.get('fitness')}** over {row.get('trades', 0)} trade(s)",
            f"- {selected} · `{row.get('notes') or ''}`",
        ]
        parent = row.get("parent_id")
        if parent:
            lines.append(f"- Mutated from: [[brain/genomes/g{int(parent):05d}]]")
        else:
            lines.append("- Mutated from: — (this one is an incumbent)")
        lines += ["", "```json", str(row.get("genome") or "{}"), "```"]
        path.write_text("\n".join(lines) + "\n")
        return path

    def rebuild_brain(self) -> Path:
        """Regenerate brain/index.md from what is actually on disk."""
        path = self.brain_note()
        path.parent.mkdir(parents=True, exist_ok=True)
        lessons = self._stems("brain/lessons")
        symbols = self._stems("brain/symbols")
        genomes = self._stems("brain/genomes")
        lines = [
            "# The brain",
            "",
            "What the village has learned, what it has learned it about, and "
            "where its strategies came from. Every link below is a relationship "
            "out of the database, not a guess.",
            "",
            f"## Lessons ({len(lessons)})",
            "",
        ]
        lines += [f"- [[brain/lessons/{stem}]]" for stem in lessons[:100]] or ["- (none yet)"]
        lines += ["", f"## Symbols ({len(symbols)})", ""]
        lines += [f"- [[brain/symbols/{stem}]]" for stem in symbols] or ["- (none yet)"]
        lines += ["", f"## Genomes ({len(genomes)})", ""]
        lines += [f"- [[brain/genomes/{stem}]]" for stem in genomes[-100:]] or ["- (none yet)"]
        path.write_text("\n".join(lines) + "\n")
        return path

    def _stems(self, folder: str) -> list:
        root = self.vault / folder
        return sorted(p.stem for p in root.glob("*.md")) if root.exists() else []

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
        if (self.vault / "brain").exists():
            lines += ["", "## The brain", "", "- [[brain/index]]"]
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


__all__ = ["ObsidianLogger", "slug"]
