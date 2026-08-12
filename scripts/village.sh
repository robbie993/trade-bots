#!/usr/bin/env bash
# The village, running on its own.
#
#     ./scripts/village.sh start     the loop and the console, in the background
#     ./scripts/village.sh status    what is alive, and when it last ticked
#     ./scripts/village.sh logs      follow what it is doing
#     ./scripts/village.sh stop      stop both
#
# `local_console.sh` serves pages and nothing else: close the browser and the
# village stops, because the only thing ticking was a button in a tab. This
# starts two processes instead — the tick loop and the web console — so the
# village keeps running while you look at Mission Control, look at the solar
# view, look at nothing, or close the laptop lid.
#
# What it turns on, deliberately and visibly:
#
#   TRADE_AUTONOMY=council   the council rules on what the evidence settles,
#                            and defers the rest to you. It has no panel for
#                            live trading, by construction.
#   TRADE_LIVING=on          the arena, bazaar and tavern run themselves. None
#                            of them can move cash.
#
# Neither grants a dollar. Capital still stops at the gate, which is why this
# is safe to leave running and why you will still find things waiting for you.
set -euo pipefail

cd "$(dirname "$0")/.."

RUN_DIR="run"
LOG_DIR="logs"
mkdir -p "$RUN_DIR" "$LOG_DIR" data

HOST="${MVV_GATE_HOST:-127.0.0.1}"
PORT="${MVV_GATE_PORT:-8000}"
INTERVAL="${MVV_TICK_INTERVAL:-60}"

# Local, always. See the note above about the gate.
unset VERCEL VERCEL_URL VERCEL_ENV VERCEL_REGION
export MVV_PUBLIC=0
# Deliberately NOT setting DATABASE_URL to some private path. The CLI has its
# own default (data/mvv.db), and a script that invents a different one gives you
# two databases: the village runs on one, and every command you type by hand
# talks to the other. That failure is silent and reads as "no such table".
#
# So: clear anything pointing at a hosted database, and let the same default
# everything else in this repository uses apply. MVV_LOCAL_DB still overrides it
# when you want a separate book on purpose.
if [ -n "${MVV_LOCAL_DB:-}" ]; then
  export DATABASE_URL="$MVV_LOCAL_DB"
fi
export TRADE_AUTONOMY="${TRADE_AUTONOMY:-council}"
export TRADE_LIVING="${TRADE_LIVING:-on}"

LOOP_PID="$RUN_DIR/loop.pid"
WEB_PID="$RUN_DIR/web.pid"

