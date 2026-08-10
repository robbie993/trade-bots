"""Firm specifications — section 2.3 of the build document.

``config/firm_config.yaml`` is the file an operator edits to add a firm. It is
read with PyYAML when PyYAML is installed, and otherwise by the small strict
loader below, which understands the subset the file actually uses: nested
mappings, lists of scalars, comments, quoted strings, numbers and booleans.

That fallback exists for the same reason the rest of the core loop runs on the
standard library: adding a firm should not require a dependency, and a parse
failure here must be a clear error about a line of config rather than an
ImportError three layers down. The loader refuses anything it does not
understand rather than guessing — a silently mis-parsed risk limit is exactly
the kind of quiet wrongness this system is built to avoid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from ...money import D
from ..config import FirmDefaults, TradingConfig

DEFAULT_ANALYSTS = ("technical", "sentiment", "macro")


class FirmSpecError(ValueError):
    """The firm config could not be read, or is missing something required."""


def _strategy(raw: dict) -> str:
    """A firm's strategy label, or the bot it delegates to.

    `bot: bots/mine.py` means the village runs that file instead of its own
    analysts — see src/trading/adapter.py. It is stored in the same column as
    the free-text label because it is the same thing: what this firm does.
    """
    bot = str(raw.get("bot") or "").strip()
    if bot:
        return f"bot:{bot}"
    return str(raw.get("strategy") or "")


@dataclass
class FirmSpec:
    firm_key: str
    name: str = ""
    asset_class: str = ""
    strategy: str = ""
    venue: str = "paper"
    risk_limit: Decimal = D("0.02")
    allocation: Decimal = D("0")
    universe: tuple = ()
    analysts: tuple = DEFAULT_ANALYSTS
    genome: dict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, key: str, raw: dict, defaults: Optional[FirmDefaults] = None) -> "FirmSpec":
        limits = defaults or FirmDefaults()
        if not isinstance(raw, dict):
            raise FirmSpecError(f"firm {key!r} must be a mapping, got {type(raw).__name__}")

        universe = raw.get("universe") or raw.get("symbols") or ()
        if isinstance(universe, str):
            universe = [s.strip() for s in universe.split(",") if s.strip()]
        if not universe:
            raise FirmSpecError(f"firm {key!r} has no universe: list the symbols it may trade")

        analysts = raw.get("analysts") or list(DEFAULT_ANALYSTS)
        if isinstance(analysts, str):
            analysts = [a.strip() for a in analysts.split(",") if a.strip()]
        # The build document lists researchers and the trader alongside the
        # analysts. Every firm has those seats by construction, so they are
        # filtered out here rather than rejected as unknown analysts.
        seats = {"bullresearcher", "bearresearcher", "trader", "riskmanager", "bull", "bear"}
        analysts = [
            a
            for a in analysts
            if str(a).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
            not in seats
        ]

        genome = raw.get("genome") or {}
        if not isinstance(genome, dict):
            raise FirmSpecError(f"firm {key!r}: genome must be a mapping")

        return cls(
            firm_key=key,
            name=str(raw.get("name") or key),
            asset_class=str(raw.get("asset_class") or ""),
            strategy=_strategy(raw),
            venue=str(raw.get("venue") or limits.venue),
            risk_limit=D(raw.get("risk_limit", limits.risk_limit)),
            allocation=D(raw.get("capital_allocation", raw.get("allocation", limits.allocation))),
            universe=tuple(str(s).upper() for s in universe),
            analysts=tuple(str(a) for a in analysts),
            genome={str(k): v for k, v in genome.items()},
        )


def load_firm_specs(
    path: Optional[Path] = None, config: Optional[TradingConfig] = None
) -> list[FirmSpec]:
    cfg = config or TradingConfig()
    target = Path(path or cfg.firms_config)
    if not target.exists():
        raise FirmSpecError(
            f"no firm config at {target}. Copy config/firm_config.yaml.example, or set "
            "TRADE_FIRMS_CONFIG."
        )
    raw = parse_config(target)
    firms = raw.get("firms", raw)
    if not isinstance(firms, dict) or not firms:
        raise FirmSpecError(f"{target} defines no firms")
    return [FirmSpec.from_mapping(key, value, cfg.firm) for key, value in firms.items()]


def parse_config(path: Path) -> dict:
    """Read a YAML (or JSON) config file into plain Python."""
    text = Path(path).read_text()
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise FirmSpecError(f"{path} did not parse to a mapping")
        return loaded
    except ImportError:
        return parse_simple_yaml(text, source=str(path))


# =========================================================================
# the fallback parser
# =========================================================================
def parse_simple_yaml(text: str, source: str = "<config>") -> dict:
    """Parse the indentation-based subset used by this project's config files.

    Supported: nested mappings, `- item` lists of scalars, inline `[a, b]`
    lists, comments, quoted and bare scalars, ints, decimals, booleans, null.
    Anything else raises, because a config parser that guesses is worse than
    no config parser at all.
    """
    lines: list[tuple[int, int, str]] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split(" #", 1)[0] if " #" in raw_line else raw_line
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent] or "\t" in line:
            raise FirmSpecError(f"{source}:{lineno}: tabs are not valid indentation in YAML")
        lines.append((indent, lineno, line.strip()))

    cursor = [0]

    def parse_block(indent: int):
        """Consume every line at `indent`, returning a dict or a list."""
        if cursor[0] >= len(lines):
            return {}
        is_list = lines[cursor[0]][2].startswith("- ")
        block: Any = [] if is_list else {}

        while cursor[0] < len(lines):
            line_indent, lineno, content = lines[cursor[0]]
            if line_indent < indent:
                break
            if line_indent > indent:
                raise FirmSpecError(f"{source}:{lineno}: unexpected indentation")

            if content.startswith("- "):
                if not is_list:
                    raise FirmSpecError(f"{source}:{lineno}: list item inside a mapping")
                block.append(_scalar(content[2:]))
                cursor[0] += 1
                continue
            if is_list:
                raise FirmSpecError(f"{source}:{lineno}: mapping key inside a list")
            if ":" not in content:
                raise FirmSpecError(f"{source}:{lineno}: expected `key: value`, got {content!r}")

            key, _, value = content.partition(":")
            key, value = key.strip().strip("\"'"), value.strip()
            cursor[0] += 1
            if value:
                block[key] = _scalar(value)
                continue
            # Empty value: a nested block if the next line is deeper, else null.
            if cursor[0] < len(lines) and lines[cursor[0]][0] > indent:
                block[key] = parse_block(lines[cursor[0]][0])
            else:
                block[key] = None
        return block

    parsed = parse_block(lines[0][0] if lines else 0)
    if not isinstance(parsed, dict):
        raise FirmSpecError(f"{source}: top level must be a mapping, not a list")
    return parsed


def _scalar(raw: str) -> Any:
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~", ""):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value) if "e" in lowered else str(D(value))
    except Exception:
        return value


__all__ = ["DEFAULT_ANALYSTS", "FirmSpec", "FirmSpecError", "load_firm_specs", "parse_config"]
