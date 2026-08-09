"""The jurors who sit on a decision the human gate used to hold.

Same shape as the strategy court's jury, and for the same reason: a weighted
panel where any single juror can be read on its own, and where three of them
carry a **veto** — a fact so disqualifying that no amount of good performance
outvotes it.

The two design rules that matter:

**A juror with nothing to say abstains.** It does not vote for the motion. A
panel where silence counts as support is a panel that approves everything on a
quiet day, which is precisely how the strategy court's hygiene jurors were
miscalibrated the first time round.

**Missing evidence is never a grant.** Below the sample gate the answer is
"insufficient data", and insufficient data defers to a human rather than
guessing. This is the same rule the kill criteria have always followed, moved
one level up.
"""

from __future__ import annotations

from ...money import D, ZERO
from ..court.jury import Finding, _abstain, _against, _for

# What each juror may be worth. Vetoes are absolute and carry no weight.
HEAVY, MEDIUM, LIGHT = D("60"), D("40"), D("25")


# =========================================================================
# the vetoes — true of every decision, whatever it is
# =========================================================================
def books(evidence) -> Finding:
    """Nothing is decided on books that do not add up."""
    if not evidence.books_reconcile:
        return _against(
            "books", 100,
            f"the ledger does not reconcile: {evidence.reconciliation}", veto=True,
        )
    return _abstain("books", "cash and equity both reconcile")


def firm_exists(evidence) -> Finding:
    if evidence.firm_key and not evidence.firm_exists:
        return _against(
            "firm_exists", 100, f"no firm called {evidence.firm_key}", veto=True
        )
    return _abstain("firm_exists", "the firm is on the books")


def sample_gate(evidence) -> Finding:
    """Below the gate there is no verdict to give — not a bad one, none."""
    if not evidence.sufficient_data:
        return _against(
            "sample_gate", 100,
            f"{evidence.closed_trades} closed trade(s) — insufficient data, "
            "which is not a reason to act",
            veto=True,
        )
    return _abstain("sample_gate", f"{evidence.closed_trades} closed trades is enough to read")


# The three conditions `should_kill_firm` checks *before* the sample gate,
# because they are not statistical judgements about an edge — they are the
# account being emptied. A council that demanded twenty trades before it would
# act on a 40% drawdown would be reproducing the exact mistake the kill switch
# was written to avoid.
PRE_GATE = ("Drawdown", "Worst single trade", "Consecutive losses")


def sample_gate_for_kill(evidence) -> Finding:
    """Same gate, except where the ledger already overrode it."""
    urgent = [c for c in evidence.tripped if c in PRE_GATE]
    if urgent:
        return _abstain(
            "sample_gate",
            f"{', '.join(urgent)} is checked before the sample gate — "
            "the account is being emptied, which is not a question of sample size",
        )
    if not evidence.sufficient_data:
        return _against(
            "sample_gate", 100,
            f"{evidence.closed_trades} closed trade(s) — insufficient data, "
            "which is not a reason to kill",
            veto=True,
        )
    return _abstain("sample_gate", f"{evidence.closed_trades} closed trades is enough to read")


# =========================================================================
# raising an allocation
# =========================================================================
def score(evidence) -> Finding:
    if evidence.score >= evidence.good_score:
        return _for("score", HEAVY, f"score {evidence.score} is at or above "
                                    f"{evidence.good_score}")
    if evidence.score <= evidence.poor_score:
        return _against("score", HEAVY, f"score {evidence.score} is at or below "
                                        f"{evidence.poor_score}")
    return _abstain("score", f"score {evidence.score} is between the bands")


def performance(evidence) -> Finding:
    if evidence.return_pct > ZERO:
        return _for("performance", MEDIUM, f"up {evidence.return_pct}% on capital")
    return _against("performance", MEDIUM, f"down {evidence.return_pct}% on capital")


def drawdown(evidence) -> Finding:
    if evidence.drawdown_pct >= D("20"):
        return _against("drawdown", HEAVY, f"{evidence.drawdown_pct}% off its high-water mark")
    if evidence.drawdown_pct <= D("5"):
        return _for("drawdown", LIGHT, f"only {evidence.drawdown_pct}% off its high")
    return _abstain("drawdown", f"{evidence.drawdown_pct}% drawdown is unremarkable")


def win_rate(evidence) -> Finding:
    if evidence.win_rate_pct >= D("55"):
        return _for("win_rate", LIGHT, f"{evidence.win_rate_pct}% of trades won")
    if evidence.win_rate_pct < D("40"):
        return _against("win_rate", MEDIUM, f"only {evidence.win_rate_pct}% of trades won")
    return _abstain("win_rate", f"{evidence.win_rate_pct}% is neither here nor there")


def headroom(evidence) -> Finding:
    """The brokerage's cap is not the council's to raise."""
    if evidence.delta > evidence.headroom:
        return _against(
            "headroom", 100,
            f"the raise is {evidence.delta} and only {evidence.headroom} is "
            "left under the brokerage's total capital",
            veto=True,
        )
    return _abstain("headroom", f"{evidence.headroom} left under the cap")


