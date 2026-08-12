#!/usr/bin/env bash
# The container's entry point: one image, two jobs.
#
#     MVV_ROLE=web      serve the pages          (the default)
#     MVV_ROLE=worker   tick the village forever
#
# This is the shape a hosted village needs and a laptop one does not. On your
# own machine `village.sh` starts two processes side by side; on a platform
# they are two *services* sharing one database, because that is how a platform
# restarts, scales and logs them separately. Same image, same code, same
# database — the only difference is this variable.
#
# Which job does what, and why the split is a safety boundary rather than a
# deployment detail:
#
#   the worker  trades, scores, kills, holds the council, runs the living
#               quarters. It has no HTTP surface at all, so nothing it does is
#               reachable from the internet. This is the piece that makes the
#               village *live* rather than a mirror.
#
#   the web     serves pages. It is read-only unless somebody signs in
#               (MVV_GATE_TOKEN — see src/access.py), and it never migrates
#               anything: running schema changes from a public URL is not
#               read-only, whoever is signed in.
#
# Migrations happen in the worker, once, before the loop starts. One writer
# doing schema work is the whole reason to put it there rather than in both.
set -euo pipefail

cd "$(dirname "$0")/.."

ROLE="${MVV_ROLE:-web}"

# Platforms hand you the port to listen on and expect you to use it. Falling
# back to MVV_GATE_PORT keeps the local scripts working unchanged.
PORT="${PORT:-${MVV_GATE_PORT:-8000}}"
HOST="${MVV_GATE_HOST:-0.0.0.0}"
INTERVAL="${MVV_TICK_INTERVAL:-300}"

case "$ROLE" in

web)
  echo "==> web on ${HOST}:${PORT}"
  # Deliberately no init-db here. If the tables are missing the page says so,
  # with the command to fix it, which is a better failure than a public URL
  # that quietly rewrites the schema of the database it is reading.
  exec python -m src.main serve --host "$HOST" --port "$PORT"
  ;;

worker)
  echo "==> worker: bringing the schema up to date"
  python -m src.main init-db
  echo "==> worker: creating firms from config/firm_config.yaml"
  # Never re-funds a firm that already exists — it updates the mandate and
  # nothing else, so running it on every deploy is safe and keeps the village
  # in step with the config in the image.
  python -m src.main trade init

  # Same two switches village.sh turns on, and for the same reasons. Neither
  # grants a dollar: the council has no panel for live trading by
  # construction, and the living quarters cannot move cash. Capital still
  # stops at the gate, which is what makes this safe to leave running
  # unattended on a machine you are not looking at.
  export TRADE_AUTONOMY="${TRADE_AUTONOMY:-council}"
  export TRADE_LIVING="${TRADE_LIVING:-on}"

  echo "==> worker: ticking every ${INTERVAL}s"
  exec python -m src.main trade run --interval "$INTERVAL"
  ;;

*)
  echo "unknown MVV_ROLE=$ROLE (expected 'web' or 'worker')" >&2
  exit 1
  ;;
esac
