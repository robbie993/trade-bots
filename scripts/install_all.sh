#!/usr/bin/env bash
# Install this project, then optionally the vendored frameworks' dependencies.
#
# The first half is all you need to run the trading ecosystem. The second half
# only does anything if you have run ./scripts/clone_all.sh, and it installs
# each vendored project into its OWN virtualenv — their pins conflict with each
# other, and none of them should be able to change what this repository's core
# loop depends on.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="${TRADE_VENDOR_DIR:-$ROOT/vendor}"
PY="${PYTHON:-python3}"

echo "==> this repository"
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -e "$ROOT[dev,web,analysis]" || {
  echo "editable install failed; falling back to requirements.txt"
  "$PY" -m pip install -r "$ROOT/requirements.txt"
}

echo
echo "==> checking the core loop"
"$PY" -m pytest "$ROOT/tests" -q || { echo "tests failed — stopping"; exit 1; }

echo
echo "==> optional: AgentMem (persistent memory mirror)"
"$PY" -m pip install agentmem 2>/dev/null && echo "    installed" || \
  echo "    not available on this index — the native memory in src/trading/brain is used"

if [ ! -d "$VENDOR" ]; then
  echo
  echo "No vendor/ directory. Run ./scripts/clone_all.sh first if you want the"
  echo "external frameworks. Nothing here needs them."
  exit 0
fi

echo
echo "==> vendored frameworks (each in its own virtualenv)"
for dir in "$VENDOR"/*/; do
  [ -d "$dir" ] || continue
  name="$(basename "$dir")"
  echo "--- $name"
  if [ -f "$dir/requirements.txt" ]; then
    "$PY" -m venv "$dir/.venv" 2>/dev/null
    "$dir/.venv/bin/pip" install -q -r "$dir/requirements.txt" \
      && echo "    python deps installed into $dir.venv" \
      || echo "    python deps failed (see $dir/requirements.txt)"
  fi
  if [ -f "$dir/package.json" ]; then
    if command -v npm >/dev/null; then
      (cd "$dir" && npm install --silent) && echo "    npm deps installed" \
        || echo "    npm install failed"
    else
      echo "    package.json present but node/npm is not on PATH"
    fi
  fi
done

echo
echo "Done. Status:"
echo "  python -m src.main trade frameworks"