def ceiling(evidence) -> Finding:
    """A firm may not grow without limit off its own starting stake."""
    if evidence.max_allocation and evidence.new_allocation > evidence.max_allocation:
        return _against(
            "ceiling", 100,
            f"{evidence.new_allocation} is past this firm's ceiling of "
            f"{evidence.max_allocation}",
            veto=True,
        )
    return _abstain("ceiling", "inside the firm's allocation ceiling")


def compounding(evidence) -> Finding:
    """The failure the human gate existed to prevent, stated as a juror.

    Granting a raise every tick to the same firm is how a run-away allocation
    happens, and it happens fastest to the firm that is *currently* winning.
    """
    if evidence.recent_council_grants >= 3:
        return _against(
            "compounding", 100,
            f"the council has already raised {evidence.firm_key} "
            f"{evidence.recent_council_grants} times recently; that is compounding, "
            "and it needs a human",
            veto=True,
        )
    if evidence.recent_council_grants:
        return _against(
            "compounding", MEDIUM,
            f"{evidence.recent_council_grants} recent raise(s) already granted",
        )
    return _abstain("compounding", "no recent raises to this firm")


# =========================================================================
# killing a firm
# =========================================================================
def kill_condition(evidence) -> Finding:
    """A kill has to be the ledger's conclusion, not the council's opinion."""
    if evidence.notes.get("kill_condition_met"):
        return _for(
            "kill_condition", HEAVY,
            f"a kill condition is met: {evidence.notes.get('kill_reason', '')}",
        )
    return _against(
        "kill_condition", 100,
        "no kill condition is currently met; killing would not be the ledger's "
        "conclusion",
        veto=True,
    )


def sustained(evidence) -> Finding:
    if len(evidence.tripped) > 1:
        return _for("sustained", MEDIUM,
                    f"{len(evidence.tripped)} conditions tripped at once: "
                    + ", ".join(evidence.tripped))
    if evidence.tripped:
        return _abstain("sustained", f"one condition tripped: {evidence.tripped[0]}")
    return _abstain("sustained", "nothing tripped")


def already_paused(evidence) -> Finding:
    """Pausing already stopped the bleeding. A kill is the irreversible part."""
    if evidence.status == "paused":
        return _for("already_paused", LIGHT,
                    "the firm is already paused, so a kill costs nothing further")
    return _against("already_paused", MEDIUM,
                    f"the firm is {evidence.status}; pausing comes before killing")


def open_positions(evidence) -> Finding:
    if evidence.open_positions:
        return _against(
            "open_positions", LIGHT,
            f"{evidence.open_positions} position(s) still open — a killed firm "
            "exits over the next ticks rather than at once",
        )
    return _for("open_positions", LIGHT, "flat; nothing left to unwind")


# =========================================================================
# un-pausing a firm
# =========================================================================
def condition_cleared(evidence) -> Finding:
    """The only thing that earns a resume: the reason for the pause is gone."""
    if evidence.tripped:
        return _against(
            "condition_cleared", 100,
            "the condition that paused it is still tripped: "
            + ", ".join(evidence.tripped),
            veto=True,
        )
    return _for("condition_cleared", HEAVY,
                "no kill condition is tripped any more")


def recovered(evidence) -> Finding:
    if evidence.drawdown_pct <= D("10"):
        return _for("recovered", MEDIUM,
                    f"back to within {evidence.drawdown_pct}% of its high")
    return _against("recovered", MEDIUM,
                    f"still {evidence.drawdown_pct}% below its high-water mark")


def solvent(evidence) -> Finding:
    if evidence.withdrawable > ZERO:
        return _for("solvent", LIGHT, f"{evidence.withdrawable} of cash to trade with")
    return _against("solvent", HEAVY, "no uninvested cash; resuming would do nothing")


# =========================================================================
# moving capital between firms
# =========================================================================
def seller_can_pay(evidence) -> Finding:
    if evidence.from_firm and evidence.from_firm_withdrawable <= ZERO:
        return _against(
            "seller_can_pay", 100,
            f"{evidence.from_firm} has no uninvested cash to hand over",
            veto=True,
        )
    return _abstain("seller_can_pay", "the seller can cover it")


def transfer_is_internal(evidence) -> Finding:
    """Capital moving between two firms does not change what is at stake."""
    if evidence.kind == "capital_transfer":
        return _for("transfer_is_internal", LIGHT,
                    "both legs are inside the village; total deployed capital "
                    "does not change")
    return _abstain("transfer_is_internal", "not a transfer")


# The panel, per action. Order is the order they are read in.
RAISE = (books, firm_exists, sample_gate, headroom, ceiling, compounding,
         score, performance, drawdown, win_rate)
KILL = (books, firm_exists, sample_gate_for_kill, kill_condition, sustained,
        already_paused, open_positions)
RESUME = (books, firm_exists, condition_cleared, recovered, solvent)
TRANSFER = (books, firm_exists, seller_can_pay, headroom, transfer_is_internal,
            score, drawdown)


__all__ = ["KILL", "RAISE", "RESUME", "TRANSFER"]
