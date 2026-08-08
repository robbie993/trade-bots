"""Reviewer backends for the File Court.

Each backend is one reviewer. They differ in how they run — a subprocess, an
npm binary, a skill that only exists inside an interactive Claude Code session
— but they all answer the same question and return the same shape.

The one rule every backend obeys:

    **A reviewer that did not run does not get a score.**

The deployment prompt this was built from had `run_codetribunal` fall back to
``{"risk_score": 50}`` whenever the subprocess produced no stdout. That is the
worst possible failure mode for a court: a missing reviewer silently votes
"medium risk", every file lands on MIXED, and the system looks like it is
working while reviewing nothing. Here a backend that cannot run returns
``available=False`` with the reason, contributes no score, and is reported.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from ..money import D

# Risk is a 0-100 score, quantized like money is: the boundaries at 40 and 70
# decide verdicts, so binary float drift is not acceptable there either.
RISK_PRECISION = Decimal("0.01")


def risk(value: Any) -> Optional[Decimal]:
    """Coerce a backend's reported risk score into a bounded Decimal."""
    if value is None:
        return None
    try:
        score = D(value)
    except Exception:
        return None
    if score < 0:
        score = Decimal("0")
    if score > 100:
        score = Decimal("100")
    return score.quantize(RISK_PRECISION)


@dataclass
class ReviewOutcome:
    """One reviewer's answer.

    ``available`` is the load-bearing field. When it is False the backend did
    not produce a judgement, ``score`` is None, and ``error`` says why.
    """

    backend: str
    available: bool
    score: Optional[Decimal] = None
    verdict: str = ""
    summary: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def counted(self) -> bool:
        """Whether this outcome contributes a vote to the aggregate."""
        return self.available and self.score is not None

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "available": self.available,
            "score": str(self.score) if self.score is not None else None,
            "verdict": self.verdict,
            "summary": self.summary,
            "error": self.error,
        }

    @classmethod
    def unavailable(cls, backend: str, error: str) -> "ReviewOutcome":
        return cls(backend=backend, available=False, error=error)


class ReviewBackend:
    """Interface every reviewer implements."""

    name = "backend"

    def is_available(self) -> tuple[bool, str]:
        """(usable, reason_if_not). Checked before review() is called."""
        raise NotImplementedError

    def review(self, file_path: Path) -> ReviewOutcome:
        raise NotImplementedError


