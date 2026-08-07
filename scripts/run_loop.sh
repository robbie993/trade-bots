#!/usr/bin/env bash
# Run the orchestrator loop. Spec section 12.
#
# Exit code 2 means a KILL_ALL stop condition fired — the system has concluded
# it does not work. A supervisor should NOT restart it on a 2.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

MODE="${1:-loop}"

case "$MODE" in
  once)
    # One pass. Suitable for cron: `*/60 * * * * scripts/run_loop.sh once`
    python -m src.main tick "${@:2}"
    ;;
  loop)
    python -m src.main run "${@:2}"
    ;;
  health)
    python -m src.main health
    ;;
  *)
    echo "usage: $0 [once|loop|health]" >&2
    exit 1
    ;;
esac
