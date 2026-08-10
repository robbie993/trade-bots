#!/usr/bin/env bash
# Point a hosted deployment at a database that has actually been set up.
#
# The hosted copy is read-only on purpose — it will not run migrations against
# your database from a public URL — so the schema has to be created from
# somewhere that can reach it. That is this script, run from your own machine.
#
# It asks for the connection string rather than making you export it, and it
# reads it without echoing, so a database password does not end up on screen,
# in your shell history, or in a file in this repository.
#
#     ./scripts/host_setup.sh
#
# Nothing here is destructive. `init-db` creates tables that do not exist,
# `trade init` never re-funds a firm that already exists, and `simulate` only
# appends. Run it twice and the second run is close to a no-op.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

DAYS="${MVV_SEED_DAYS:-45}"

# -------------------------------------------------------------------------
# the connection string
# -------------------------------------------------------------------------
if [ -z "${DATABASE_URL:-}" ]; then
  cat <<'EOF'
This needs the connection string for the database your deployment uses.

On Vercel: your project -> Storage -> the Postgres database -> the .env.local
tab -> copy the value of POSTGRES_URL_NON_POOLING.

Use the non-pooling one. Migrations sent through a connection pooler are a
known way to end up with a schema that is half applied.

Nothing is echoed as you paste, and it is not written anywhere.
EOF
  printf '\nConnection string: '
  read -r -s DATABASE_URL
  printf '\n\n'
  export DATABASE_URL
fi

case "$DATABASE_URL" in
  postgres://*|postgresql://*) ;;
  "")
    echo "No connection string given. Nothing done." >&2
    exit 1
    ;;
  sqlite*)
    echo "That is a SQLite URL. A serverless host has no disk that survives" >&2
    echo "a request, so the deployment cannot use it — attach Postgres." >&2
    exit 1
    ;;
  *)
    echo "That does not look like a database URL. Expected it to start with" >&2
    echo "postgres:// or postgresql://." >&2
    exit 1
    ;;
esac

# Say which database without saying the password.
python - <<'PY'
import os, re
url = os.environ["DATABASE_URL"]
print("==> database:", re.sub(r"//[^@]*@", "//***@", url.split("?")[0]))
PY

# -------------------------------------------------------------------------
# check it answers before changing anything
# -------------------------------------------------------------------------
echo "==> checking the database answers"
python - <<'PY'
import sys

# The driver first, and separately: "psycopg is not installed" and "your
# password is wrong" are both failures to connect, and only one of them is
# about the database.
try:
    import psycopg  # noqa: F401
except ImportError:
    print(
        "    the PostgreSQL driver is not installed.\n\n"
        "    Install the dependencies and run this again:\n"
        "        pip install -r requirements.txt\n"
        "    or, for just what the app needs:\n"
        "        pip install -e .\n\n"
        "    Nothing has been changed.",
        file=sys.stderr,
    )
    raise SystemExit(1)

from src.config import Config
from src.db.connection import Database

try:
    db = Database.from_url(Config().database_url)
    db.table_names()
    db.close()
except Exception as exc:
    print(f"    it did not answer: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(
        "\n    Wrong credentials, wrong host, or the database is not reachable\n"
        "    from here. Nothing has been changed.",
        file=sys.stderr,
    )
    raise SystemExit(1)
print("    ok")
PY

# -------------------------------------------------------------------------
# the three steps the hosted page asks for
# -------------------------------------------------------------------------
echo "==> creating the schema"
python -m src.main init-db

echo "==> creating the firms from config/firm_config.yaml"
python -m src.main trade init

echo "==> giving them a history to show (${DAYS} days)"
python -m src.main trade simulate --days "$DAYS"

cat <<'EOF'

Done. Two things left:

  1. Redeploy on Vercel, so the deployment picks up a database that now has
     tables in it.
  2. Open /village on the hosted URL — not /, which is the approval gate and
     is nearly empty on a mirror by design.

The hosted copy shows the village and changes nothing: every write is refused
and the controls are stripped out of the page. For the working console, with
the buttons, run it on this machine against a local database:

  ./scripts/local_console.sh

EOF
