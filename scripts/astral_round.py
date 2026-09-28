"""Write one round of Astral iterations: ten files built on one parent.

    python scripts/astral_round.py spec.json

Every iteration file exports the same four names, so the next round can
build on any of them:

    PARAMS        overrides for `run`'s SETTINGS (bots/astral.py)
    TRAIL         (bars, atrs) or None
    targets       the take-profit ladder function
    entry_filter  a veto on new positions, or None
    revert_exit   reversion's catch-up exit as [(z, keep), ...], or None

A child starts from its parent's four and changes what the spec says. The
spec is JSON: {"round": "x1", "parent": "astral_vt_basket", "note": "...",
"variants": {name: {"title", "doc", "params", "trail", "filter", "targets"}}},
where "filter" and "targets" are Python function bodies (indented four
spaces) and may call `parent_filter` or `parent_targets`.
"""

import json
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# How a parent that predates this script is read. Anything written by it
# exports all four names; the older iterations export only `targets`.
LEGACY = """try:
    from bots.{parent} import PARAMS as PARENT_PARAMS
except ImportError:
    PARENT_PARAMS = {{}}
try:
    from bots.{parent} import TRAIL as PARENT_TRAIL
except ImportError:
    PARENT_TRAIL = RUN_TRAIL
try:
    from bots.{parent} import entry_filter as parent_filter
except ImportError:
    parent_filter = None
try:
    from bots.{parent} import revert_exit as parent_revert_exit
except ImportError:
    parent_revert_exit = None
from bots.{parent} import targets as parent_targets
"""


def render(round_id, parent, index, name, spec, note):
    lines = [f'"""ASTRAL {round_id.upper()}.{index}: {spec["title"]}', "",
             textwrap.fill(spec["doc"].strip(), 76), "",
             textwrap.fill(f"Built on {parent}: {note} Only what is described above "
                           "differs from the parent. See `run` in bots/astral.py. "
                           "Not in the village.", 76),
             '"""', ""]
    if spec.get("imports"):
        lines += spec["imports"] + [""]
    lines += ["from bots.astral import RUN_TRAIL, run",
              LEGACY.format(parent=parent), ""]
    lines.append(f"PARAMS = dict(PARENT_PARAMS, **{spec.get('params', {})!r})")
    trail = spec.get("trail", "parent")
    lines.append("TRAIL = PARENT_TRAIL" if trail == "parent" else f"TRAIL = {trail!r}")
    lines.append("")
    lines.append("")
    if spec.get("filter"):
        lines += ["def entry_filter(context, symbol, read, basket):",
                  "    if parent_filter is not None and not parent_filter(context, symbol, read, basket):",
                  "        return False",
                  spec["filter"].rstrip()]
    else:
        lines.append("entry_filter = parent_filter")
    lines += ["", ""]
    if spec.get("targets"):
        lines += ["def targets(context, symbol, read, entry, basket):", spec["targets"].rstrip()]
    else:
        lines.append("targets = parent_targets")
    lines += ["", ""]
    if spec.get("revert_exit"):
        lines += ["def revert_exit(context, symbol, read, basket):", spec["revert_exit"].rstrip()]
    else:
        lines.append("revert_exit = parent_revert_exit")
    lines += ["", "", "def propose(context):",
              "    return run(context, take_profit=targets, trail=TRAIL, params=PARAMS,",
              "               entry_filter=entry_filter, revert_exit=revert_exit)", ""]
    return "\n".join(lines)


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    for i, (name, v) in enumerate(spec["variants"].items(), 1):
        path = REPO / "bots" / f"astral_{spec['round']}_{name}.py"
        path.write_text(render(spec["round"], spec["parent"], i, name, v, spec["note"]),
                        encoding="utf-8", newline="\n")
        print(path.relative_to(REPO))


if __name__ == "__main__":
    main()
