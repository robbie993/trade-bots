"""Forensics — what is actually in the file somebody dropped in.

Reading, never executing. A submitted strategy is parsed with ``ast`` and
``json``/YAML; nothing in this package imports the file, calls it, or evaluates
a line of it. That is not caution about crashes — a strategy file is code an
outsider wrote, arriving at a system that moves money, and "run it and see"
would hand it the process before the court had said anything.

Two things come out: the genome (the parameters the analysts read) and a
safety reading (what the file would do if it ever were imported). Both are
attached to the case row, so a ruling stays checkable against the file's hash
long after the file itself is gone.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ...money import D
from ..brain.evolver import BASE_GENOME, GENES

# Modules a strategy has no business touching. A strategy is a set of numbers
# and a rule over prices; anything here means it wants to reach the network,
# the filesystem or the process, and the court says so out loud.
DANGEROUS_IMPORTS = {
    "socket", "subprocess", "os", "sys", "shutil", "requests", "urllib",
    "http", "ftplib", "smtplib", "telnetlib", "pickle", "marshal", "ctypes",
    "importlib", "multiprocessing", "webbrowser",
}
DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}

MAX_BYTES = 512 * 1024


class EvidenceError(ValueError):
    """The file cannot be read as a strategy at all."""


@dataclass
class Evidence:
    path: Path
    name: str = ""
    sha256: str = ""
    size: int = 0
    kind: str = ""                       # python | yaml | json
    genome: dict = field(default_factory=dict)
    universe: tuple = ()
    declared: dict = field(default_factory=dict)
    dangerous_imports: list = field(default_factory=list)
    dangerous_calls: list = field(default_factory=list)
    unknown_genes: list = field(default_factory=list)
    out_of_range: list = field(default_factory=list)
    syntax_error: Optional[str] = None
    notes: list = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return not self.dangerous_imports and not self.dangerous_calls

    @property
    def has_genome(self) -> bool:
        return bool(self.genome)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "size": self.size,
            "kind": self.kind,
            "genome": self.genome,
            "universe": list(self.universe),
            "declared": self.declared,
            "dangerous_imports": self.dangerous_imports,
            "dangerous_calls": self.dangerous_calls,
            "unknown_genes": self.unknown_genes,
            "out_of_range": self.out_of_range,
            "syntax_error": self.syntax_error,
            "notes": self.notes,
        }


def sha256_of(path: Path, chunk: int = 65536) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def gather(path) -> Evidence:
    """Read a submitted file into evidence. Never imports or executes it."""
    target = Path(path)
    if not target.exists() or not target.is_file():
        raise EvidenceError(f"no such file: {target}")
    size = target.stat().st_size
    if size > MAX_BYTES:
        raise EvidenceError(f"{target.name} is {size} bytes; the court reads at most {MAX_BYTES}")
    if size == 0:
        raise EvidenceError(f"{target.name} is empty")

    evidence = Evidence(
        path=target, name=target.name, sha256=sha256_of(target), size=size
    )
    suffix = target.suffix.lower()
    text = target.read_text(errors="replace")

    if suffix == ".py":
        evidence.kind = "python"
        _read_python(text, evidence)
    elif suffix in (".yaml", ".yml"):
        evidence.kind = "yaml"
        _read_mapping(_parse_yaml(target), evidence)
    elif suffix == ".json":
        evidence.kind = "json"
        try:
            _read_mapping(json.loads(text), evidence)
        except ValueError as exc:
            raise EvidenceError(f"{target.name} is not valid JSON: {exc}") from exc
    else:
        raise EvidenceError(
            f"{target.name}: the court reads .py, .yaml and .json strategies"
        )

    _check_genes(evidence)
    return evidence


def _parse_yaml(path: Path) -> dict:
    from ..firms.spec import parse_config

    return parse_config(path)


def _read_mapping(raw, evidence: Evidence) -> None:
    """A declarative strategy: the genome is simply in the file."""
    if not isinstance(raw, dict):
        raise EvidenceError(f"{evidence.name} does not parse to a mapping")
    # `strategy:` is allowed to be either a nested block holding the genome, or
    # a plain name — which is how config/firm_config.yaml uses it. Descending
    # into a string produced an AttributeError from inside the parser instead
    # of a readable refusal, so the type is checked rather than assumed.
    nested = raw.get("strategy")
    body = nested if isinstance(nested, dict) else raw
    genome = body.get("genome", {})
    if not isinstance(genome, dict):
        raise EvidenceError(f"{evidence.name}: `genome` must be a mapping")
    evidence.genome = {str(k): v for k, v in genome.items()}
    universe = body.get("universe") or body.get("symbols") or ()
    if isinstance(universe, str):
        universe = [s.strip() for s in universe.split(",") if s.strip()]
    evidence.universe = tuple(str(s).upper() for s in universe)
    evidence.declared = {
        k: v for k, v in body.items() if k not in ("genome", "universe", "symbols")
    }
    if not evidence.genome:
        evidence.notes.append("no genome block found; the court has nothing to backtest")


def _read_python(text: str, evidence: Evidence) -> None:
    """A Python strategy: read the tree, never the runtime.

    The genome is taken from a module-level ``GENOME`` dict of literals. A
    strategy that computes its parameters at import time is not rejected for
    it, but the court cannot see those numbers and says so — an unreadable
    parameter is not a safe one.
    """
    try:
        tree = ast.parse(text, filename=evidence.name)
    except SyntaxError as exc:
        evidence.syntax_error = f"line {exc.lineno}: {exc.msg}"
        evidence.notes.append("file does not parse; no genome could be read")
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in DANGEROUS_IMPORTS:
                    evidence.dangerous_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in DANGEROUS_IMPORTS:
                evidence.dangerous_imports.append(node.module or "?")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in DANGEROUS_CALLS:
                evidence.dangerous_calls.append(node.func.id)

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id.upper() == "GENOME":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    evidence.notes.append(
                        "GENOME is computed rather than literal; the court cannot read it "
                        "without executing the file, which it will not do"
                    )
                    continue
                if isinstance(value, dict):
                    evidence.genome = {str(k): v for k, v in value.items()}
            elif target.id.upper() in ("UNIVERSE", "SYMBOLS"):
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, (list, tuple)):
                    evidence.universe = tuple(str(s).upper() for s in value)

    if not evidence.genome and not evidence.syntax_error:
        evidence.notes.append(
            "no module-level GENOME dict found; the court has nothing to backtest"
        )


def _check_genes(evidence: Evidence) -> None:
    """Which parameters are unrecognised, and which are outside their range."""
    for name, value in evidence.genome.items():
        if name not in GENES:
            evidence.unknown_genes.append(name)
            continue
        low, high, _ = GENES[name]
        try:
            number = D(value)
        except Exception:
            evidence.out_of_range.append(f"{name}={value!r} is not a number")
            continue
        if number < low or number > high:
            evidence.out_of_range.append(f"{name}={number} outside {low}..{high}")
    if evidence.genome:
        missing = [g for g in BASE_GENOME if g not in evidence.genome]
        if missing:
            evidence.notes.append(
                f"defaults will be used for: {', '.join(sorted(missing))}"
            )


__all__ = [
    "DANGEROUS_CALLS",
    "DANGEROUS_IMPORTS",
    "Evidence",
    "EvidenceError",
    "gather",
    "sha256_of",
]
