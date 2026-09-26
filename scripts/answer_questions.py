"""Answer the firms' questions with Claude, through Claude Code on this PC.

    python scripts/answer_questions.py --dry-run     # show what would be asked
    python scripts/answer_questions.py --to-railway  # the scheduled run

Runs on the operator's PC every two hours (Windows Task Scheduler). For each
question the firms have filed (see src/trading/ask.py) that Claude has not yet
answered, it runs `claude -p` headless — the operator's own plan, no API key —
with the village's guardrails and the firm's numbers, and records the answer
with the model that gave it. The answer then reaches the asking firm's memory
as outside advice.

Claude is run from an empty scratch directory with no tools allowed: it answers
from the question and its context, and cannot read or change anything on this
machine while it does.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Task Scheduler hands this process a cp1252 console, and model answers are full
# of characters it cannot encode ("−", em dashes): the first run died on
# the third answer's print. Replace what cannot be shown rather than crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
sys.path.insert(0, str(REPO))

from src.trading import ask  # noqa: E402

ANSWERED_BY = "claude (Claude Code on the operator's PC)"

#: Task Scheduler does not always carry the interactive shell's PATH, so the
#: installed binary is used by path when it is where the installer put it.
_LOCAL = Path.home() / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")
CLAUDE = str(_LOCAL) if _LOCAL.exists() else "claude"
PER_RUN = 12
TIMEOUT_S = 300


def claude(prompt: str) -> tuple:
    """(answer text, model). Raises on a failed run."""
    with tempfile.TemporaryDirectory() as scratch:
        result = subprocess.run(
            [CLAUDE, "-p", "--output-format", "json", "--disallowedTools",
             "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task"],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT_S, cwd=scratch,
        )
    try:
        out = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"claude returned no JSON: {result.stdout[:200]!r} "
                           f"{result.stderr[:200]!r}") from exc
    if out.get("is_error"):
        raise RuntimeError(f"claude error: {str(out.get('result'))[:200]}")
    models = list((out.get("modelUsage") or {}).keys())
    return str(out.get("result") or ""), ",".join(models)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-railway", action="store_true")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from src.db.connection import Database
    from scripts.fleet_sync import railway_database_url

    db = Database.from_url(args.database_url or railway_database_url())
    db.init_schema()
    waiting = ask.open_questions(db, answered_by=ANSWERED_BY, limit=PER_RUN)
    print(f"{len(waiting)} question(s) Claude has not answered yet")
    failures = 0
    for q in waiting:
        print(f"\n#{q['id']} {q['firm_key']} [{q['topic']}]: {q['question'][:100]}")
        if args.dry_run:
            continue
        try:
            text, model = claude(ask.prompt_for(q))
        except Exception as exc:  # noqa: BLE001 - one question is not the run
            failures += 1
            print(f"  FAILED: {str(exc)[:200]}", file=sys.stderr)
            continue
        ask.answer(db, q["id"], ANSWERED_BY, text, model=model)
        print(f"  answered by {model}: {text[:200]}")
    db.close()
    return 1 if failures and failures == len(waiting) else 0


if __name__ == "__main__":
    raise SystemExit(main())
