"""Guards against believing your own results.

Ported, near-verbatim, from `research_guards.py` in the sibling repo
(`/Users/robbie/trade`), where each one was written after something actually
went wrong rather than as good practice in the abstract. Two of the five are
here because they are the two this village is about to need; the other three
are covered by machinery it already has — `brokerage/reconciliation.py` does a
stricter job than G1 for this ledger's model, and the feed's `unpriceable`
handling plus `MarketData.lagging` cover G3.

**Why this matters more than another metric.** The village is about to compare
four universe models. Four arms is four looks, and the arm that wins by chance
looks exactly like the arm that wins by merit. The sibling repo learned this
expensively: Kronos was measured four times and returned four nulls, and a
four-way strategy stack looked promising until it was run against a random
control and tied it at t=+0.79. Without a record of how often you have looked,
a future p=0.04 reads as a discovery instead of as the one-in-twenty you should
expect after twenty looks.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Optional


class GuardFailure(AssertionError):
    """Raised rather than returned: a guard that can be ignored is decoration."""


# =========================================================================
# every input's real range, and the window they all support
# =========================================================================
def date_span(obj, key: str = "d") -> tuple:
    """(first, last) across a price-file-shaped dict, or a list of ISO dates."""
    if isinstance(obj, dict):
        lo = hi = None
        for value in obj.values():
            dates = value.get(key) if isinstance(value, dict) else None
            if not dates:
                continue
            lo = dates[0] if lo is None else min(lo, dates[0])
            hi = dates[-1] if hi is None else max(hi, dates[-1])
        return lo, hi
    dates = sorted(obj)
    return (dates[0], dates[-1]) if dates else (None, None)


def intersect_spans(spans: dict, label: str = "run") -> dict:
    """The window every input actually supports, and which input binds it.

    Comparing arms over different windows is not a comparison. In the sibling
    repo one input ended three months before the others, which forced two runs
    onto a sub-window and made "remember to re-derive the benchmark on the same
    dates" a manual step — the kind that gets skipped once and produces a
    calendar artefact nobody can find afterwards.
    """
    usable = {k: v for k, v in spans.items() if v and v[0] and v[1]}
    if not usable:
        raise GuardFailure(f"[{label}] no input declared a date range")
    lo = max(v[0] for v in usable.values())
    hi = min(v[1] for v in usable.values())
    if lo >= hi:
        raise GuardFailure(f"[{label}] inputs do not overlap: {usable}")
    return {
        "start": lo,
        "end": hi,
        "binding_start": [k for k, v in usable.items() if v[0] == lo],
        "binding_end": [k for k, v in usable.items() if v[1] == hi],
        "spans": usable,
    }


# =========================================================================
# every test ever run
# =========================================================================
class PValueLedger:
    """Every test ever run, so a new p-value is read against its own history.

    A p-value answers "how surprising is this if nothing is going on" for *one*
    look. Take twenty looks and one of them comes back at 0.05 by construction.
    This records the looks, so the twentieth is read as the twentieth.
    """

    #: Where the ledger lives when no path is given. An env var rather than a
    #: constant because the evolver now records a look every generation, and
    #: the first test run after that shipped wrote `evolver:alpha`,
    #: `evolver:beta` and `evolver:test_firm` into the real ledger — test
    #: fixtures counted as real looks against real subjects. A ledger whose
    #: job is counting how often you looked cannot be polluted by the suite
    #: that checks the counting. `conftest` points this at a tmp path.
    ENV_PATH = "TRADE_PVALUE_LEDGER"

    def __init__(self, path: Optional[str] = None):
        self.path = str(path or os.environ.get(self.ENV_PATH)
                        or Path(__file__).resolve().parents[2]
                        / "data" / "pvalue_ledger.json")
        try:
            self.rows = json.load(open(self.path)) if os.path.exists(self.path) else []
        except (OSError, ValueError):
            self.rows = []

    def record(self, subject: str, test: str, p: float, run_date: str,
               note: str = "", verdict: str = "") -> None:
        """Write one result. Re-running the same test on the same day replaces
        it rather than counting as a second look — a re-run is not new
        evidence, and counting it as one would punish fixing a bug."""
        self.rows = [r for r in self.rows
                     if not (r["subject"] == subject and r["test"] == test
                             and r["run_date"] == run_date)]
        self.rows.append({"subject": subject, "test": test, "p": float(p),
                          "run_date": run_date, "note": note, "verdict": verdict})
        self.rows.sort(key=lambda r: (r["subject"], r["run_date"], r["test"]))
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            json.dump(self.rows, open(self.path, "w"), indent=1)
        except OSError:
            pass        # a ledger that cannot be written must not fail a run

    def context(self, subject: str, p: float) -> dict:
        """What this p-value is worth given how often the subject was tested.

        Šidák: P(at least one result this good | every null true) over k looks.
        """
        prior = [r for r in self.rows if r["subject"] == subject]
        k = len(prior) + 1
        return {
            "subject": subject,
            "p": p,
            "tests_including_this": k,
            "family_wise_p": 1.0 - (1.0 - p) ** k,
            "best_prior_p": min([r["p"] for r in prior], default=None),
            "bonferroni_floor": 0.05 / k,
            "survives_bonferroni_05": p < 0.05 / k,
        }

    def report(self) -> str:
        by: dict = {}
        for row in self.rows:
            by.setdefault(row["subject"], []).append(row)
        out = []
        for subject, rows in sorted(by.items()):
            best = min(rows, key=lambda r: r["p"])
            out.append(
                f"  {subject:<26} {len(rows):>2} tests   best p={best['p']:.3f} "
                f"({best['run_date']}, {best['test']})   "
                f"next one needs p<{0.05 / (len(rows) + 1):.4f}"
            )
        return "\n".join(out) or "  (no tests recorded)"


def spearman(xs, ys) -> tuple:
    """Rank correlation and a two-sided p, as `(rho, p, n)`.

    The test that separates a strategy from a curve fit: does the ranking a
    rule produces on the bars it saw predict the ranking on bars it did not?
    Coval, Hirshleifer and Shumway's load-bearing result was this and not
    their headline — top-decile performance in the first half predicting the
    second half. A difference in means cannot do the same job, because it is
    dominated by whichever window each side was measured on.

    Run once on this village's whole genome population it returned rho=+0.056
    on n=280: the selection rule carries no out-of-sample information.

    `p` comes from the usual t transform read against a normal, which is
    close enough above roughly n=20 and conservative below it. Ties are given
    average ranks.
    """
    xs, ys = list(xs), list(ys)
    n = len(xs)
    if n < 3 or n != len(ys):
        return (0.0, 1.0, n)

    def ranked(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            share = (i + j) / 2.0
            for k in range(i, j + 1):
                out[order[k]] = share
            i = j + 1
        return out

    rx, ry = ranked(xs), ranked(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    if den == 0:
        return (0.0, 1.0, n)          # everything tied: no ranking to test
    rho = num / den
    if abs(rho) >= 1:
        return (rho, 0.0, n)
    t = rho * ((n - 2) / (1 - rho * rho)) ** 0.5
    p = math.erfc(abs(t) / (2 ** 0.5))
    return (rho, min(1.0, max(0.0, p)), n)


__all__ = ["GuardFailure", "PValueLedger", "date_span", "intersect_spans",
           "spearman"]
