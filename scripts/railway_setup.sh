#!/usr/bin/env bash
# Put the village on Railway, from your machine, in one go.
#
#     ./scripts/railway_setup.sh
#
# It creates the two services HOSTING.md describes — a worker that ticks and a
# web service that serves pages — points both at a Postgres, generates a
# sign-in token, and deploys. Everything it does is something you could do by
# clicking; this is the same clicks in the right order.
#
# WHY THIS RUNS ON YOUR MACHINE AND NOT SOMEWHERE ELSE
#
# Railway's CLI authenticates as *you*, with an account-level credential that
# can read and change every project in the workspace. That credential should
# live in exactly one place — your keychain, put there by `railway login` — and
# should never be pasted into a chat window, a file, or an environment variable
# on a machine you do not own. So the setup runs here, where you already are.
#
# WHAT IT DOES WITH THE SECRET IT CREATES
#
# The sign-in token is generated locally by Python's `secrets`, sent to Railway
# through **stdin** rather than as a command-line argument — arguments are
# visible to every process on the machine via `ps`, and land in your shell
# history — and written to `.env`, which is gitignored. It is deliberately not
# printed. You will not need to read it often, and terminal scrollback has a
# way of ending up in screenshots.
#
#     grep MVV_GATE_TOKEN .env        # when you do need it
#
# NOTHING HERE IS DESTRUCTIVE. Creating a service that exists is skipped,
# setting a variable to what it already is changes nothing, and no command
# below deletes anything. Run it twice and the second run is a no-op with a
# fresh deploy on the end.
set -euo pipefail

cd "$(dirname "$0")/.."

WORKER="${RAILWAY_WORKER_SERVICE:-village-worker}"
WEB="${RAILWAY_WEB_SERVICE:-village-web}"
# Railway names the database service "Postgres" by default. The reference
# below resolves through that name, so if yours is called something else, say
# so: RAILWAY_PG_SERVICE=my-db ./scripts/railway_setup.sh
PG="${RAILWAY_PG_SERVICE:-Postgres}"
INTERVAL="${MVV_TICK_INTERVAL:-300}"
ASSUME_YES=0
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# Every command that changes something goes through here, so `--dry-run` shows
# you the exact sequence without running any of it. That matters more than
# usual for this script: it talks to an account I cannot see, and a setup
# script you can read before trusting is worth more than one that promises.
run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '    would run: %s\n' "$*"
    return 0
  fi
  "$@"
}

# Same, but the real command's output is discarded. Used for every `variables`
# call: the CLI echoes the service's variables back after setting them, and
# that list now contains the sign-in token. A setup script that prints your
# secret into scrollback has undone the reason it pipes it through stdin.
runq() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '    would run: %s\n' "$*"
    return 0
  fi
  "$@" >/dev/null
}

# Asking "does this service exist" by listing its variables. The CLI has no
# dedicated check, and this is the cheapest read that fails cleanly on a name
# the project does not have. If it ever guesses wrong the cost is one skipped
# creation and a clear error from the next command, not a duplicate service.
exists() {  # exists <service-name>
  railway variables --service "$1" >/dev/null 2>&1
}

service() {  # service <name>
  local name="$1"
  if exists "$name"; then
    echo "    '$name' already exists — updating its variables"
  else
    runq railway add -s "$name"
    echo "    created '$name'"
  fi
}

# -------------------------------------------------------------------------
# the CLI, and who you are
# -------------------------------------------------------------------------
if ! command -v railway >/dev/null 2>&1; then
  cat >&2 <<'EOF'
The Railway CLI is not installed. One of:

    brew install railway
    npm i -g @railway/cli

Then run this again.
EOF
  exit 1
fi

if ! railway whoami >/dev/null 2>&1; then
  say "signing in to Railway (a browser will open)"
  railway login
fi
railway whoami

# -------------------------------------------------------------------------
# which project
# -------------------------------------------------------------------------
if ! railway status >/dev/null 2>&1; then
  say "linking this directory to a Railway project"
  echo "    Pick the workspace and project the village should live in."
  railway link
fi
railway status || true

# -------------------------------------------------------------------------
# say what is about to happen, before it happens
# -------------------------------------------------------------------------
cat <<EOF

