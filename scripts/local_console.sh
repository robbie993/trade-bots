#!/usr/bin/env bash
# The real console: every button works, on a machine you control.
#
# The point of this script is the first line of it. If you have just set up a
# hosted database, `DATABASE_URL` is still exported in this terminal, and
# starting the console would put your clicking straight into the database the
# internet is reading. So it is cleared, deliberately, every time.
#
#     ./scripts/local_console.sh
#
# Use a different local file with MVV_LOCAL_DB, or reach a specific database on
# purpose by running `python -m src.main serve` yourself.
set -euo pipefail

cd "$(dirname "$0")/.."

# Whatever is exported for the hosted deployment, this is not that.
unset DATABASE_URL
unset POSTGRES_URL POSTGRES_URL_NON_POOLING POSTGRES_PRISMA_URL DATABASE_URL_UNPOOLED

# Read-only mode turns itself on when the host looks serverless. It is not one.
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

HOST="${MVV_GATE_HOST:-127.0.0.1}"
PORT="${MVV_GATE_PORT:-8000}"

echo "==> local database: ${DATABASE_URL:-the default (data/mvv.db)}"

echo "==> schema"
python -m src.main init-db >/dev/null
echo "    ok"

echo "==> firms"
python -m src.main trade init

if [ "${MVV_SEED_DAYS:-0}" != "0" ]; then
  echo "==> seeding ${MVV_SEED_DAYS} days of history"
  python -m src.main trade simulate --days "$MVV_SEED_DAYS"
fi

cat <<EOF

==> Mission Control:  http://${HOST}:${PORT}/village
    Approval gate:    http://${HOST}:${PORT}/

Bound to ${HOST}. This page has no authentication — anyone who can reach it
can approve spending — which is exactly why it lives here and not on a public
URL. Keep it on localhost unless you have put your own auth in front of it.

Ctrl-C to stop.

EOF

exec python -m src.main serve --host "$HOST" --port "$PORT"