alive() {  # alive <pidfile>
  [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

start_one() {  # start_one <name> <pidfile> <logfile> <command...>
  local name="$1" pidfile="$2" logfile="$3"; shift 3
  if alive "$pidfile"; then
    echo "    $name already running (pid $(cat "$pidfile"))"
    return
  fi
  nohup "$@" >>"$logfile" 2>&1 &
  echo $! >"$pidfile"
  echo "    $name started (pid $!) -> $logfile"
}

stop_one() {  # stop_one <name> <pidfile>
  local name="$1" pidfile="$2"
  if ! alive "$pidfile"; then
    echo "    $name not running"
    rm -f "$pidfile"
    return
  fi
  local pid; pid="$(cat "$pidfile")"
  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.3
  done
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$pidfile"
  echo "    $name stopped"
}

case "${1:-start}" in

start)
  echo "==> database: ${DATABASE_URL:-the default (data/mvv.db)}"
  # An earlier version of this script invented its own database file, so the
  # village ran on one book while every command typed by hand read another.
  # If that file is still here, say so rather than quietly leaving it behind.
  if [ -z "${MVV_LOCAL_DB:-}" ] && [ -f data/village.db ]; then
    cat <<'EOF'

    NOTE: data/village.db is left over from an earlier version of this script,
    which used its own database file. Everything now shares the one the CLI
    uses (data/mvv.db), so `trade recruit-watch` and friends see the same
    village as the page does.

    That old file still holds whatever it recorded. To carry it over, stop the
    village and run:

        mv data/mvv.db data/mvv.db.bak 2>/dev/null
        mv data/village.db data/mvv.db
        ./scripts/village.sh start      # init-db brings the schema up to date

    Or ignore this and start fresh — nothing is lost until you delete it.

EOF
  fi
  python -m src.main init-db >/dev/null
  python -m src.main trade init >/dev/null
  echo "==> starting"
  start_one "tick loop" "$LOOP_PID" "$LOG_DIR/loop.log" \
    python -m src.main trade run --interval "$INTERVAL"
  start_one "console"   "$WEB_PID"  "$LOG_DIR/web.log" \
    python -m src.main serve --host "$HOST" --port "$PORT"
  # Both processes are given a moment to fall over before this claims they
  # are up. The first version printed the Mission Control URL unconditionally,
  # so a console that died on a busy port looked exactly like one that started
  # — and the URL it printed was for a process that no longer existed.
  sleep 2
  failed=0
  for pair in "tick loop:$LOOP_PID:$LOG_DIR/loop.log" "console:$WEB_PID:$LOG_DIR/web.log"; do
    name="${pair%%:*}"; rest="${pair#*:}"
    pidfile="${rest%%:*}"; logfile="${rest##*:}"
    if ! alive "$pidfile"; then
      failed=1
      echo
      echo "    $name DIED on startup. The last of $logfile:"
      tail -6 "$logfile" 2>/dev/null | sed 's/^/      /'
      rm -f "$pidfile"
    fi
  done
  if [ "$failed" = "1" ]; then
    cat <<EOF

Not everything started, so the village is not fully up.

A console that dies immediately is usually the port: something else is already
on ${PORT}. Find it with \`lsof -i :${PORT}\`, or just use another one:

    MVV_GATE_PORT=8010 ./scripts/village.sh restart

EOF
    exit 1
  fi
  cat <<EOF

==> Mission Control:  http://${HOST}:${PORT}/village
    Solar view:       http://${HOST}:${PORT}/village/solar

The village is ticking every ${INTERVAL}s on its own. Close the browser, close
this terminal — it keeps going. Capital still stops at the gate, so expect to
find decisions waiting for you.

    ./scripts/village.sh status
    ./scripts/village.sh logs
    ./scripts/village.sh stop

EOF
  ;;

stop)
  echo "==> stopping"
  stop_one "tick loop" "$LOOP_PID"
  stop_one "console"   "$WEB_PID"
  ;;

restart)
  "$0" stop
  "$0" start
  ;;

status)
  for pair in "tick loop:$LOOP_PID" "console:$WEB_PID"; do
    name="${pair%%:*}"; pidfile="${pair##*:}"
    if alive "$pidfile"; then
      echo "  $name: running (pid $(cat "$pidfile"))"
    else
      echo "  $name: stopped"
    fi
  done
  python - <<'PY'
import os
try:
    from src.config import Config
    from src.db.connection import Database

    db = Database.from_url(Config().database_url)
    if "flow_events" not in set(db.table_names()):
        db.close()
        print("  last tick: never (no database yet — run `village.sh start`)")
        raise SystemExit(0)
    row = db.query_one("SELECT MAX(created_at) AS last FROM flow_events")
    db.close()
    print(f"  last tick: {(row or {}).get('last') or 'never'}")
except SystemExit:
    raise
except Exception as exc:
    print(f"  last tick: unknown ({type(exc).__name__}: {exc})")
PY
  echo "  waiting on you:"
  python -m src.main approvals 2>/dev/null | sed 's/^/    /' || true
  ;;

logs)
  tail -f "$LOG_DIR/loop.log" "$LOG_DIR/web.log"
  ;;

*)
  echo "usage: $0 {start|stop|restart|status|logs}" >&2
  exit 1
  ;;
esac
