"""Settings that survive closing a terminal.

Everything in this system is configurable by environment variable, which is
right for a container and wrong for a laptop. On a laptop it means the
configuration lives in one shell window and nowhere else:

* `export TRADE_DATA_SOURCE=alpaca` is gone when that window closes;
* `TRADE_DATA_SOURCE=alpaca some-command` applies to *that command* and not the
  next one, which is a shell rule everyone knows and nobody remembers at
  eleven at night;
* the background tick loop and the web console are separate processes, so a
  variable exported after they started reaches neither.

The result is a village where the loop is reading one feed, the page is
reading another, and a command typed by hand is reading a third — with nothing
saying so. That is not a configuration system, it is a memory test.

So: a `.env` file in the repository root, read once at startup by every
process that starts here.

**The real environment always wins.** A value already set is never overwritten,
so `TRADE_DATA_SOURCE=synthetic ./scripts/village.sh start` still overrides the
file for that run, and a container's injected variables are untouched. The file
is a default, not an authority.

**It is gitignored, and that is the point.** API keys belong in a file git will
not take rather than in a shell you have to remember to set up. Nothing here
logs a value, and `.env.example` carries the names with no secrets in it.

No dependency. python-dotenv would do this and is not worth an import for
thirty lines, on the same principle as the YAML parser in `firms/spec.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / ".env"


def parse(text: str) -> dict:
    """KEY=VALUE lines into a dict. Tolerant of how people actually write them.

    Accepts `export KEY=value`, quoted values, inline comments outside quotes,
    and blank lines. Refuses nothing loudly: a line it cannot read is skipped,
    because a malformed `.env` should not stop a village from starting.
    """
    out: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue

        value = value.strip()
        if value[:1] in ("'", '"'):
            # Quoted: everything up to the matching quote, exactly as written,
            # and whatever follows is a comment. Checking only first and last
            # character got this wrong for `KEY='value'  # note`, which kept
            # the quotes and produced a secret with apostrophes in it.
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = value.split(" #", 1)[0].strip()   # unquoted: trailing comment
        out[key] = value
    return out


def load(path=None) -> list:
    """Read `.env` into the environment. Returns the names it set.

    Names, never values — the return is used for a "loaded 3 settings from
    .env" line, and a startup message that prints an API key is worse than no
    startup message.
    """
    path = Path(path) if path is not None else DEFAULT_PATH
    try:
        text = path.read_text()
    except OSError:
        return []

    applied = []
    for key, value in parse(text).items():
        if key in os.environ:
            continue                     # the real environment wins, always
        os.environ[key] = value
        applied.append(key)
    return applied


__all__ = ["DEFAULT_PATH", "load", "parse"]
