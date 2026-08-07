#!/usr/bin/env bash
# Drop everything and re-apply the migrations. Spec section 12.
#
# This destroys the experiment ledger and the cash ledger — the entire record
# of what was decided and what it cost. It asks first, every time.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

DB="${DATABASE_URL:-sqlite:///./data/mvv.db}"

echo "About to DESTROY all data in: $DB"
echo "That is every experiment, every approval, and the whole cash ledger."
if [ "${MVV_FORCE_RESET:-}" != "1" ]; then
  read -r -p "Type 'destroy' to continue: " answer
  [ "$answer" = "destroy" ] || { echo "aborted"; exit 1; }
fi

case "$DB" in
  postgres*|postgresql*)
    psql "$DB" -v ON_ERROR_STOP=1 -c \
      "DROP TABLE IF EXISTS experiment_events, human_approvals, cash_flow, orders, experiments CASCADE;"
    ;;
  sqlite*)
    path="${DB#sqlite://}"
    path="${path#/}"
    rm -f "$path"
    echo "removed $path"
    ;;
  *)
    echo "don't know how to reset $DB" >&2
    exit 1
    ;;
esac

python -m src.main init-db
echo "done. Run 'python -m src.main cash open' to seed the budget."
