"""The jury — twelve jurors, none of whom have an opinion.

The build document asks for twelve experts. Twelve *opinions* about a strategy
file are twelve things nobody can check, so each juror here is a deterministic
function of the evidence and one backtest, returning a verdict, a weight and a
reason you can re-derive from the stored case row.

Three of them are vetoes. A file that reaches for the network, evaluates
strings, or does not parse is refused no matter how well it backtests — a
profitable strategy that opens a socket is not a profitable strategy, it is a
socket.

The jurors split into two kinds, and the split is what keeps the ruling
honest:

**Hygiene** (parses, imports, calls, genome_present, gene_ranges, universe,
determinism) can only ever vote *against*. A clean result abstains. "Imports
nothing dangerous" is a prerequisite, not a merit, and counting it for the
defence is how a strategy that lost money gets admitted on the strength of
having tidy imports — which is exactly what the first calibration of this
file did.

**Performance** (return, drawdown, sample_size, kill_criteria, costs) may
argue either way, because it is the only evidence about whether the thing
works.

Every juror may abstain, and an abstention is not a vote either way: a
strategy with no genome cannot be backtested, and the jurors who read a
backtest say so rather than scoring an absent number as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ...money import D, ZERO, percent
from ..config import TradingConfig
from ..firms.kill_switch import FirmMetrics, should_kill_firm
from .evidence import Evidence

FOR = "for"
AGAINST = "against"
ABSTAIN = "abstain"


@dataclass
class Finding:
    juror: str
    verdict: str
    weight: Decimal = ZERO
    reason: str = ""
    veto: bool = False

    @property
    def is_for(self) -> bool:
        return self.verdict == FOR

    @property
    def is_against(self) -> bool:
        return self.verdict == AGAINST

    @property
    def abstained(self) -> bool:
        return self.verdict == ABSTAIN

    def to_dict(self) -> dict:
        return {
            "juror": self.juror,
            "verdict": self.verdict,
            "weight": str(self.weight),
            "reason": self.reason,
            "veto": self.veto,
        }


def _for(juror: str, weight, reason: str) -> Finding:
    return Finding(juror, FOR, percent(weight), reason)


def _against(juror: str, weight, reason: str, veto: bool = False) -> Finding:
    return Finding(juror, AGAINST, percent(weight), reason, veto=veto)


def _abstain(juror: str, reason: str) -> Finding:
    return Finding(juror, ABSTAIN, ZERO, reason)


class Jury:
    """Twelve jurors over one file and one backtest."""

    JURORS = (
        "parses",
        "imports",
        "calls",
        "genome_present",
        "gene_ranges",
        "universe",
        "return",
        "drawdown",
        "sample_size",
        "kill_criteria",
        "costs",
        "determinism",
    )

    def __init__(self, config: Optional[TradingConfig] = None):
        self.config = config or TradingConfig()

    def deliberate(self, evidence: Evidence, result=None, replay=None) -> list:
        """``result`` is a BacktestResult; ``replay`` a second run of the same."""
        return [
            self._parses(evidence),
            self._imports(evidence),
            self._calls(evidence),
            self._genome_present(evidence),
            self._gene_ranges(evidence),
            self._universe(evidence),
            self._return(result),
            self._drawdown(result),
            self._sample_size(result),
            self._kill_criteria(result),
            self._costs(result),
            self._determinism(result, replay),
        ]

    # -- the three vetoes --------------------------------------------------
    def _parses(self, evidence: Evidence) -> Finding:
        if evidence.syntax_error:
            return _against(
                "parses", 100, f"the file does not parse: {evidence.syntax_error}", veto=True
            )
        return _abstain("parses", f"reads cleanly as {evidence.kind}")

    def _imports(self, evidence: Evidence) -> Finding:
        if evidence.dangerous_imports:
            return _against(
                "imports",
                100,
                "reaches outside the strategy: imports "
                + ", ".join(sorted(set(evidence.dangerous_imports))),
                veto=True,
            )
        return _abstain("imports", "imports nothing outside the strategy's business")

    def _calls(self, evidence: Evidence) -> Finding:
        if evidence.dangerous_calls:
            return _against(
                "calls",
                100,
                "evaluates code at runtime: " + ", ".join(sorted(set(evidence.dangerous_calls))),
                veto=True,
            )
        return _abstain("calls", "no eval/exec/open")

    # -- the readable file -------------------------------------------------
    def _genome_present(self, evidence: Evidence) -> Finding:
        if not evidence.has_genome:
            return _against(
                "genome_present",
                60,
                "no readable genome; the court cannot measure this without running it",
            )
        return _abstain("genome_present", f"{len(evidence.genome)} parameter(s) readable")

    def _gene_ranges(self, evidence: Evidence) -> Finding:
        if not evidence.has_genome:
            return _abstain("gene_ranges", "no genome to range-check")
        if evidence.out_of_range:
            return _against(
                "gene_ranges", 55, "parameters outside their ranges: "
                + "; ".join(evidence.out_of_range)
            )
        if evidence.unknown_genes:
            return _against(
                "gene_ranges",
                25,
                "unrecognised parameters (they will be ignored): "
                + ", ".join(sorted(evidence.unknown_genes)),
            )
        return _abstain("gene_ranges", "every parameter inside its range")

    def _universe(self, evidence: Evidence) -> Finding:
        if not evidence.universe:
            return _abstain("universe", "no universe declared; the firm's own would be used")
        return _abstain("universe", f"declares {len(evidence.universe)} symbol(s)")

    # -- the backtest ------------------------------------------------------
    def _return(self, result) -> Finding:
        if result is None:
            return _abstain("return", "not backtested")
        if result.return_pct > D(5):
            return _for("return", min(D(60), result.return_pct * D(3)),
                        f"returned {result.return_pct}% over the sample")
        if result.return_pct < ZERO:
            return _against("return", min(D(60), abs(result.return_pct) * D(3)),
                            f"lost {result.return_pct}% over the sample")
        return _for("return", 10, f"returned {result.return_pct}% — thin but positive")

    def _drawdown(self, result) -> Finding:
        if result is None:
            return _abstain("drawdown", "not backtested")
        limit = self.config.kill.max_drawdown_pct
        if result.max_drawdown_pct > limit:
            return _against(
                "drawdown",
                min(D(80), result.max_drawdown_pct * D(2)),
                f"max drawdown {result.max_drawdown_pct}% is past the firm kill limit ({limit}%)",
            )
        if result.max_drawdown_pct > limit / D(2):
            return _against("drawdown", 30,
                            f"max drawdown {result.max_drawdown_pct}% is over half the kill limit")
        return _for("drawdown", 30, f"max drawdown {result.max_drawdown_pct}%")

    def _sample_size(self, result) -> Finding:
        if result is None:
            return _abstain("sample_size", "not backtested")
        minimum = self.config.kill.minimum_trades
        if result.closed_trades < minimum:
            # Not an argument that the strategy is bad — an argument that
            # nobody yet knows, which is a reason to modify, not to reject.
            return _against(
                "sample_size",
                35,
                f"only {result.closed_trades} closed trades; below the {minimum}-trade gate "
                "nothing here is a verdict about the strategy",
            )
        return _for("sample_size", 30, f"{result.closed_trades} closed trades")

    def _kill_criteria(self, result) -> Finding:
        """Would this strategy have tripped the firm kill switch on its own data?"""
        if result is None:
            return _abstain("kill_criteria", "not backtested")
        metrics = FirmMetrics(
            trades=result.closed_trades,
            drawdown_pct=result.max_drawdown_pct,
            win_rate_pct=result.win_rate_pct,
            sharpe=result.sharpe,
        )
        should, reason = should_kill_firm(metrics, self.config.kill)
        if should:
            return _against("kill_criteria", 90,
                            f"would have been killed during its own backtest: {reason}")
        return _for("kill_criteria", 40, "survives the firm kill criteria on its own data")

    def _costs(self, result) -> Finding:
        """Does the edge survive the fees, or is it an artefact of ignoring them?"""
        if result is None:
            return _abstain("costs", "not backtested")
        gross = D(result.final_equity) - D(result.start_capital) + D(result.fees)
        if gross <= 0:
            return _against("costs", 40, "no gross profit before costs")
        share = percent(D(result.fees) / gross * D(100))
        if share > D(50):
            return _against(
                "costs", 45,
                f"fees consume {share}% of gross profit; the edge is mostly the broker's",
            )
        return _for("costs", 25, f"fees are {share}% of gross profit")

    def _determinism(self, result, replay) -> Finding:
        """Same file, same data, twice. A strategy that drifts cannot be audited."""
        if result is None or replay is None:
            return _abstain("determinism", "not replayed")
        if result.final_equity != replay.final_equity or result.trades != replay.trades:
            return _against(
                "determinism",
                70,
                f"two runs over identical data disagreed "
                f"({result.final_equity} vs {replay.final_equity}); this cannot be audited",
            )
        return _abstain("determinism", "two runs over identical data agreed exactly")


__all__ = ["ABSTAIN", "AGAINST", "FOR", "Finding", "Jury"]
