"""Bridges to the external frameworks named in the build document.

Every framework in the plan is optional here, and none of them is in the
critical path. The ecosystem is complete and correct with none of them
installed; each adapter adds a capability *alongside* the native one and is
skipped when the package or checkout is absent.

That is a deliberate inversion of the build plan's week 1-5 ordering, for one
reason: a trading system whose kill switch lives in someone else's repository
is a trading system whose kill switch can be deleted by someone else. The
refusals — kill criteria, risk limits, reconciliation, the human gate — are
implemented in this repository and stay here.

Reachability at the time of writing (checked with `git ls-remote`):

    LLMQuant/Magents                        exists
    TauricResearch/TradingAgents            exists
    KCNyu/clawock                           exists
    OpenSourceAGI/ai-broker-investing-agent exists
    cubexch/ai-fund                         exists
    ruvnet/ruflo                            exists
    EvoMap/evolver-claude-code-plugin       exists
    JaceHo/AgentMem                         exists
    diegosouzapw/OmniRoute                  exists
    Drakkar-Software/OctoBot-AI             exists
    lex-conscience/lex-conscience           NOT REACHABLE — the heart is
                                            implemented natively instead
                                            (see heart/conscience.py)

``scripts/clone_all.sh`` clones the reachable ones into ``vendor/``. Nothing
is cloned automatically: pulling and running third-party code that places
orders is a decision an operator makes explicitly.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import TradingConfig


@dataclass
class AdapterStatus:
    name: str
    kind: str            # "pip" | "repo" | "plugin"
    target: str
    installed: bool
    detail: str = ""
    role: str = ""

    def __str__(self) -> str:
        mark = "✓" if self.installed else "·"
        return f"{mark} {self.name:<24} {self.kind:<7} {self.role}" + (
            f"  [{self.detail}]" if self.detail else ""
        )


# name -> (kind, target, role)
FRAMEWORKS: dict = {
    "Magents": ("repo", "LLMQuant/Magents", "multi-strategy pods"),
    "TradingAgents": ("repo", "TauricResearch/TradingAgents", "bull/bear debate"),
    "OctoBot-AI": ("repo", "Drakkar-Software/OctoBot-AI", "crypto execution"),
    "clawock": ("repo", "KCNyu/clawock", "self-grading investment desk"),
    "ai-broker": ("repo", "OpenSourceAGI/ai-broker-investing-agent", "leaderboard + allocation"),
    "ai-fund": ("repo", "cubexch/ai-fund", "42 hedge fund agents"),
    "OmniRoute": ("repo", "diegosouzapw/OmniRoute", "LLM gateway"),
    "AgentMem": ("pip", "agentmem", "persistent memory"),
    "lex-conscience": ("pip", "lex_conscience", "ethics (repo unreachable; native heart used)"),
    "evolver": ("plugin", "EvoMap/evolver-claude-code-plugin", "genome evolution"),
    "ruflo": ("plugin", "ruvnet/ruflo", "98-agent orchestration"),
}


def module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def vendor_path(name: str, config: Optional[TradingConfig] = None) -> Path:
    cfg = config or TradingConfig()
    return cfg.vendor_dir / name


def survey(config: Optional[TradingConfig] = None) -> list:
    """What is actually installed here, right now."""
    cfg = config or TradingConfig()
    out = []
    for name, (kind, target, role) in FRAMEWORKS.items():
        if kind == "pip":
            installed = module_available(target)
            detail = target if installed else f"pip install {target.replace('_', '-')}"
        elif kind == "repo":
            path = vendor_path(name, cfg)
            installed = path.exists() and any(path.iterdir()) if path.exists() else False
            detail = str(path) if installed else f"scripts/clone_all.sh ({target})"
        else:
            installed = False
            detail = f"/plugin install (see {target})"
        out.append(AdapterStatus(name, kind, target, installed, detail, role))
    return out


def render_survey(config: Optional[TradingConfig] = None) -> str:
    rows = survey(config)
    present = sum(1 for r in rows if r.installed)
    lines = [
        f"external frameworks: {present}/{len(rows)} present "
        "(all optional — the ecosystem runs without any of them)",
        "",
    ]
    lines += [str(r) for r in rows]
    lines += [
        "",
        "Clone the repository-based ones with ./scripts/clone_all.sh, then",
        "./scripts/install_all.sh to install their requirements into a venv.",
        "The Claude Code plugins are installed from inside Claude Code:",
        "  /plugin marketplace add EvoMap/evolver-claude-code-plugin",
        "  /plugin install evolver@evolver",
        "  /plugin marketplace add ruvnet/ruflo",
        "  /plugin install ruflo-core@ruflo",
    ]
    return "\n".join(lines)


# =========================================================================
# optional bridges
# =========================================================================
class AgentMemBridge:
    """Mirror memory writes into AgentMem when it is installed.

    ``trade_memory`` stays the source of truth. This only adds a second index
    for semantic recall; if it is missing, every ``mirror`` call is a no-op
    and the system remembers exactly as much as it did before.
    """

    def __init__(self):
        self.available = module_available("agentmem")
        self._client = None

    def client(self):
        if self._client is None and self.available:  # pragma: no cover - optional dep
            import agentmem  # type: ignore

            factory = getattr(agentmem, "AgentMem", None) or getattr(agentmem, "Memory", None)
            self._client = factory() if factory else None
        return self._client

    def mirror(self, summary: str, metadata: Optional[dict] = None) -> bool:
        client = self.client()
        if client is None:
            return False
        for method in ("add", "remember", "write"):  # pragma: no cover - optional dep
            fn = getattr(client, method, None)
            if callable(fn):
                fn(summary, metadata or {})
                return True
        return False


class VendorRepo:
    """A cloned framework on disk, and what can be said about it."""

    def __init__(self, name: str, config: Optional[TradingConfig] = None):
        self.name = name
        self.path = vendor_path(name, config)

    @property
    def present(self) -> bool:
        return self.path.exists() and any(self.path.iterdir())

    def entrypoints(self) -> list:
        """Plausible entry scripts, for an operator deciding how to run it."""
        if not self.present:
            return []
        candidates = ["main.py", "cli.py", "run.py", "app.py", "package.json", "pyproject.toml"]
        return [str(p.relative_to(self.path)) for c in candidates for p in self.path.glob(c)]

    def doctor(self) -> str:
        if not self.present:
            return f"{self.name}: not cloned (run ./scripts/clone_all.sh)"
        tools = []
        if (self.path / "requirements.txt").exists():
            tools.append("pip install -r requirements.txt")
        if (self.path / "package.json").exists():
            tools.append("npm install")
        node = shutil.which("node")
        if (self.path / "package.json").exists() and not node:
            tools.append("(node not found on PATH)")
        entries = ", ".join(self.entrypoints()) or "no obvious entrypoint"
        return f"{self.name}: cloned at {self.path} — {entries}" + (
            f" — {'; '.join(tools)}" if tools else ""
        )


__all__ = [
    "AdapterStatus",
    "AgentMemBridge",
    "FRAMEWORKS",
    "VendorRepo",
    "module_available",
    "render_survey",
    "survey",
    "vendor_path",
]
