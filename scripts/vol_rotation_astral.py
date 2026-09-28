"""Write ASTRAL.md for the rotation bots from their own parameters.

One file per folder: bots/vol_rotation/ (the ten groups) and
bots/vol_rotation_tp/ (ten take-profit iterations of the Treasuries bot).

Astral (heyastral.ai) builds a strategy from a plain-English description, so
each bot becomes one self-contained prompt with every number spelled out.
Generated rather than hand-written, so the prompts cannot drift from the code
they describe:

    python scripts/vol_rotation_astral.py
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.trading import vol_rotation  # noqa: E402

HEADINGS = {
    "vol_rotation": "The ten bots, as Astral prompts",
    "vol_rotation_tp": "The ten take-profit iterations, as Astral prompts",
}

FOLDERS = {
    REPO / "bots" / "vol_rotation": "Vol Rotation",
    REPO / "bots" / "vol_rotation_tp": "Treasuries TP",
}


def _module(path: Path):
    """Import one of the bots. They are this repository's own files."""
    spec = importlib.util.spec_from_file_location(f"vol_rotation_bot_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _title(path: Path) -> str:
    doc = ast.get_docstring(ast.parse(path.read_text())) or ""
    return doc.splitlines()[0].split(":", 1)[-1].strip().rstrip(".")


def _about(path: Path) -> str:
    doc = ast.get_docstring(ast.parse(path.read_text())) or ""
    return " ".join(doc.split("\n\n")[1].split())


def take_profit_rules(p: vol_rotation.Params) -> str:
    """The take-profit section of a prompt, or nothing when there is none."""
    if not p.take_profit:
        return ""
    atr = f"ATR = average true range over the last {p.atr_window} bars."
    trail = (f"trailing stop = highest high of the last {p.tp_lookback} bars minus "
             f"{p.tp_trail_atr:g} x ATR")
    before = ("measured over the bars up to the last close at or below the average "
              "entry price (the bars before the current run up), not over the rally itself")
    rule = {
        "atr_target": f"Sell the whole position when close >= average entry + {p.tp_atr:g} x ATR.",
        "vol_target": (
            f"Sell the whole position when close >= average entry x exp({p.tp_sigma:g} x "
            f"per-bar volatility x sqrt({p.tp_horizon})), per-bar volatility being the "
            "short volatility above."),
        "r_multiple": (
            f"Swing low = lowest low of the last {p.tp_lookback} bars, measured over the "
            "bars before the most recent run above the average entry price, anchored to the "
            "last bar that closed above entry, so the stop does not move down as price falls. "
            f"Risk (1R) = max(average entry - swing low, {p.tp_stop_atr:g} x ATR); if no bar "
            f"has closed above entry yet, 1R = {p.tp_stop_atr:g} x ATR. Sell the whole "
            f"position when close >= average entry + {p.tp_r:g}R. Stop: sell the whole "
            "position when close <= average entry - 1R."),
        "swing_high": (
            f"Resistance = highest high of the last {p.tp_lookback} bars, {before}. Sell "
            f"the whole position when close >= max(resistance, average entry) + "
            f"{p.tp_atr:g} x ATR."),
        "measured_move": (
            f"Range = highest high minus lowest low of the last {p.tp_lookback} bars, "
            f"{before}. Sell the whole position when close >= average entry + "
            f"{p.tp_mult:g} x range."),
        "vwap_band": (
            f"Band = VWAP of the last {p.tp_lookback} bars + {p.tp_sigma:g} x the standard "
            "deviation of their closes around that VWAP. Sell the whole position when "
            "close >= max(band, average entry + 2 x ATR)."),
        "chandelier": (
            f"No fixed target. Once close >= average entry + {p.tp_arm_atr:g} x ATR, sell "
            f"the whole position when close < {trail}."),
        "ladder": (
            f"Risk (1R) = {p.tp_stop_atr:g} x ATR. At close >= average entry + "
            f"{p.tp_r / 2:g}R, cut the position to 2/3 of a full position. At close >= "
            f"average entry + {p.tp_r:g}R, cut it to 1/3, and sell that last third when "
            f"close < {trail}. Once the first rung is reached, stop scaling in."),
        "volume_climax": (
            f"Once close >= average entry + {p.tp_arm_atr:g} x ATR: sell the whole position "
            f"on an up bar with RVOL >= {p.tp_climax_rvol:g} (a volume climax), or when "
            f"close < {trail}."),
        "fib_extension": (
            f"Swing low = lowest low of the last {p.tp_lookback} bars. Leg = average entry "
            f"- swing low (at least 1 ATR). Sell the whole position when close >= swing "
            f"low + {p.tp_mult:g} x leg."),
    }[p.take_profit]
    return f"""

Take profit (this overrides the scaling-out rules above):
- {atr}
- {rule}
- While close > average entry price, ignore every scaling-out rule above (rotation, distribution, negative score, vol spike, trim). Only this take profit closes a winning position.
- When close falls back to or below the average entry price, all the scaling-out rules above apply again.
- A winning position held after it stops being a target still counts toward the number of tickers held, so do not open a new ticker in its place until it is closed."""


def prompt(title: str, universe: list, p: vol_rotation.Params, prefix: str = "Vol Rotation") -> str:
    crypto = p.bars_per_year == vol_rotation.CRYPTO_15M_BARS_PER_YEAR
    session = ("24/7, every 15-minute bar" if crypto
               else "regular US market hours only (9:30-16:00 ET), 15-minute bars")
    held = "the single best-ranked ticker" if p.hold == 1 else f"the top {p.hold} ranked tickers"
    tickers = ", ".join(universe)
    return f"""Build a volatility rotation strategy called "{prefix}: {title}".

Universe: {tickers}. Long only, no leverage, no shorting. Timeframe: {session}.
Evaluate on each bar close.

Indicators, per ticker:
- Short volatility = standard deviation of 1-bar log returns over the last {p.vol_window} bars.
- Long volatility = standard deviation of 1-bar log returns over the last {p.long_vol_window} bars.
- Vol ratio = short volatility / long volatility.
- Annualised volatility = short volatility x sqrt({p.bars_per_year}).
- Momentum = ln(close / close {p.mom_window} bars ago).
- Score = momentum / (short volatility x sqrt({p.mom_window})).
- Relative volume (RVOL) = this bar's volume / average volume of the previous {p.volume_window - 1} bars.
- VWAP = volume-weighted average close over the last {p.volume_window} bars.
- Up bar = close > open. Down bar = close < open.

Ranking and rotation:
- Eligible = score > {p.min_score} AND vol ratio <= {p.max_vol_ratio}.
- Rank eligible tickers by score, adding {p.switch_margin} to the score of any ticker currently held (so a challenger must clearly beat a holding to replace it).
- Targets = {held}.

Position sizing:
- Full position per ticker = {p.max_weight:.0%} of equity x min(1, {p.target_vol:.0%} / annualised volatility).
- A full position is split into {p.tranches} equal tranches.

Scaling in (targets only), on a bar where ALL are true: up bar, close > VWAP, RVOL >= {p.rvol_in}:
- Buy 1 tranche, or 2 tranches if RVOL >= {p.rvol_surge}, never exceeding the full position.
- A quiet up bar (RVOL < {p.rvol_in}) adds nothing.

Scaling out:
- Exit the whole position immediately if score < 0 or vol ratio > {p.max_vol_ratio}.
- A held ticker that is no longer a target: sell 1 tranche per bar; 2 tranches if RVOL >= {p.rvol_out}; everything if RVOL >= {p.rvol_surge} on a down bar.
- A held target that prints a down bar below VWAP with RVOL >= {p.rvol_out} (distribution): sell 1 tranche.
- If a holding is worth more than 110% of its full position (volatility rose), trim it back to the full position.
- If a sale would leave less than a quarter of a tranche, sell the whole position.

Process sells before buys on each bar. Backtest with commissions and slippage on.{take_profit_rules(p)}"""


def write(folder: Path, prefix: str) -> int:
    sections = []
    for path in sorted(folder.glob("*.py")):
        bot = _module(path)
        universe = bot.UNIVERSE
        params = vol_rotation.Params.from_mapping(bot.PARAMS)
        title = _title(path)
        sections.append(
            f"## {''.join(c for c in path.stem if c.isdigit())[:2]}. {title}\n\n{_about(path)}\n\n"
            f"Repo version: `{path.relative_to(REPO)}`\n\n"
            "```text\n" + prompt(title, universe, params, prefix) + "\n```\n"
        )
    header = f"""# {HEADINGS[folder.name]}

Paste one block at a time into Astral's strategy builder (heyastral.ai). Each
block is self-contained and describes exactly what the matching file in this
folder does, with every number the code uses, so a backtest there and a
backtest here are tests of the same strategy.

Generated by `python scripts/vol_rotation_astral.py` from the bots' own
parameters. Change a bot, rerun the script, and this file follows. Do not edit
it by hand.

**Check what Astral built before you trust its backtest.** It writes the
strategy from these words, and an AI builder can quietly change a rule, for
example by dropping the volume gate or reading "tranche" loosely. Its editor
shows the rules it wrote, so check them against the block you pasted.

"""
    out = folder / "ASTRAL.md"
    out.write_text(header + "\n".join(sections))
    print(f"wrote {out.relative_to(REPO)} ({len(sections)} strategies)")
    return 0


def main() -> int:
    for folder, prefix in FOLDERS.items():
        write(folder, prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
