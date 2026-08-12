"""Importing bots that were never written for this village.

``trade recruit`` needs a file that already speaks the dialect: a literal
``GENOME`` dict and a ``UNIVERSE`` list. A bot you wrote last year does not
have those. It has ``FAST_MA = 12``, ``TICKERS = [...]``, and its edge in a
function called ``should_buy``.

This reads such a file and produces one the village can take, and — more
importantly — tells you exactly what did *not* come across.

**It never runs your code.** Same rule as the court: parsed with ``ast``, read
as a tree, never imported. That is also why it can only see literals. A
parameter computed at import time is invisible here, and the report says so
rather than guessing.

**What carries over is configuration, not logic.** A firm in this village is a
genome — the numbers the built-in analysts read — so a bot whose edge lives in
its own functions does not arrive intact. The report is blunt about this,
because an importer that quietly emitted a plausible genome and let you believe
your strategy had been ported would be worse than one that refuses.

**Nothing is silently invented.** A gene the file does not mention is left at
the village default and listed under ``defaulted``. A name matched by alias
rather than exactly is listed under ``guessed``. You are meant to read the
report before dropping the result in ``bots/``.

**Secrets are never copied.** If the file looks like it holds an API key, the
importer says so and writes nothing containing it.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..money import D
from .brain.evolver import BASE_GENOME, GENES

# gene -> the names other people's bots actually use for it. Matched
# case-insensitively after stripping underscores, so FAST_MA, fastMa and
# fast_ma are one name.
ALIASES: dict = {
    "fast_window": (
        "fast", "fastwindow", "fastperiod", "fastma", "fastema", "fastlen",
        "fastlength", "shortwindow", "shortperiod", "shortma", "emafast",
        "smafast", "fastsma", "shortlen",
    ),
    "slow_window": (
        "slow", "slowwindow", "slowperiod", "slowma", "slowema", "slowlen",
        "slowlength", "longwindow", "longperiod", "longma", "emaslow",
        "smaslow", "slowsma", "longlen",
    ),
    "rsi_window": (
        "rsi", "rsiwindow", "rsiperiod", "rsilength", "rsilen", "rsibars",
    ),
    "trend_bias": (
        "trendbias", "bias", "trend", "momentumbias", "trendweight",
        "momentumweight", "trendstrength",
    ),
    "value_window": (
        "valuewindow", "lookback", "lookbackperiod", "lookbackwindow",
        "valueperiod", "meanwindow", "mawindow", "baselinewindow", "window",
    ),
    "fair_band_pct": (
        "fairbandpct", "band", "bandpct", "thresholdpct", "deviationpct",
        "entrythreshold", "devpct", "spreadpct", "tolerance",
    ),
    "calm_vol_pct": (
        "calmvolpct", "vol", "volatility", "volpct", "atrpct",
        "volatilitythreshold", "volthreshold", "atrmultiplier",
    ),
}

# Where other people keep their symbol list.
SYMBOL_NAMES = (
    "universe", "symbols", "tickers", "watchlist", "pairs", "coins", "assets",
    "instruments", "symbollist", "tradinglist", "markets",
)

# Things the village does not do. Worth naming in the report rather than
# letting somebody find out from an empty leaderboard three days later.
#
# Every word here has to be unambiguous as a *substring of a parameter name*,
# which is what rules out the obvious ones. Bare "short" matches `short_window`
# — a moving-average length, not a direction — and bare "tick" matches
# `TICKERS`. Both produced confident, wrong warnings, which is worse than
# staying quiet: a false "this bot shorts" on a plain crossover strategy
# teaches you to ignore the section.
UNSUPPORTED = {
    "options": ("strike", "expiry", "expiration", "greek", "theta", "vega",
                "impliedvol", "premium", "optionchain", "contractsize"),
    "shorting": ("sellshort", "allowshort", "shortside", "shortentry",
                 "goshort", "enableshort", "shortselling", "canshort"),
    "leverage": ("leverage", "borrow", "notionalmultiplier"),
    "intraday": ("intraday", "timeframe", "minutebar", "secondbar",
                 "barinterval"),
}

SECRET = re.compile(
    r"(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|private[_-]?key"
    r"|passphrase|password|bearer)", re.I
)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


@dataclass
class ImportReport:
    path: Path
    name: str = ""
    genome: dict = field(default_factory=dict)
    universe: tuple = ()
    mapped: dict = field(default_factory=dict)      # gene -> source name
    guessed: dict = field(default_factory=dict)     # gene -> source name (alias)
    defaulted: list = field(default_factory=list)   # genes the file never mentions
    out_of_range: dict = field(default_factory=dict)
    ignored: list = field(default_factory=list)     # literals that meant nothing here
    logic: list = field(default_factory=list)       # functions/classes not carried
    unsupported: dict = field(default_factory=dict) # feature -> the words that hinted
    renamed: dict = field(default_factory=dict)     # symbol as written -> as the feed spells it
    secrets: bool = False
    error: str = ""

    @property
    def usable(self) -> bool:
        return not self.error and not self.secrets and bool(self.universe)

    def summary(self) -> str:
        if self.error:
            return f"{self.path.name}: cannot be read — {self.error}"
        lines = [f"{self.path.name} -> {self.name}"]
        if self.secrets:
            lines.append("  ⚠ looks like it holds a credential. Nothing was written.")
            return "\n".join(lines)
        lines.append(f"  symbols   : {', '.join(self.universe) or '(none found)'}")
        if self.renamed:
            lines.append(
                "  respelled : "
                + ", ".join(f"{was} -> {now}" for was, now in sorted(self.renamed.items()))
                + "   (the feed's spelling)"
            )
        for gene, source in sorted(self.mapped.items()):
            lines.append(f"  mapped    : {gene:15} <- {source}")
        for gene, source in sorted(self.guessed.items()):
            lines.append(f"  guessed   : {gene:15} <- {source}   (alias — check it)")
        if self.defaulted:
            lines.append(f"  defaulted : {', '.join(sorted(self.defaulted))}")
        for gene, note in sorted(self.out_of_range.items()):
            lines.append(f"  clamped   : {gene:15} {note}")
        if self.logic:
            lines.append(
                f"  NOT CARRIED: {len(self.logic)} function(s)/class(es) — "
                + ", ".join(self.logic[:6])
            )
        for feature, words in sorted(self.unsupported.items()):
            lines.append(f"  UNSUPPORTED: {feature} ({', '.join(sorted(words)[:4])})")
        if self.ignored:
            lines.append(f"  ignored   : {', '.join(self.ignored[:8])}")
        return "\n".join(lines)


def scan(path) -> ImportReport:
    """Read a foreign bot and work out what the village can take from it."""
    path = Path(str(path))
    report = ImportReport(path=path, name=_firm_name(path))
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        report.error = str(exc)
        return report

    if SECRET.search(text):
        report.secrets = True
        return report

    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            values = _from_python(text, report)
        elif suffix in (".yaml", ".yml"):
            from .firms.spec import parse_config

            values = _flatten(parse_config(path))
        elif suffix == ".json":
            values = _flatten(json.loads(text))
        else:
            report.error = f"the importer reads .py, .yaml and .json, not {suffix}"
            return report
    except Exception as exc:  # noqa: BLE001 - a bad file is a report, not a crash
        report.error = str(exc)
        return report

    _find_symbols(values, report)
    _map_genes(values, report)
    _flag_unsupported(text, values, report)
    return report


# Exchanges spell the same pair differently and none of them is wrong. The
# village's feed uses BTC-USD; a bot written against a crypto exchange API
# almost always says BTC/USD. Left alone the symbol resolves to nothing and the
# firm arrives holding an empty universe with no explanation — a failure that
# shows up days later as a leaderboard row that never trades.
SEPARATORS = ("/", ":", "_")


def _feed_symbol(symbol: str, report=None) -> str:
    """Spell a symbol the way the market feed does, and say so when it changed."""
    out = symbol.strip()
    for sep in SEPARATORS:
        out = out.replace(sep, "-")
    # BINANCE-BTC-USD and the like: keep the pair, drop the venue prefix.
    parts = [p for p in out.split("-") if p]
    if len(parts) > 2:
        parts = parts[-2:]
    if len(parts) == 2 and parts[1] in ("USDT", "USDC"):
        parts[1] = "USD"
    out = "-".join(parts)
    if out != symbol and report is not None:
        report.renamed[symbol] = out
    return out


def _firm_name(path: Path) -> str:
    from .recruit import firm_key_for

    return firm_key_for(path.name)


def _from_python(text: str, report: ImportReport) -> dict:
    """Module-level literals, plus a note of the logic that is being left behind."""
    tree = ast.parse(text)
    values: dict = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            report.logic.append(node.name)
            continue
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            try:
                values[target.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                report.ignored.append(f"{target.id} (computed, not a literal)")
    # A dict of parameters is as common as loose constants.
    for key, value in list(values.items()):
        if isinstance(value, dict) and _norm(key) in (
            "params", "config", "settings", "genome", "parameters", "strategy"
        ):
            for inner, number in value.items():
                values.setdefault(str(inner), number)
    return values


def _flatten(raw) -> dict:
    """One level of nesting is enough for the shapes people actually write."""
    if not isinstance(raw, dict):
        raise ValueError("does not parse to a mapping")
    out: dict = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            for inner, number in value.items():
                out.setdefault(str(inner), number)
        else:
            out[str(key)] = value
    return out


def _find_symbols(values: dict, report: ImportReport) -> None:
    for key, value in values.items():
        if _norm(key) not in SYMBOL_NAMES:
            continue
        if isinstance(value, str):
            value = [s.strip() for s in value.split(",") if s.strip()]
        if isinstance(value, (list, tuple)) and value:
            report.universe = tuple(
                _feed_symbol(str(s).upper(), report)
                for s in value if isinstance(s, (str, int))
            )
            return


def _map_genes(values: dict, report: ImportReport) -> None:
    by_norm = {_norm(k): (k, v) for k, v in values.items()}
    genome = dict(BASE_GENOME)

    for gene in BASE_GENOME:
        hit = by_norm.get(_norm(gene))
        bucket = report.mapped
        if hit is None:
            for alias in ALIASES.get(gene, ()):
                hit = by_norm.get(alias)
                if hit is not None:
                    bucket = report.guessed
                    break
        if hit is None:
            report.defaulted.append(gene)
            continue

        source, raw = hit
        try:
            number = D(str(raw))
        except Exception:  # noqa: BLE001
            report.ignored.append(f"{source} (not a number)")
            report.defaulted.append(gene)
            continue

        low, high, is_int = GENES[gene]
        clamped = max(low, min(high, number))
        if clamped != number:
            report.out_of_range[gene] = f"{number} -> {clamped} (range {low}..{high})"
        genome[gene] = int(clamped) if is_int else str(clamped.quantize(D("0.01")))
        bucket[gene] = source

    report.genome = genome

    used = {_norm(v) for v in {**report.mapped, **report.guessed}.values()}
    for key, value in values.items():
        if _norm(key) in used or _norm(key) in SYMBOL_NAMES:
            continue
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            report.ignored.append(str(key))


def _flag_unsupported(text: str, values: dict, report: ImportReport) -> None:
    """Matched against parameter *names*, not the whole file.

    Scanning the raw text found "short" inside `short_window` and "tick" inside
    `TICKERS`, and reported both as unsupported features. Names are the thing
    that carries the intent.
    """
    names = [_norm(k) for k in values]
    names += [_norm(f) for f in report.logic]
    for feature, words in UNSUPPORTED.items():
        hits = {w for w in words if any(w in name for name in names)}
        if hits:
            report.unsupported[feature] = hits


def to_village(report: ImportReport) -> str:
    """The village-format file, with its provenance written into it."""
    lines = [
        f'"""{report.name} — imported from {report.path.name}.',
        "",
        "Generated by `trade import`. Read this before recruiting it: what came",
        "across is configuration, not logic.",
        "",
    ]
    for gene, source in sorted(report.mapped.items()):
        lines.append(f"  {gene:15} <- {source}")
    for gene, source in sorted(report.guessed.items()):
        lines.append(f"  {gene:15} <- {source}  (matched by alias — check it)")
    if report.defaulted:
        lines.append(f"  village defaults: {', '.join(sorted(report.defaulted))}")
    if report.logic:
        lines += [
            "",
            f"  {len(report.logic)} function(s)/class(es) did NOT come across:",
            f"  {', '.join(report.logic[:12])}",
            "  The village runs its own analysts over these parameters. If the",
            "  edge lived in that code, it is not here.",
        ]
    for feature, words in sorted(report.unsupported.items()):
        lines.append(f"  UNSUPPORTED: {feature} — {', '.join(sorted(words)[:4])}")
    lines += ['"""', "", "GENOME = {"]
    for gene, value in report.genome.items():
        rendered = value if isinstance(value, int) else f'"{value}"'
        lines.append(f'    "{gene}": {rendered},')
    lines.append("}")
    lines.append("")
    lines.append(f"UNIVERSE = {list(report.universe)!r}")
    lines.append("")
    return "\n".join(lines)


def write(report: ImportReport, out_dir="bots") -> Optional[Path]:
    if not report.usable:
        return None
    out_dir = Path(str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{report.name}.py"
    target.write_text(to_village(report))
    return target


def scan_all(directory) -> list:
    directory = Path(str(directory))
    if not directory.exists():
        raise FileNotFoundError(f"{directory} does not exist")
    return [
        scan(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix.lower() in (".py", ".yaml", ".yml", ".json")
    ]


__all__ = [
    "ALIASES",
    "ImportReport",
    "scan",
    "scan_all",
    "to_village",
    "write",
]
