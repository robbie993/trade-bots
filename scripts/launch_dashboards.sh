#!/usr/bin/env bash
# Launch what this repository can actually show you.
#
# The build document lists six dashboards from six external projects. Five of
# them belong to repositories that are not part of this system, so this script
# does not pretend to start them — it starts the two surfaces that exist here
# and then tells you, honestly, what the others need.
#
#   http://127.0.0.1:8000   the approval gate (KILL / CONTINUE buttons)
#   terminal                the live ecosystem monitor
#
# Ctrl-C stops both.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="${TRADE_VENDOR_DIR:-$ROOT/vendor}"
PY="${PYTHON:-python3}"
HOST="${MVV_WEB_HOST:-127.0.0.1}"
PORT="${MVV_WEB_PORT:-8000}"
INTERVAL="${TRADE_MONITOR_INTERVAL:-30}"

cd "$ROOT"

cleanup() {
  echo
  echo "stopping…"
  [ -n "${WEB_PID:-}" ] && kill "$WEB_PID" 2>/dev/null
  exit 0
}
trap cleanup INT TERM

echo "==> approval gate on http://$HOST:$PORT"
"$PY" -m src.main serve --host "$HOST" --port "$PORT" &
WEB_PID=$!
sleep 2

if ! kill -0 "$WEB_PID" 2>/dev/null; then
  echo "    failed to start (FastAPI missing? pip install -e '.[web]')"
  WEB_PID=""
fi

echo
echo "==> external dashboards"
for name in clawock TradingAgents ai-broker ai-fund OmniRoute; do
  if [ -d "$VENDOR/$name" ]; then
    echo "    $name is cloned at $VENDOR/$name — start it with its own instructions"
  else
    echo "    $name not cloned (./scripts/clone_all.sh $name)"
  fi
done

echo
echo "==> ecosystem monitor (every ${INTERVAL}s)"
echo
exec "$PY" -m src.main trade monitor --watch --interval "$INTERVAL"
