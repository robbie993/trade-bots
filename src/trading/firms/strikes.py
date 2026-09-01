"""Three strikes: suspend, suspend longer, then terminate.

The village's only response to a bad run used to be death. Six firms died in
nine days, every one of them on a kill counter that was counting fills instead
of bars, and every one of those kills was wrong. A system whose sole reaction
to failure is destruction gets quieter each time it is mistaken, and it throws
away what the firm had learned on the way out.

So a firm now gets three chances:

    strike 1   suspended for GULAG_BARS_FIRST bars
    strike 2   suspended for GULAG_BARS_SECOND bars
    strike 3   terminated — the kill is requested, bankruptcy takes the estate

Two things about this are load-bearing, and both were established by measuring
against the village's own history rather than by taste.

**A strike is an episode, not a state.** Drawdown persists: a firm 27% under
its high-water mark is in breach on every bar until it recovers. Counting a
strike per bar gives `firm_c_crypto` 54 strikes and `firm_h_global` 50 — both
terminated within three hours over a single bad stretch, which is harsher than
the kill switch it replaces, not gentler. Counting episodes gives them 2 each.
`breach_open` is the flag that tells them apart: a firm earns its next strike
only after climbing back out and falling in again.

**The sentence is served in bars, not days.** Twenty bars is twenty hours to a
crypto desk trading around the clock and three trading days to a bonds desk
that keeps exchange hours. Storing a release *time* would hand those two firms
sentences differing by a factor of three-and-a-half while appearing to treat
them identically — the same unit confusion that has now produced seven separate
bugs in this codebase, including the ninety-six-bar staleness window and the
six false kills. So the counter decrements once per bar the village actually
observes, which is also the only definition that survives a weekend, a holiday
and a feed freeze.

**What the gulag is for.** A suspended firm holds no capital and earns no
tokens, but it is not idle: `brain/evolver.py` keeps mutating its genome and
backtesting the variants on identical data, promoting one only when it beats
the incumbent re-scored on that same data. A firm comes back having actually
earned a change rather than having simply waited. Without that the suspension
is a bench, not a sentence with a point.

Against the real ledger this saves four of the six firms that died: `a_etf` and
`b_stocks` still reach three strikes, `c_crypto`, `e_momentum` and `h_global`
land in the longer gulag, and `i_memecoins` gets a first warning.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from ...money import D


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except ValueError:
        return default


@dataclass
class StrikeConfig:
    """How many chances, and how long each one costs."""

    #: Strikes before termination. Three, because two is a coin toss and four
    #: is a firm that has been failing for a fortnight.
    max_strikes: int = field(default_factory=lambda: _env_int("TRADE_MAX_STRIKES", 3))
    #: Bars — not days — served for the first and second strike.
    gulag_bars_first: int = field(
        default_factory=lambda: _env_int("TRADE_GULAG_BARS_FIRST", 20)
    )
    gulag_bars_second: int = field(
        default_factory=lambda: _env_int("TRADE_GULAG_BARS_SECOND", 40)
    )

    def sentence(self, strike: int) -> int:
        """Bars owed for the nth strike. The last strike has no sentence."""
        if strike <= 1:
            return self.gulag_bars_first
        return self.gulag_bars_second


@dataclass
class StrikeOutcome:
    """What this bar did to one firm. Every field is reportable."""

    firm_key: str
    struck: bool = False
    released: bool = False
    terminated: bool = False
    strikes: int = 0
    bars_left: int = 0
    reason: str = ""

    def describe(self) -> str:
        if self.terminated:
            return (
                f"{self.firm_key}: strike {self.strikes} — TERMINATED. {self.reason}"
            )
        if self.struck:
            return (
                f"{self.firm_key}: strike {self.strikes} of 3 — {self.bars_left} "
                f"bars in the gulag. {self.reason}"
            )
        if self.released:
            return f"{self.firm_key}: sentence served, back to work"
        return ""


def serve_sentence(store, firm, bar: str) -> Optional[StrikeOutcome]:
    """Count down one bar of a suspended firm's sentence. Release at zero.

    Decrements once per *distinct* bar. The loop calls this every tick, and a
    counter that dropped once per tick would turn a twenty-bar sentence into
    twenty minutes — which is the same mistake as the borrow charge, the Sharpe
    annualisation and the kill counter, and is why `gulag_last_bar` is stored.
    """
    state = store.strike_state(firm.id)
    left = state["gulag_bars_left"]
    if left <= 0:
        return None
    # **Ordered, not equal.** Equality assumes the bar only ever moves forward,
    # and `market.as_of()` does not: a partial bar drops in and out of a fetch,
    # so the village's bar was measured alternating 16:00 / 16:15 / 16:00
    # within single minutes. Against `==`, every flip is a new bar and the
    # sentence counts down twice as fast — an 80-bar sentence served in about
    # two hours instead of twenty. ISO bar keys are fixed-width, so string
    # ordering is chronological. The caller clamps this too; the guard is
    # ordered as well because `gulag_last_bar` is persisted and has to be right
    # across a restart, when the caller's high-water mark starts empty.
    if not bar or bar <= (state["gulag_last_bar"] or ""):
        return None                     # already counted, or time went backwards

    left -= 1
    store.set_gulag(firm.id, left, bar)
    if left > 0:
        return None
    store.release_from_gulag(firm.id)
    return StrikeOutcome(firm_key=firm.firm_key, released=True,
                         strikes=state["strikes"])


def record_breach(store, firm, reason: str, config: StrikeConfig) -> Optional[StrikeOutcome]:
    """A firm has tripped a kill criterion. Give it a strike, or terminate it.

    Returns None when the firm is already inside the same episode — the flag
    that stops one long drawdown becoming fifty-four strikes.
    """
    state = store.strike_state(firm.id)
    if state["breach_open"]:
        return None                     # same slump, already paid for

    strikes = state["strikes"] + 1
    if strikes >= config.max_strikes:
        store.set_strikes(firm.id, strikes, breach_open=1, reason=reason)
        return StrikeOutcome(firm_key=firm.firm_key, struck=True, terminated=True,
                             strikes=strikes, reason=reason)

    bars = config.sentence(strikes)
    store.set_strikes(firm.id, strikes, breach_open=1, reason=reason)
    store.set_gulag(firm.id, bars, None)
    return StrikeOutcome(firm_key=firm.firm_key, struck=True, strikes=strikes,
                         bars_left=bars, reason=reason)


def clear_breach(store, firm) -> None:
    """The firm is back above the line. Its next slump is a new episode."""
    if store.strike_state(firm.id)["breach_open"]:
        store.set_breach_open(firm.id, 0)


__all__ = [
    "StrikeConfig",
    "StrikeOutcome",
    "serve_sentence",
    "record_breach",
    "clear_breach",
]