def extract_json(text: str) -> Optional[dict]:
    """Pull a JSON object out of output that may also contain log noise.

    CodeTribunal is packaged as a Gradio Space, so its CLI path can emit
    banners, download progress and warnings around the payload. Parsing the
    whole of stdout is tried first; failing that, the last balanced ``{...}``
    span that parses as an object wins, since the verdict is printed last.

    Returns None rather than a guess when nothing parses — a reviewer whose
    output cannot be read has not produced a score.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        whole = json.loads(text)
        if isinstance(whole, dict):
            return whole
    except json.JSONDecodeError:
        pass

    # Try to decode an object at each "{", keeping the last one that works.
    #
    # raw_decode does the brace matching, which matters more than it looks:
    # a hand-rolled depth counter is derailed by an unbalanced brace in log
    # noise ("Loading weights... {corrupt"), because the counter never returns
    # to zero and every later object looks nested inside the garbage. Here a
    # span that does not parse is simply skipped.
    decoder = json.JSONDecoder()
    found = None
    i = text.find("{")
    while i != -1:
        try:
            candidate, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(candidate, dict):
            found = candidate
            i = text.find("{", max(end, i + 1))
        else:
            i = text.find("{", i + 1)
    return found


class CodeTribunalBackend(ReviewBackend):
    """CodeTribunal — forensic evidence, investigation, trial, verdict.

    It is a Hugging Face **Space**, not a GitHub repo and not a model repo:

        git clone https://huggingface.co/spaces/amine-yagoub/CodeTribunal
        cd CodeTribunal && pip install -r requirements.txt
        cp .env.example .env      # then set ZAI_API_KEY

    Point MVV_CODETRIBUNAL_PATH at the clone. Two layouts are recognised, so
    this keeps working if the Space is restructured:

    * ``src/code_tribunal/app.py``  -> ``python -m code_tribunal.app``
      (the current layout, run with src/ on PYTHONPATH)
    * ``main.py`` at the root       -> ``python main.py``

    Until one of them is found the backend reports unavailable and scores
    nothing.
    """

    name = "codetribunal"

    def __init__(
        self,
        path: Path,
        timeout_s: int = 600,
        entrypoint: str = "",
        extra_args: Optional[list] = None,
    ):
        self.path = Path(path)
        self.timeout_s = timeout_s
        self.entrypoint = entrypoint  # "" = autodetect
        # `is not None`, not a truthiness check: an empty list means "pass no
        # extra arguments", which a falsy test would silently override with
        # the default and make --output json impossible to remove.
        self.extra_args = (
            list(extra_args) if extra_args is not None else ["--output", "json"]
        )

    # -- layout detection -------------------------------------------------
    def _module_pkg(self) -> Optional[Path]:
        candidate = self.path / "src" / "code_tribunal" / "app.py"
        return candidate if candidate.exists() else None

    def _root_script(self) -> Optional[Path]:
        name = self.entrypoint or "main.py"
        candidate = self.path / name
        return candidate if candidate.exists() else None

    def invocation(self) -> Optional[tuple]:
        """(argv_prefix, env_overrides) for the detected layout, or None."""
        if self.entrypoint:
            script = self._root_script()
            if script:
                return ["python", str(script)], {}
            return None
        if self._module_pkg():
            return (
                ["python", "-m", "code_tribunal.app"],
                {"PYTHONPATH": str((self.path / "src").resolve())},
            )
        script = self._root_script()
        if script:
            return ["python", str(script)], {}
        return None

    def is_available(self) -> tuple[bool, str]:
        if not self.path.exists():
            return False, f"CodeTribunal not cloned at {self.path}"
        if self.invocation() is None:
            return (
                False,
                f"no entrypoint under {self.path} "
                "(expected src/code_tribunal/app.py or main.py)",
            )
        return True, ""

    def review(self, file_path: Path) -> ReviewOutcome:
        ok, why = self.is_available()
        if not ok:
            return ReviewOutcome.unavailable(self.name, why)

        argv, env_overrides = self.invocation()
        cmd = argv + ["--file", str(Path(file_path).resolve())] + self.extra_args
        env = None
        if env_overrides:
            env = dict(os.environ)
            env.update(env_overrides)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=str(self.path),
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ReviewOutcome.unavailable(
                self.name, f"timed out after {self.timeout_s}s"
            )
        except OSError as exc:
            return ReviewOutcome.unavailable(self.name, f"could not start: {exc}")

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else "no output"
            return ReviewOutcome.unavailable(
                self.name, f"exit {proc.returncode}: {tail}"
            )

        if not (proc.stdout or "").strip():
            # The bug this class exists to not have.
            return ReviewOutcome.unavailable(self.name, "produced no output")

        payload = extract_json(proc.stdout)
        if payload is None:
            return ReviewOutcome.unavailable(
                self.name, "unparseable output: no JSON object found"
            )

        score = risk(payload.get("risk_score"))
        if score is None:
            return ReviewOutcome.unavailable(
                self.name, "no risk_score in CodeTribunal output"
            )

        return ReviewOutcome(
            backend=self.name,
            available=True,
            score=score,
            verdict=str(payload.get("verdict", "")).upper(),
            summary=str(payload.get("summary", "")),
            raw=payload,
        )


class CommandBackend(ReviewBackend):
    """A reviewer exposed as a JSON-on-stdout command.

    Yama (``npm install -g @juspay/yama``, github.com/juspay/yama) is the one
    this was written for, but anything that prints a JSON object with a risk
    score fits.
    """

    name = "command"

    def __init__(
        self,
        name: str,
        argv: list,
        timeout_s: int = 600,
        score_key: str = "risk_score",
    ):
        self.name = name
        self.argv = list(argv)
        self.timeout_s = timeout_s
        self.score_key = score_key

    def is_available(self) -> tuple[bool, str]:
        if not self.argv:
            return False, "no command configured"
        if shutil.which(self.argv[0]) is None:
            return False, f"{self.argv[0]} not on PATH"
        return True, ""

    def review(self, file_path: Path) -> ReviewOutcome:
        ok, why = self.is_available()
        if not ok:
            return ReviewOutcome.unavailable(self.name, why)

        cmd = [str(a).replace("{file}", str(Path(file_path).resolve())) for a in self.argv]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_s
            )
        except subprocess.TimeoutExpired:
            return ReviewOutcome.unavailable(self.name, f"timed out after {self.timeout_s}s")
        except OSError as exc:
            return ReviewOutcome.unavailable(self.name, f"could not start: {exc}")

        if proc.returncode != 0:
            return ReviewOutcome.unavailable(self.name, f"exit {proc.returncode}")

        payload = extract_json(proc.stdout)
        if payload is None:
            return ReviewOutcome.unavailable(self.name, "did not print JSON")

        score = risk(payload.get(self.score_key))
        if score is None:
            return ReviewOutcome.unavailable(self.name, f"no {self.score_key} in output")

        return ReviewOutcome(
            backend=self.name,
            available=True,
            score=score,
            verdict=str(payload.get("verdict", "")).upper(),
            summary=str(payload.get("summary", "")),
            raw=payload,
        )


class InteractiveSkillBackend(ReviewBackend):
    """A reviewer that only exists inside an interactive Claude Code session.

    The Tribunal skill (``/tribunal``) and the Agent Review Panel
    (``/roundtable:agent-review-panel``) are prompts that run in a Claude Code
    conversation. A background daemon cannot invoke them — there is no session
    to answer, and no non-interactive entrypoint to shell out to.

    So this backend does not pretend. It records that the file *needs* that
    review and leaves the case open. `mvv court pending` lists what is waiting,
    and the operator runs the slash command against those files. Recording the
    obligation is honest; inventing a score would not be.
    """

    name = "interactive"

    def __init__(self, name: str, command: str, scope: str = ""):
        self.name = name
        self.command = command
        # What the skill actually reviews. /tribunal works on the current
        # branch's diff against a base branch, not on one file, so telling the
        # operator to "run /tribunal on nasty.py" would misdescribe it.
        self.scope = scope

    def is_available(self) -> tuple[bool, str]:
        return False, f"{self.command} is interactive — run it in Claude Code"

    def review(self, file_path: Path) -> ReviewOutcome:
        where = f" ({self.scope})" if self.scope else f" on {Path(file_path).name}"
        return ReviewOutcome.unavailable(
            self.name, f"awaiting operator: run {self.command}{where}"
        )


def build_backends(court_config) -> dict:
    """Assemble the three tiers of the court from configuration."""
    tier1 = CodeTribunalBackend(
        path=court_config.codetribunal_path,
        timeout_s=court_config.backend_timeout_s,
        entrypoint=court_config.codetribunal_entrypoint,
        extra_args=court_config.codetribunal_args,
    )
    tier2 = InteractiveSkillBackend(
        "tribunal",
        "/tribunal",
        scope="reviews the branch diff, not a single file",
    )
    tier3 = InteractiveSkillBackend(
        "agent_review_panel", "/roundtable:agent-review-panel"
    )

    backends = {"tier1": [tier1], "tier2": [tier2], "tier3": [tier3]}

    if court_config.yama_enabled:
        backends["tier1"].append(
            CommandBackend(
                "yama",
                ["yama", "review", "--file", "{file}", "--output", "json"],
                timeout_s=court_config.backend_timeout_s,
            )
        )
    return backends
