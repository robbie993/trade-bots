"""Entry point — spec section 12.

    python -m src.main <command>      (or: mvv <command>)

Everything lives in src/cli.py; this module exists so the file structure
matches the spec and so `python src/main.py` works from a checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # invoked as `python src/main.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.cli import main
else:
    from .cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