This will, in the project above:

  1. add a PostgreSQL database, if the project has none
  2. create the service '${WORKER}'  — ticks the village, no public URL
  3. create the service '${WEB}'     — serves Mission Control, public URL
  4. generate a sign-in token and set it on '${WEB}' (also saved to .env)
  5. deploy this directory to both services
  6. give '${WEB}' a public domain

Both services are billed by Railway while they run. The worker ticks every
${INTERVAL}s and keeps ticking whether or not you are watching, which is
the point of it.

EOF

if [ "$ASSUME_YES" != "1" ]; then
  printf 'Go ahead? [y/N] '
  read -r reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "Nothing done."; exit 0 ;;
  esac
fi

# -------------------------------------------------------------------------
# the database
# -------------------------------------------------------------------------
say "database"
if exists "$PG"; then
  echo "    '$PG' already exists — leaving it alone"
else
  run railway add -d postgres
  echo "    added PostgreSQL"
fi

# The reference, not the value. Railway resolves \${{Postgres.DATABASE_URL}}
# at deploy time, so the connection string is never on this machine, never in
# this repository, and never in your shell history. Single-quoted so the shell
# does not try to expand it on the way past.
DB_REF="\${{${PG}.DATABASE_URL}}"

# -------------------------------------------------------------------------
# the two services
# -------------------------------------------------------------------------
say "the worker (ticks the village; listens on no port)"
service "$WORKER"
# --skip-deploys throughout: each of these would otherwise trigger its own
# redeploy, so the service would rebuild three times before the code arrives.
runq railway variables --service "$WORKER" --skip-deploys \
  --set "MVV_ROLE=worker" \
  --set "DATABASE_URL=$DB_REF" \
  --set "MVV_TICK_INTERVAL=$INTERVAL"
echo "    variables set"

say "the web console (serves pages; read-only until you sign in)"
service "$WEB"
runq railway variables --service "$WEB" --skip-deploys \
  --set "MVV_ROLE=web" \
  --set "DATABASE_URL=$DB_REF"
echo "    variables set"

# -------------------------------------------------------------------------
# the token
# -------------------------------------------------------------------------
say "sign-in token"
if grep -q '^MVV_GATE_TOKEN=.\{16,\}' .env 2>/dev/null; then
  TOKEN="$(grep '^MVV_GATE_TOKEN=' .env | head -1 | cut -d= -f2-)"
  echo "    reusing the one already in .env"
elif [ "$DRY_RUN" = "1" ]; then
  TOKEN=""
  echo "    would generate one and save it to .env"
else
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  touch .env && chmod 600 .env
  # Replace rather than append, so running this twice does not leave two.
  if grep -q '^MVV_GATE_TOKEN=' .env 2>/dev/null; then
    grep -v '^MVV_GATE_TOKEN=' .env > .env.tmp && mv .env.tmp .env
  fi
  printf 'MVV_GATE_TOKEN=%s\n' "$TOKEN" >> .env
  chmod 600 .env
  echo "    generated and saved to .env (gitignored, mode 600)"
fi

# Through stdin, never as an argument: argv is world-readable via `ps` and
# lands in shell history. --set-from-stdin takes the KEY and reads the value.
if [ "$DRY_RUN" = "1" ]; then
  echo "    would run: railway variables --service $WEB --skip-deploys" \
       "--set-from-stdin MVV_GATE_TOKEN   (token piped in, never in argv)"
else
  printf '%s' "$TOKEN" | railway variables --service "$WEB" --skip-deploys \
    --set-from-stdin MVV_GATE_TOKEN >/dev/null
  echo "    set on '$WEB'"
fi
unset TOKEN

# -------------------------------------------------------------------------
# deploy
# -------------------------------------------------------------------------
say "deploying the worker"
run railway up --service "$WORKER" --detach

say "deploying the web console"
run railway up --service "$WEB" --detach

say "public domain"
run railway domain --service "$WEB" || true

cat <<EOF

Done. Two things to know while it comes up:

  * The web service usually boots before the worker has finished migrating,
    so the first page may say "The database is empty". That is the expected
    order, not a failure. Reload in a few seconds — it re-checks on its own.

  * Open /village on the domain above, click Sign in, and paste the token:

        grep MVV_GATE_TOKEN .env

The worker is already ticking and will keep ticking with nobody watching.
Capital still stops at the approval gate, so expect to find decisions waiting.

    railway logs --service $WORKER
    railway logs --service $WEB

EOF
