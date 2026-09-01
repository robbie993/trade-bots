"""The paper loop — one decision a day, and a book that survives the process.

Everything else in this package either runs once and prints, or runs a
thousand times and prints. This is the piece that *keeps* something: a book
with a position in it that is still there tomorrow.

Paper only. There is no venue here but the hostile paper one, no credential is
read, and no code path reaches a broker. Phase 5 of ``DEPLOY.md`` is months of
this before any real money exists, and that is the whole job of the file.

Four things it insists on, three of which are about the day it goes wrong:

**Nothing runs twice.** Cron fires late, cron fires twice, a laptop wakes from
sleep and catches up. The book records the last bar it processed and a second
run on the same bar does nothing. A double-processed bar is a doubled
position, and it would look exactly like a good day.

**Nothing is skipped.** If three bars have printed since the last run — a long
weekend, an outage, a holiday — all three are processed in order. Taking only
the newest would leave an equity curve with holes in it that still plots.

**The genome does not move.** It is loaded from a certificate and its
fingerprint is checked against it. This phase measures what the crucible
certified; a genome that mutates here is measuring nothing, so evolution is
not switched off by a flag, it is absent.

**The scales are checked before the first bar.** A genome certified against
one sentiment scale and run against another is reading different numbers
through the same thresholds, and reports nothing. The certificate carries the
profile it was earned on and the run refuses when the feed does not match.

    python -m hive_mind.live --once      # one day. This is the cron line.
    python -m hive_mind.live --status    # what the book holds right now
    python -m hive_mind.live --catch-up  # every unprocessed bar, then stop
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from src.money import D, ZERO, fmt_money, money

from .engine import GodBrokerEngine
from .evolver import Genome
from .lock import strategy_fingerprint
from .market import Venue
from .memory import ObsidianMemory
from .real_feed import MARKET_DIR, RealDataMissing, RealFeed

BOOK_NAME = "live_book.json"
CERTIFIED = "certified"
JOURNAL = "live_journal.jsonl"


class LiveRefused(RuntimeError):
    """The loop will not start. The reason is always specific and always says why."""


# =========================================================================
# the book
# =========================================================================
@dataclass
class Book:
    """Everything that has to be true again tomorrow morning."""

    genome: dict = field(default_factory=dict)
    genome_fingerprint: str = ""
    strategy_fingerprint: str = ""
    profile: dict = field(default_factory=dict)

    start_capital: Decimal = D(100_000)
    cash: Decimal = D(100_000)
    shares: Decimal = ZERO
    entry_price: Decimal = ZERO
    entry_index: int = 0

    last_bar_date: str = ""
    last_bar_index: int = -1
    steps: int = 0

    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    memory: dict = field(default_factory=dict)
    scouts: list = field(default_factory=list)

    created_at: str = ""
    updated_at: str = ""

    # -- storage ----------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> Optional["Book"]:
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        return cls(
            genome=raw.get("genome", {}),
            genome_fingerprint=raw.get("genome_fingerprint", ""),
            strategy_fingerprint=raw.get("strategy_fingerprint", ""),
            profile=raw.get("profile", {}),
            start_capital=D(raw.get("start_capital", 0)),
            cash=D(raw.get("cash", 0)),
            shares=D(raw.get("shares", 0)),
            entry_price=D(raw.get("entry_price", 0)),
            entry_index=int(raw.get("entry_index", 0)),
            last_bar_date=raw.get("last_bar_date", ""),
            last_bar_index=int(raw.get("last_bar_index", -1)),
            steps=int(raw.get("steps", 0)),
            equity_curve=[D(v) for v in raw.get("equity_curve", [])],
            trades=raw.get("trades", []),
            memory=raw.get("memory", {}),
            scouts=raw.get("scouts", []),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
        )

    def save(self, path: Path) -> None:
        """Write via a temp file and replace, so a crash mid-write keeps
        yesterday's book rather than leaving half of today's."""
        self.updated_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "genome": self.genome,
            "genome_fingerprint": self.genome_fingerprint,
            "strategy_fingerprint": self.strategy_fingerprint,
            "profile": self.profile,
            "start_capital": str(self.start_capital),
            "cash": str(self.cash),
            "shares": str(self.shares),
            "entry_price": str(self.entry_price),
            "entry_index": self.entry_index,
            "last_bar_date": self.last_bar_date,
            "last_bar_index": self.last_bar_index,
            "steps": self.steps,
            "equity_curve": [str(v) for v in self.equity_curve],
            "trades": self.trades,
            "memory": self.memory,
            "scouts": self.scouts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        temp.replace(path)

    # -- reading ----------------------------------------------------------
    def equity(self, mark: Decimal) -> Decimal:
        return money(self.cash + self.shares * D(mark))

    def summary(self, mark: Optional[Decimal] = None) -> str:
        equity = self.equity(mark) if mark is not None else (
            self.equity_curve[-1] if self.equity_curve else self.start_capital
        )
        pnl = equity - self.start_capital
        pct = (pnl / self.start_capital * D(100)) if self.start_capital else ZERO
        position = (
            f"{self.shares:+f} shares at {fmt_money(self.entry_price)}"
            if self.shares
            else "flat"
        )
        return (
            f"genome {self.genome_fingerprint} (strategy {self.strategy_fingerprint})\n"
            f"  through   : {self.last_bar_date or 'nothing yet'} "
            f"({self.steps} day(s) traded)\n"
            f"  position  : {position}\n"
            f"  cash      : {fmt_money(self.cash)}\n"
            f"  equity    : {fmt_money(equity)} "
            f"({pnl:+} — {pct:+.2f}% since {self.created_at[:10] or 'the start'})\n"
            f"  closed    : {len(self.trades)} trade(s)"
        )


# =========================================================================
# starting one
# =========================================================================
def load_certificate(directory: Path, fingerprint: str = "") -> dict:
    """The certified genome, and a refusal that names the alternatives."""
    folder = Path(directory) / CERTIFIED
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    if not files:
        raise LiveRefused(
            f"no certified genome in {folder}. Nothing runs here that has not been "
            "through the crucible:\n    python -m hive_mind.crucible_real"
        )
    if fingerprint:
        match = folder / f"{fingerprint}.json"
        if not match.exists():
            raise LiveRefused(
                f"no certificate for {fingerprint}. Available: "
                + ", ".join(f.stem for f in files)
            )
        files = [match]
    elif len(files) > 1:
        raise LiveRefused(
            f"{len(files)} certified genomes in {folder} — name one with --genome, "
            "because picking for you would be picking the strategy:\n    "
            + "\n    ".join(f.stem for f in files)
        )
    return json.loads(files[0].read_text())


def genome_from(payload: dict) -> Genome:
    """Rebuild the genome and check it is the one the certificate names."""
    genome = Genome(**{k: D(v) for k, v in payload.get("genome", {}).items()})
    named = payload.get("fingerprint", "")
    if named and genome.fingerprint() != named:
        raise LiveRefused(
            f"the certificate names genome {named} but its numbers hash to "
            f"{genome.fingerprint()}. The file has been edited; it licenses nothing."
        )
    return genome


def open_book(directory: Path, capital, fingerprint: str = "") -> Book:
    payload = load_certificate(directory, fingerprint)
    genome = genome_from(payload)
    book = Book(
        genome={k: str(v) for k, v in genome.to_dict().items()},
        genome_fingerprint=genome.fingerprint(),
        strategy_fingerprint=strategy_fingerprint(genome),
        profile=payload.get("profile", {}),
        start_capital=money(capital),
        cash=money(capital),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return book


# =========================================================================
# running one day
# =========================================================================
def _engine_from(book: Book, genome: Genome, bars: list, index: int) -> GodBrokerEngine:
    """Rehydrate the engine at the moment before ``bars[index]`` prints."""
    engine = GodBrokerEngine(
        genome=genome,
        memory=ObsidianMemory(),
        venue=Venue(),
        capital=book.start_capital,
        seed=0,
        online_evolution=False,  # frozen: this phase measures, it does not fit
        verbose=False,
    )
    engine.cash = book.cash
    engine.shares = book.shares
    engine.entry_price = book.entry_price
    engine.entry_index = book.entry_index
    engine.equity_curve = list(book.equity_curve)
    # The council needs its lookback. Handing it the prior bars rebuilds the
    # indicator window without replaying a single trade through the venue.
    engine.history = list(bars[:index])
    engine.memory.knowledge_graph = {
        key: {"trades": [{**t, "pnl": D(t["pnl"]), "return_pct": D(t["return_pct"])}
                         for t in value["trades"]]}
        for key, value in book.memory.items()
    }
    for saved in book.scouts:
        scout = engine.council.scouts[saved["id"] - 1]
        scout.confidence = float(saved["confidence"])
        scout.calls = int(saved["calls"])
        scout.right = int(saved["right"])
    return engine


def _into(book: Book, engine: GodBrokerEngine, bar) -> None:
    book.cash = engine.cash
    book.shares = engine.shares
    book.entry_price = engine.entry_price
    book.entry_index = engine.entry_index
    book.equity_curve = list(engine.equity_curve)
    book.last_bar_date = bar.day
    book.last_bar_index = bar.index
    book.steps += 1
    book.memory = {
        key: {"trades": [{**t, "pnl": str(t["pnl"]), "return_pct": str(t["return_pct"])}
                         for t in value["trades"]]}
        for key, value in engine.memory.knowledge_graph.items()
    }
    book.scouts = [
        {"id": s.id, "confidence": s.confidence, "calls": s.calls, "right": s.right}
        for s in engine.council.scouts
    ]
    book.trades = [
        {
            "opened": t.opened, "closed": t.closed, "side": t.side,
            "shares": str(t.shares), "entry": str(t.entry), "exit": str(t.exit),
            "pnl": str(t.pnl), "return_pct": str(t.return_pct),
            "vix": str(t.vix), "regime": t.regime,
        }
        for t in engine.trades
    ]


def journal(directory: Path, record: dict) -> None:
    """One line per decision, append-only. The book is state; this is history."""
    path = Path(directory) / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            prefix = "" if handle.read(1) == b"\n" else "\n"
    with path.open("a") as handle:
        handle.write(prefix + json.dumps(record, sort_keys=True) + "\n")


def step_once(book: Book, feed, directory: Path, on_note=print) -> int:
    """Process every bar the book has not seen. Returns how many."""
    bars = feed.bars()
    if not bars:
        raise LiveRefused("the feed has no bars")

    # The scales the certificate was earned on, checked before anything trades.
    live_profile = feed.profile() if hasattr(feed, "profile") else {}
    if book.profile and live_profile:
        differences = [
            f"{k}: certified on {book.profile[k]!r}, this feed is {live_profile.get(k)!r}"
            for k in sorted(book.profile)
            if book.profile[k] != live_profile.get(k)
        ]
        if differences:
            raise LiveRefused(
                "this genome was certified on different data than it is being run on — "
                + "; ".join(differences)
                + ". Its thresholds are calibrated to a scale nothing here is using; "
                "re-run the crucible against this feed."
            )

    # Integrity before the quiet-day shortcut. A tampered book that is only
    # noticed on the next trading day is one that was trusted in between.
    genome = Genome(**{k: D(v) for k, v in book.genome.items()})
    if genome.fingerprint() != book.genome_fingerprint:
        raise LiveRefused(
            f"the book says genome {book.genome_fingerprint} but its numbers hash to "
            f"{genome.fingerprint()} — the file has been edited"
        )

    pending = [b for b in bars if b.index > book.last_bar_index]
    if not pending:
        on_note(f"· nothing new — the book is already through {book.last_bar_date}")
        return 0

    for bar in pending:
        engine = _engine_from(book, genome, bars, bar.index)
        decision = engine.step(bar)
        _into(book, engine, bar)

        equity = book.equity(bar.close)
        on_note(
            f"  {bar.day}  {decision.action:<5} "
            f"{f'{decision.leverage}x' if decision.is_trade else '     '}  "
            f"close {fmt_money(bar.close)}  vix {bar.vix}  "
            f"-> {fmt_money(equity)}"
        )
        journal(
            directory,
            {
                "date": bar.day,
                "bar": bar.index,
                "close": str(bar.close),
                "vix": str(bar.vix),
                "sentiment": str(bar.sentiment),
                "action": decision.action,
                "leverage": str(decision.leverage),
                "confidence": str(decision.confidence),
                "reason": decision.reason[:200],
                "shares": str(book.shares),
                "cash": str(book.cash),
                "equity": str(equity),
                "genome": book.genome_fingerprint,
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )
    return len(pending)


# =========================================================================
# CLI
# =========================================================================
def run(
    directory: Path,
    capital,
    fingerprint: str,
    status_only: bool,
    catch_up: bool,
    allow_vix_proxy: bool,
) -> int:
    directory = Path(directory)
    path = directory / BOOK_NAME
    book = Book.load(path)

    if status_only:
        if book is None:
            print(f"no book at {path}. Start one with `python -m hive_mind.live --once`.")
            return 1
        print(book.summary())
        print(f"\n  book      : {path}\n  journal   : {directory / JOURNAL}")
        return 0

    try:
        feed = RealFeed(directory=directory, allow_vix_proxy=allow_vix_proxy)
        feed.bars()
    except RealDataMissing as exc:
        print(f"{exc}")
        return 1

    opened = book is None
    if book is None:
        try:
            book = open_book(directory, capital, fingerprint)
        except LiveRefused as exc:
            print(f"{exc}")
            return 1
        # A new book starts at the end of the tape, not the beginning. Replaying
        # thirty years through it would produce a backtest wearing a live book's
        # name — and the crucible is where backtests belong.
        book.last_bar_index = feed.bars()[-1].index - (1 if catch_up else 0)
        book.profile = book.profile or feed.profile()
        print(
            f"opened a paper book on genome {book.genome_fingerprint} with "
            f"{fmt_money(book.start_capital)}\n"
            f"  starting at {feed.bars()[-1].day}; it trades from the next bar on.\n"
            f"  PAPER ONLY — there is no broker in this package."
        )

    try:
        processed = step_once(book, feed, directory, on_note=print)
    except LiveRefused as exc:
        print(f"\n✋ {exc}")
        return 1

    # Save a freshly opened book even on a quiet day. Otherwise it is rebuilt
    # from the certificate on every run, `created_at` resets each time, and the
    # loop that is supposed to hold a position never holds anything.
    if processed or opened:
        book.save(path)
    if processed:
        print()
    print(book.summary(feed.bars()[-1].close))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hive_mind.live",
        description="Trade one certified genome on paper, one day at a time.",
    )
    parser.add_argument("--dir", default=str(MARKET_DIR))
    parser.add_argument("--genome", default="", help="fingerprint, if several are certified")
    parser.add_argument("--capital", default="100000", help="paper capital for a new book")
    parser.add_argument("--once", action="store_true", help="process new bars and exit (cron)")
    parser.add_argument("--status", action="store_true", help="what the book holds")
    parser.add_argument(
        "--catch-up",
        action="store_true",
        help="on a new book, also trade the newest bar rather than waiting for the next",
    )
    parser.add_argument("--allow-vix-proxy", action="store_true")
    args = parser.parse_args(argv)

    if not (args.once or args.status):
        parser.error("choose --once (the cron line) or --status")

    return run(
        directory=Path(args.dir),
        capital=args.capital,
        fingerprint=args.genome,
        status_only=args.status,
        catch_up=args.catch_up,
        allow_vix_proxy=args.allow_vix_proxy,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["BOOK_NAME", "Book", "LiveRefused", "journal", "open_book", "run", "step_once"]
